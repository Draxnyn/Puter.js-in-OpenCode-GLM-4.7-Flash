[← Voltar ao índice](README.md)

# OpenCode Puter Bridge

O **OpenCode Puter Bridge** conecta o OpenCode ao Puter por uma API local compatível com OpenAI. Ele usa uma sessão Puter autenticada no navegador e está configurado para o **GLM-4.7 Flash**.

---

<img width="1292" height="635" alt="image" src="https://github.com/user-attachments/assets/f32d4234-4d42-41d6-b202-e53905984036" />


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

Clone o repositório e execute o instalador:

```bash
git clone https://github.com/Draxnyn/Puter.js-in-OpenCode-GLM-4.7-Flash.git
cd Puter.js-in-OpenCode-GLM-4.7-Flash
bash install.sh
source ~/.bashrc
```

O instalador baixa o OpenCode pelo instalador oficial caso ele ainda não esteja disponível. Depois instala a ponte em `~/.local/share/opencode-puter-bridge`, o wrapper em `~/.local/bin/opencode` e a configuração modelo quando ainda não existe uma configuração do OpenCode.

Se a porta `8765` já estiver ocupada, o instalador escolhe automaticamente a próxima porta livre e a aplica tanto à ponte quanto à configuração do OpenCode.

Inicie a versão Puter com `opencode`. Para abrir o OpenCode normal, sem a ponte, use `opencode -n`. No WSL, a página da ponte é aberta no navegador do Windows. Mantenha essa aba aberta enquanto usa o OpenCode.

---

## Configuração

| Variável | Padrão | Função |
|---|---:|---|
| `PUTER_MAX_CONCURRENT` | `2` | Máximo de chamadas simultâneas ao Puter. Faixa aceita: 1–8. |
| `PUTER_BRIDGE_PORT` | `8765` | Porta local da ponte. |
| `PUTER_BRIDGE_TIMEOUT` | `600` | Tempo de espera pela resposta do navegador, em segundos. |

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

## Nova tentativa de quota

Quando o Puter retorna erro de quota ou rate-limit, a ponte do navegador mantém a mesma requisição do OpenCode viva e tenta novamente após 5 segundos. A espera aumenta até 30 segundos entre tentativas. Isso vale tanto para o agente mestre quanto para cada subagente: uma quota temporária não volta ao OpenCode como falha final de uma ferramenta.

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
