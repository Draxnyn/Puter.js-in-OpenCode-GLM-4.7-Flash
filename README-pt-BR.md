[← Voltar ao índice](README.md)

# OpenCode Puter Bridge

O **OpenCode Puter Bridge** conecta o OpenCode ao Puter por uma API local compatível com OpenAI. Ele usa uma sessão Puter autenticada no navegador e está configurado para o **GLM-4.7 Flash**.

---

## Ideia central

O OpenCode já aceita provedores compatíveis com OpenAI. Este projeto expõe um endpoint local que converte requisições do OpenCode em chamadas `puter.ai.chat()` e converte a resposta de volta para o formato chat-completions da OpenAI.

```text
OpenCode → ponte local → navegador com sessão Puter → GLM-4.7 Flash
```

A ponte fica em `127.0.0.1`; nenhum token da sessão Puter é enviado para um servidor externo.

---

## O que ele fornece

- Um único modelo configurado: `puter/glm-4.7-flash`.
- Endpoint `/v1/chat/completions` compatível com OpenAI.
- Tradução de tool calls para ações agenciais do OpenCode.
- Até sete subagentes GLM disponíveis para o agente mestre.
- Concorrência configurável no navegador, com duas requisições Puter simultâneas por padrão.
- Repasse de uso de tokens quando o Puter inclui esses dados na resposta.

---

## Requisitos

1. Python 3.10 ou superior.
2. [OpenCode](https://opencode.ai/).
3. Uma conta Puter autenticada no navegador aberto pelo inicializador.

Nenhum pacote Python precisa ser instalado.

---

## Instalação

1. Copie ou mescle [`templates/opencode.jsonc`](./templates/opencode.jsonc) na configuração do OpenCode:

   - Linux/macOS: `~/.config/opencode/opencode.jsonc`
   - Windows: use a pasta de configuração do OpenCode.

   O template expõe intencionalmente apenas o modelo GLM do Puter.

2. Torne o inicializador executável:

   ```bash
   chmod +x run_opencode_puter.sh
   ```

3. Inicie o OpenCode pela ponte:

   ```bash
   ./run_opencode_puter.sh
   ```

4. Mantenha a aba **Puter → OpenCode Bridge** aberta enquanto usa o OpenCode.

---

## Configuração

| Variável | Padrão | Função |
|---|---:|---|
| `PUTER_MAX_CONCURRENT` | `2` | Máximo de chamadas simultâneas ao Puter. Faixa aceita: 1–8. |
| `PUTER_BRIDGE_PORT` | `8765` | Porta local da ponte. |
| `PUTER_BRIDGE_TIMEOUT` | `180` | Tempo de espera pela resposta do navegador, em segundos. |

Exemplo:

```bash
PUTER_MAX_CONCURRENT=2 ./run_opencode_puter.sh
```

---

## Subagentes

O template cria `puter-worker-1` até `puter-worker-7`. O agente `build` só pode delegar para esses trabalhadores; os trabalhadores não podem criar novos trabalhadores. Todos usam o mesmo modelo GLM.

O próprio OpenCode controla o agendamento das tarefas. A configuração limita as identidades de trabalhadores disponíveis a sete.

---

## Uso de tokens

A ponte repassa `prompt_tokens`, `completion_tokens` e `total_tokens` quando o Puter inclui esses campos nos metadados da resposta. Alguns provedores Puter podem não retornar uso por chamada; nesse caso, o OpenCode não consegue mostrar contagens ou custo exatos.

---

## Logs

O inicializador grava os logs da ponte em:

```text
$XDG_STATE_HOME/opencode/puter-bridge.log
```

Quando `XDG_STATE_HOME` não está definido, usa `~/.local/state/opencode/puter-bridge.log`.

---

## Segurança

- A ponte escuta somente em `127.0.0.1`.
- O inicializador gera um token local aleatório.
- Não publique uma porta de ponte ativa nem sua URL com token.
- Não inclua arquivos `.env`, logs ou dados de navegador no repositório.
