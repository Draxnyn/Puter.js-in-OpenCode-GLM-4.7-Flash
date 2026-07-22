#!/usr/bin/env python3
"""Local OpenAI-compatible bridge between OpenCode and browser-based Puter.js."""

import base64
import json
import logging
import mimetypes
import os
import re
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
    "prism-ml/ternary-bonsai-27b": "prism-ml/ternary-bonsai-27b",
    "cohere/north-mini-code:free": "cohere/north-mini-code:free",
    "z-ai/glm-4.6v-flash": "z-ai/glm-4.6v-flash",
}
VISION_MODEL = "z-ai/glm-4.6v-flash"
LOCAL_MEDIA_SUFFIXES = {".gif", ".jpeg", ".jpg", ".pdf", ".png", ".webp"}
MAX_LOCAL_MEDIA_BYTES = int(os.getenv("PUTER_MAX_LOCAL_MEDIA_BYTES", str(20 * 1024 * 1024)))
QUOTED_LOCAL_PATH = re.compile(r'''["'](/[^"']+)["']''')

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
    return MODEL_MAP.get(opencode_model)


def local_media_paths(messages: list[object]) -> list[Path]:
    """Find local visual files explicitly referenced by the OpenCode conversation."""
    candidates: list[str] = []

    def inspect(value: object) -> None:
        if isinstance(value, str):
            candidates.extend(QUOTED_LOCAL_PATH.findall(value))
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return
            inspect(parsed)
            return
        if isinstance(value, list):
            for item in value:
                inspect(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in {"filePath", "file_path", "path"} and isinstance(item, str):
                candidates.append(item)
            inspect(item)

    inspect(messages)
    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve(strict=True)
            size = resolved.stat().st_size
        except OSError:
            continue
        if (
            resolved in seen
            or resolved.suffix.lower() not in LOCAL_MEDIA_SUFFIXES
            or size > MAX_LOCAL_MEDIA_BYTES
        ):
            continue
        seen.add(resolved)
        found.append(resolved)
    return found


def attach_local_media(messages: list[object], puter_model: str) -> list[object]:
    """Attach files referenced through OpenCode tools to Puter's vision request."""
    if puter_model != VISION_MODEL:
        return messages
    paths = local_media_paths(messages)
    if not paths:
        return messages

    context: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content_value = message.get("content")
        if isinstance(content_value, str):
            context.append(content_value)
        elif isinstance(content_value, list):
            for block in content_value:
                if isinstance(block, dict) and block.get("type") == "text":
                    context.append(str(block.get("text", "")))
    content: list[dict[str, object]] = [{
        "type": "text",
        "text": (
            "Analyze the following local file(s) requested in the conversation.\n\n"
            "Relevant task context:\n" + "\n".join(item for item in context if item)[-24_000:]
        ),
    }]
    for path in paths:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        if mime.startswith("image/"):
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            content.append({
                "type": "local_file",
                "name": path.name,
                "mime": mime,
                "data_url": data_url,
            })
    # Puter's multimodal schema only accepts text and file blocks. A compact
    # standalone vision prompt avoids forwarding OpenCode's internal history
    # blocks (tool-call, tool-result, reasoning, etc.) to the vision model.
    return [{"role": "user", "content": content}]


def normalize_messages_for_puter(messages: list[object]) -> list[dict[str, object]]:
    """Keep OpenCode's richer content blocks within Puter's text/file schema."""
    normalized: list[dict[str, object]] = []
    for source in messages:
        if not isinstance(source, dict):
            normalized.append({"role": "user", "content": str(source)})
            continue
        role = str(source.get("role", "user"))
        if role not in {"system", "assistant", "user", "tool"}:
            role = "user"
        content = source.get("content", "")
        if not isinstance(content, list):
            message: dict[str, object] = {"role": role, "content": str(content or "")}
        else:
            blocks: list[dict[str, object]] = []
            for block in content:
                if not isinstance(block, dict):
                    blocks.append({"type": "text", "text": str(block)})
                    continue
                block_type = str(block.get("type", ""))
                if block_type == "text":
                    blocks.append({"type": "text", "text": str(block.get("text", ""))})
                elif block_type == "local_file":
                    blocks.append(block)
                elif block_type == "file":
                    puter_path = block.get("puter_path")
                    if isinstance(puter_path, str) and puter_path:
                        blocks.append({"type": "file", "puter_path": puter_path})
                    else:
                        data_url = str(block.get("data_url") or block.get("url") or "")
                        if data_url:
                            blocks.append({
                                "type": "local_file",
                                "name": str(block.get("name") or block.get("filename") or "attachment"),
                                "mime": str(block.get("mime") or "application/octet-stream"),
                                "data_url": data_url,
                            })
                        else:
                            blocks.append({
                                "type": "text",
                                "text": json.dumps(block, ensure_ascii=False),
                            })
                elif block_type == "image_url" and isinstance(block.get("image_url"), dict):
                    url = str(block["image_url"].get("url", ""))
                    if url:
                        blocks.append({
                            "type": "local_file",
                            "name": "image-attachment",
                            "mime": "image/*",
                            "data_url": url,
                        })
                else:
                    # OpenCode includes blocks such as tool-call and tool-result.
                    # Puter only accepts text and file blocks, so preserve their
                    # information as text instead of forwarding an invalid type.
                    blocks.append({
                        "type": "text",
                        "text": json.dumps(block, ensure_ascii=False),
                    })
            message = {"role": role, "content": blocks}
        if isinstance(source.get("tool_calls"), list):
            message["tool_calls"] = source["tool_calls"]
        if role == "tool" and source.get("tool_call_id") is not None:
            message["tool_call_id"] = str(source["tool_call_id"])
        normalized.append(message)
    return normalized


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
            supported = ", ".join(MODEL_MAP)
            raise ValueError(f"Unsupported model. Available models: {supported}.")

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages is required.")

        options: dict[str, object] = {"tools": body.get("tools", [])}
        if body.get("tool_choice") is not None:
            options["tool_choice"] = body["tool_choice"]
        prompt = normalize_messages_for_puter(attach_local_media(messages, puter_model))
        result = bridge.submit(prompt, puter_model, options)
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
