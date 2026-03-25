# STS2 Local Agent

A local LLM agent that plays Slay the Spire 2 autonomously via the STS2MCP game mod's REST API.

## Architecture

```
Local LLM (KoboldCPP / Ollama)
    │ OpenAI-compatible API (localhost:5001)
    ▼
agent.py (Python, main loop)
    │ HTTP (localhost:15526)
    ▼
STS2MCP C# mod (inside game process)
    │ Game API
    ▼
Slay the Spire 2
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Main game loop — fetches state, calls LLM, executes tool calls |
| `config.py` | Model URL, timeouts, history limits |
| `prompts.py` | System prompts + state-specific addendums (combat, map, rewards, etc.) |
| `tools.py` | OpenAI-format tool definitions + state-based routing |
| `game_api.py` | HTTP wrapper for the STS2MCP REST API |
| `test_setup.py` | Connectivity tests (LLM + game API + tool calling) |
| `auto_restart.py` | Auto-restart wrapper for agent.py |
| `docs/` | Design documents for planned auxiliary tools |
| `logs/` | JSONL run logs (one file per run) |

## Prerequisites

- **STS2MCP mod** running in Slay the Spire 2 (REST API on `localhost:15526`)
- **KoboldCPP** or **Ollama** serving a model with tool-calling support
- Python 3.9+ with `httpx` and `openai` packages

## Usage

```bash
# 1. Start the game with STS2MCP mod
# 2. Start your LLM server (KoboldCPP/Ollama)
# 3. Run the agent
python agent.py                    # Uses model from config.py
python agent.py --model phi4:14b   # Override model
```

## Config

Edit `config.py` to change:
- `OLLAMA_BASE_URL` — LLM API endpoint (default: `http://localhost:5001/v1`)
- `ACTIVE_MODEL` — Model name (default: `koboldcpp`)
- `LLM_TEMPERATURE` — Sampling temperature (default: 0.3)
- `LLM_MAX_TOKENS` — Max output tokens (default: 1024)
- `MAX_HISTORY_TURNS` — Conversation history length (default: 5)

## Current Status

- Action success rate: ~88%
- Tested with: Qwen3.5-27B (Q4_K_M) on RTX 4090 via KoboldCPP
- Can complete Act 1 (beat boss), struggles with HP management across fights
- Key reliability features: energy guard, parser fallback, loop detection, smart-wait for player turn
