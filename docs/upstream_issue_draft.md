# GitHub Issue Draft — for Gennadiyev/STS2MCP

**Title:** Feature: Add run logging and smart state polling for combat turns

---

**Body:**

Hi! I've been using STS2MCP to build AI agents that play STS2 — both via Claude Code (MCP) and a local Qwen3.5-27B agent talking directly to the REST API. Great project, thanks for building it!

While experimenting, I ran into two pain points and implemented fixes in my fork. I'd like to contribute them back if you're interested.

### 1. Run logging (`run_logger.py` + `log_agent_decision` tool)

**Problem:** There's no way to review what happened during a run after it ends. When comparing different models or debugging bad plays, I had to rely on terminal output.

**Solution:** A lightweight JSONL logger (`mcp/run_logger.py`) that records every tool call and its result. Also adds a `log_agent_decision` MCP tool so the AI can log its reasoning before key decisions. Logs go to `logs/run_<timestamp>.jsonl`.

Example log entry:
```json
{"ts": "2026-03-24T19:30:00", "type": "tool_call", "action": "play_card", "args": {"card_index": 2, "target": "jaw_worm_0"}, "result_length": 1200, "result_preview": "..."}
```

### 2. Smart state polling (`_get_smart()`)

**Problem:** During combat, `get_game_state` returns the state even during the enemy's turn (`Play Phase: False`). The AI can't act on this state, so it wastes a tool call and tokens. With Claude, this burned through the session token limit quickly (112 state calls out of 254 total). With local models, each wasted call costs 10+ seconds of inference time.

**Solution:** A `_get_smart()` wrapper that polls for up to 8 seconds (1s intervals) until the state is actionable:
- `Play Phase: True` (player's turn)
- A non-combat state (map, rewards, event, etc.)
- Combat ended

`get_game_state` uses this by default. No behavior change for non-combat screens.

```python
async def _get_smart(params, wait_for_player_turn=True):
    text = await _get(params)
    if not wait_for_player_turn:
        return text
    if is_combat and "Play Phase: False" in text:
        for _ in range(8):
            await asyncio.sleep(1.0)
            text = await _get(params)
            if "Play Phase: True" in text or not in_combat:
                break
    return text
```

### Scope

Both changes are additive — no modifications to existing tool signatures or the C# mod. Happy to open a PR if you'd like these upstream. Also open to feedback on the approach.

My fork: https://github.com/[YOUR_USERNAME]/STS2MCP
