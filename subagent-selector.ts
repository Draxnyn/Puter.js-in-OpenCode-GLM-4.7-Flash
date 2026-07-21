import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"

const choices = [
  {
    title: "GLM 4.7 Flash",
    value: "glm-4.7-flash",
    description: "Default — fast agentic coding and tools",
  },
  {
    title: "NVIDIA Nemotron Nano 9B V2",
    value: "nemotron-nano-9b-v2-free",
    description: "Free — coding and configurable reasoning",
  },
  {
    title: "Baidu Qianfan CoBuddy",
    value: "cobuddy-free",
    description: "Free — programming, agents and tools",
  },
]

export const tui = async (api) => {
  const modelFile = process.env.PUTER_SUBAGENT_MODEL_FILE
  if (!modelFile || !api.command) return

  const current = () => {
    try {
      const value = readFileSync(modelFile, "utf8").trim()
      return choices.some((choice) => choice.value === value) ? value : choices[0].value
    } catch {
      return choices[0].value
    }
  }

  api.command.register(() => [
    {
      title: "Change the subagent model",
      value: "puter.subagent.model",
      description: "Change the subagent model",
      category: "Model",
      slash: { name: "subagent" },
      onSelect(dialog) {
        const stack = dialog ?? api.ui.dialog
        stack.replace(() =>
          api.ui.DialogSelect({
            title: "Select subagent model",
            options: choices,
            current: current(),
            onSelect(option) {
              mkdirSync(dirname(modelFile), { recursive: true })
              writeFileSync(modelFile, option.value + "\n", "utf8")
              api.ui.toast({
                variant: "success",
                message: `Subagent model: ${option.title}`,
              })
              stack.clear()
            },
          }),
        )
      },
    },
  ])
}
