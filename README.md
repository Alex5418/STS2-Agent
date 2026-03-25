# STS2 Local Agent

A local LLM agent that plays **Slay the Spire 2** autonomously using open-source models.

Built on top of [STS2MCP](https://github.com/Gennadiyev/STS2MCP) by [@Gennadiyev](https://github.com/Gennadiyev), which provides the C# BepInEx mod that exposes the game as a REST API.

## How It Works

```
Local LLM (Qwen3.5-27B / Phi-4 / etc.)
    │ OpenAI-compatible API
    ▼
agent.py ── main loop: observe → think → act
    │ HTTP requests
    ▼
STS2MCP mod (inside game process, localhost:15526)
    │
    ▼
Slay the Spire 2
```

The agent runs a simple loop:
1. **Observe** — fetch game state (hand, enemies, intents, HP, energy)
2. **Think** — send state to a local LLM with state-specific tools and prompts
3. **Act** — parse the LLM's tool call and execute it via the game's REST API
4. Repeat until the run ends

Only 1–3 tools are exposed per game state (combat, map, rewards, etc.) to keep small models focused.

## Current Results

| Metric | Value |
|--------|-------|
| Action success rate | ~88% |
| Best result | Beat Act 1 boss (Ironclad) |
| Model | Qwen3.5-27B (Q4_K_M) |
| Hardware | RTX 4090, KoboldCPP |
| Speed | ~10 sec/action |

### Reliability Features

- **Energy guard** — client-side tracking prevents playing cards you can't afford
- **Parser fallback** — recovers tool calls from text when structured parsing fails (common with local models)
- **Loop detection** — breaks out of stuck states (e.g., reward claim loops)
- **Smart-wait** — polls during enemy turns instead of wasting LLM calls
- **Single-tool mode** — executes one action per LLM response to avoid index-shift bugs

## Prerequisites

- [STS2MCP mod](https://github.com/Gennadiyev/STS2MCP) installed and running in Slay the Spire 2
- An OpenAI-compatible LLM server ([KoboldCPP](https://github.com/LostRuins/koboldcpp), [Ollama](https://ollama.com), etc.)
- Python 3.9+

## Setup

```bash
# Install dependencies
pip install httpx openai

# Verify connectivity (game must be running with mod)
python test_setup.py

# Run the agent
python agent.py
```

## Configuration

Edit `config.py`:

```python
OLLAMA_BASE_URL = "http://localhost:5001/v1"  # LLM API endpoint
ACTIVE_MODEL = "koboldcpp"                     # Model name
LLM_TEMPERATURE = 0.3                          # Lower = more deterministic
LLM_MAX_TOKENS = 1024                          # Output limit (tool calls are short)
MAX_HISTORY_TURNS = 5                           # Conversation history length
```

## Project Structure

```
├── agent.py          # Main game loop
├── config.py         # Model and game server settings
├── prompts.py        # System prompts (combat, map, rewards, etc.)
├── tools.py          # Tool definitions + state-based routing
├── game_api.py       # HTTP wrapper for STS2MCP REST API
├── test_setup.py     # Connectivity tests
├── logs/             # JSONL run logs (one per run)
└── docs/             # Design docs for planned features
```

## Known Limitations

- **HP management** — the agent plays too aggressively and bleeds HP across fights
- **No map planning** — chooses nodes one at a time, can't plan routes to avoid elites
- **Limited reasoning** — Qwen3.5-27B produces minimal strategic thinking compared to larger models
- **State transition gaps** — occasionally confused when combat ends but state hasn't updated yet

## Acknowledgments

- [@Gennadiyev](https://github.com/Gennadiyev) for [STS2MCP](https://github.com/Gennadiyev/STS2MCP) — the mod and API that makes this possible
- [Mega Crit](https://www.megacrit.com/) for Slay the Spire 2
