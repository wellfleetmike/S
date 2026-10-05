# Sovereign Harness

Minimal, model-agnostic terminal coding agent.

Works with any local inference server (Ollama, llama.cpp, LM Studio, vLLM, etc.) or remote providers through the built-in OpenAI-compatible shim.

## Goals

- Zero Claude, Anthropic, or "nano-claude" branding
- Runs against any model
- Small, auditable, strict ASCII codebase
- Full tool calling, memory, and skills support

## Quick Start

```bash
cd sovereign-harness
python sovereign_harness.py
```

One-shot mode:

```bash
python sovereign_harness.py "Refactor the config loader to support environment overrides"
```

## Model Selection (in priority order)

1. `--model` command-line flag
2. `SOVEREIGN_MODEL` environment variable
3. `FROM` line parsed from a Modelfile (in cwd, ~/.sovereign/Modelfile or via SOVEREIGN_MODELFILE env)
4. Fallback: `qwen2.5-coder-7b-instruct`

Examples:

```bash
export SOVEREIGN_MODEL=ollama/qwen2.5-coder-7b-instruct
python sovereign_harness.py -m ollama/codellama
```

## Connecting to Local Models

The harness talks to models through the `openai.py` shim. Common configurations:

- **Ollama**: `ollama serve` then use model names like `ollama/qwen2.5-coder-7b-instruct`
- **llama.cpp server**: `custom/http://localhost:8080/v1`
- **LM Studio**: similar `custom` base URL

See `providers.py` for supported prefixes.

## Key Features

- Read / Write / Edit / Bash / Grep / Glob tools
- Persistent memory (user + project scopes)
- Reusable skills
- Sub-agents (multi-agent support is optional)
- Streaming responses and extended thinking
- Permission system with ask/accept-all modes

## Configuration

All persistent state lives under:

```
~/.sovereign/
    config.json
    sessions/
    input_history.txt
```

## Requirements

For local use only:

```
httpx
rich
```

Remote providers (Anthropic, OpenAI, etc.) are optional and can be installed separately.

## Launch Script

Preferred way to start (handles venv activation if present, sets PYTHONPATH to sovereign-harness dir, resolves model using config, ensures ~/.sovereign/ dirs):

```bash
cd sovereign-harness
./sovereign-launch.sh --help
./sovereign-launch.sh "Refactor the config loader to support environment overrides"
```

Env vars:
- SOVEREIGN_MODEL=ollama/qwen2.5-coder-7b-instruct   (model override)
- SOVEREIGN_MODELFILE=/path/to/Modelfile             (for auto FROM parse)

All state under consistent config dir:
```
~/.sovereign/
    config.json
    sessions/
    memory/
    input_history.txt
```

The launch script and sovereign_harness.py support full CLI: --model, --verbose, --version, --print etc.

## Philosophy

- The harness itself is model-neutral.
- All model-specific behavior is isolated in providers and the shim.
- No hidden Unicode, no telemetry, minimal attack surface.

