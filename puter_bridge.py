#!/usr/bin/env python3
"""Local OpenAI-compatible bridge between OpenCode and browser-based Puter.js."""

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = int(os.getenv("PUTER_BRIDGE_PORT", "8765"))
# The local bridge remains token-protected. OpenCode uses the same token via
# PUTER_BRIDGE_TOKEN, without storing secrets in its configuration.
TOKEN = os.environ["PUTER_BRIDGE_TOKEN"]
# A quota/rate-limit response is retried by the browser client. Keep the
# OpenCode request alive long enough for those retries instead of ending an
# agent or subagent on the first transient response.
REQUEST_TIMEOUT = int(os.getenv("PUTER_BRIDGE_TIMEOUT", "600"))
PAGE = Path(__file__).with_name("puter_bridge.html")
MODEL_MAP = {
    "glm-4.7-flash": "z-ai:z-ai/glm-4.7-flash",
    "nemotron-nano-9b-v2-free": "nvidia/nemotron-nano-9b-v2:free",
    "cobuddy-free": "baidu/cobuddy:free",
}
SUBAGENT_MODEL_FILE = Path(
    os.getenv(
        "PUTER_SUBAGENT_MODEL_FILE",
        str(Path(__file__).with_name("subagent-model")),
    )
)
DEFAULT_SUBAGENT_MODEL = "glm-4.7-flash"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


class Bridge:
    def __init__(self) -> None:
        self.queue: deque[dict[str, object]] = deque()
        self.pending: dict[str, dict[str, object]] = {}
        self.condition = threading.Condition()
        self.last_browser_seen = 0.0

    def submit(
        self,
        prompt: str | list[dict[str, object]],
        model: str,
        options: dict[str, object] | None = None,
    ) -> object:
        request_id = uuid.uuid4().hex
        completed = threading.Event()
        request: dict[str, object] = {"done": completed, "answer": None, "error": None}

        with self.condition:
            self.pending[request_id] = request
            self.queue.append(
                {
                    "id": request_id,
                    "prompt": prompt,
                    "model": model,
                    "options": options or {},
                }
            )
            self.condition.notify_all()

        if not completed.wait(REQUEST_TIMEOUT):
            with self.condition:
                self.pending.pop(request_id, None)
            raise TimeoutError("The browser-based Puter client did not respond in time.")

        if request["error"]:
            raise RuntimeError(str(request["error"]))
        return request["answer"] or ""

    def next_request(self) -> dict[str, object] | None:
        deadline = time.monotonic() + 25
        with self.condition:
            self.last_browser_seen = time.time()
            while not self.queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            return self.queue.popleft()

    def finish(self, request_id: str, answer: object | None, error: str | None) -> bool:
        with self.condition:
            request = self.pending.pop(request_id, None)
        if request is None:
            return False
        request["answer"] = answer
        request["error"] = error
        request["done"].set()  # type: ignore[union-attr]
        return True


bridge = Bridge()


def resolve_puter_model(opencode_model: str) -> str | None:
    if opencode_model != "subagent":
        return MODEL_MAP.get(opencode_model)
    try:
        selected = SUBAGENT_MODEL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        selected = DEFAULT_SUBAGENT_MODEL
    return MODEL_MAP.get(selected, MODEL_MAP[DEFAULT_SUBAGENT_MODEL])


def content_from_result(result: object) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)

    message = result.get("message", result)
    if not isinstance(message, dict):
        return str(message)
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict)
        )
    return str(content)


def message_from_result(result: object) -> dict[str, object]:
    if isinstance(result, dict) and isinstance(result.get("message"), dict):
        source = result["message"]
        message: dict[str, object] = {
            "role": str(source.get("role", "assistant")),
            "content": content_from_result(result) or None,
        }
        if isinstance(source.get("tool_calls"), list):
            message["tool_calls"] = source["tool_calls"]
        return message
    return {"role": "assistant", "content": content_from_result(result) or None}


def streaming_tool_calls(tool_calls: object) -> list[dict[str, object]]:
    """Convert Puter tool calls into OpenAI SSE deltas."""
    if not isinstance(tool_calls, list):
        return []

    normalized: list[dict[str, object]] = []
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        normalized.append(
            {
                "index": index,
                "id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": str(function.get("name", "")),
                    "arguments": arguments,
                },
            }
        )
    return normalized


def usage_from_result(result: object) -> dict[str, int] | None:
    """Translate Puter usage fields into the OpenAI format."""
    if not isinstance(result, dict):
        return None

    message = result.get("message")
    candidates = [
        result.get("usage"),
        result.get("usage_metadata"),
        result.get("metadata", {}).get("usage") if isinstance(result.get("metadata"), dict) else None,
        message.get("usage") if isinstance(message, dict) else None,
    ]
    for usage in candidates:
        if not isinstance(usage, dict):
            continue

        def number(*names: str) -> int:
            for name in names:
                value = usage.get(name)
                if isinstance(value, (int, float)):
                    return int(value)
            return 0

        prompt_tokens = number("prompt_tokens", "input_tokens", "inputTokens")
        completion_tokens = number("completion_tokens", "output_tokens", "outputTokens")
        total_tokens = number("total_tokens", "totalTokens")
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        if prompt_tokens or completion_tokens or total_tokens:
            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        query = parse_qs(urlparse(self.path).query)
        supplied = header.removeprefix("Bearer ") or query.get("token", [""])[0]
        return supplied == TOKEN

    def send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The page can close during long polling; this is not a bridge error.
            logger.debug("Client disconnected before receiving its response.")

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length))

    def send_sse(self, payload: object) -> None:
        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def complete_opencode_request(
        self, body: dict[str, object]
    ) -> tuple[dict[str, object], str, dict[str, int] | None, str]:
        opencode_model = str(body.get("model", ""))
        puter_model = resolve_puter_model(opencode_model)
        if puter_model is None:
            supported = ", ".join([*MODEL_MAP, "subagent"])
            raise ValueError(f"Unsupported model. Available models: {supported}.")

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages is required.")

        options: dict[str, object] = {"tools": body.get("tools", [])}
        if body.get("tool_choice") is not None:
            options["tool_choice"] = body["tool_choice"]
        result = bridge.submit(messages, puter_model, options)
        return message_from_result(result), uuid.uuid4().hex, usage_from_result(result), opencode_model

    def send_openai_completion(self, body: dict[str, object]) -> None:
        message, completion_id, usage, opencode_model = self.complete_opencode_request(body)
        created = int(time.time())
        tool_calls = message.get("tool_calls")
        finish_reason = "tool_calls" if tool_calls else "stop"

        if not body.get("stream"):
            response: dict[str, object] = {
                "id": f"chatcmpl-{completion_id}",
                "object": "chat.completion",
                "created": created,
                "model": opencode_model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
            }
            if usage:
                response["usage"] = usage
            self.send_json(
                HTTPStatus.OK,
                response,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        chunk_base = {
            "id": f"chatcmpl-{completion_id}",
            "object": "chat.completion.chunk",
            "created": created,
            "model": opencode_model,
        }
        self.send_sse({
            **chunk_base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        })
        if message.get("content"):
            self.send_sse({
                **chunk_base,
                "choices": [{"index": 0, "delta": {"content": message["content"]}, "finish_reason": None}],
            })
        if tool_calls:
            self.send_sse({
                **chunk_base,
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": streaming_tool_calls(tool_calls)},
                    "finish_reason": None,
                }],
            })
        self.send_sse({**chunk_base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]})
        if usage:
            self.send_sse({**chunk_base, "choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if path == "/next":
            request = bridge.next_request()
            self.send_json(HTTPStatus.OK, {"request": request})
            return
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"browser_connected": time.time() - bridge.last_browser_seen < 35})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if path == "/v1/chat/completions":
            try:
                self.send_openai_completion(self.read_json())
            except (ValueError, KeyError, json.JSONDecodeError) as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except TimeoutError as error:
                self.send_json(HTTPStatus.GATEWAY_TIMEOUT, {"error": str(error)})
            except BrokenPipeError:
                logger.info("OpenCode client closed the connection.")
            except Exception as error:
                logger.exception("OpenCode request failed")
                self.send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
            return
        if path != "/answer":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        try:
            body = self.read_json()
            request_id = str(body["id"])
            answer = body.get("result", body.get("answer"))
            error = body.get("error")
            found = bridge.finish(request_id, answer, str(error) if error else None)
            self.send_json(HTTPStatus.OK, {"ok": found})
        except (ValueError, KeyError, json.JSONDecodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid request body"})


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logger.info("Puter bridge listening on http://%s:%s", HOST, PORT)
    server.serve_forever()
