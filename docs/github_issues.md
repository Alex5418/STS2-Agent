# GitHub Issues — copy to https://github.com/Alex5418/STS2-Agent/issues

---

## Issue 1

**Title:** Agent bleeds HP across fights — no defensive play awareness

**Labels:** `gameplay`, `priority:high`

**Body:**

### Problem
The agent plays almost exclusively offensive cards and rarely uses Defend, even when enemies signal high-damage attacks. In a typical Act 1 run, HP drops steadily: 80 → 75 → 66 → 59 → 49 → 34 → 30 → 26, requiring rest sites just to survive.

### Evidence
From `run_koboldcpp_20260324_204818.jsonl`: 9 combats, HP never recovered except at rest sites. In the boss fight (Ceremonial Beast, 30 turns), defensive plays were minimal despite high incoming damage.

One exception where the model DID reason about defense (Bygone Effigy elite):
> "The enemy intends to attack for 25 damage. I have 21 HP and 0 Block. I cannot survive this hit without blocking."

This shows the model CAN reason about defense when survival is at stake, but doesn't do it proactively at higher HP.

### Possible approaches
1. **Prompt-level:** Add explicit defense threshold rules to COMBAT_ADDENDUM (e.g., "If enemy intent is Attack >= 10, play at least one Defend before attacking")
2. **Code-level:** Parse enemy intent from state JSON, inject a hint like "WARNING: incoming 15 damage, consider blocking" into the prompt
3. **Hybrid:** Both — prompt rules for general behavior, code hints for critical situations

### Considerations
- Prompt changes are cheap but Qwen3.5-27B doesn't always follow instructions
- Code-level hints add minimal tokens but require parsing enemy intent data
- Over-blocking is also bad — the agent needs to balance offense and defense, not just always block

---

## Issue 2

**Title:** No multi-step map path planning — agent walks into elites blindly

**Labels:** `gameplay`, `priority:medium`

**Body:**

### Problem
The agent chooses map nodes one at a time without looking ahead. It frequently walks into Elite fights in Act 1 when the deck isn't strong enough, because by the time it sees the Elite node, there's no alternative path left.

### Example
In `run_koboldcpp_20260324_185000.jsonl`, the agent chose a path leading to Phrog Parasite (Elite) on floor 7 and died. It could have taken a different route at floor 5 to avoid the elite entirely.

### Desired behavior
At the start of each act (or when entering the map), analyze all paths from current position to the boss. Score paths by:
- Number of Elite nodes (avoid in Act 1)
- Number of Rest Sites (prefer)
- Number of Shops (prefer if rich)
- Overall safety

Return a recommended sequence of node indices.

### Implementation idea
Add an `analyze_map_paths` helper that reads the map JSON, enumerates paths via DFS/BFS, scores them, and returns a recommendation. This is pure computation — no LLM call needed. Register as an extra tool for `state_type="map"`.

### Considerations
- Need to understand the map JSON structure (nodes, connections, types)
- Should be a local computation tool, not an LLM task
- Only useful if the map data in JSON actually contains the full graph (needs verification)

---

## Issue 3

**Title:** State transition confusion — agent calls combat tools after combat ends

**Labels:** `bug`, `priority:low`

**Body:**

### Problem
When the last card kills an enemy, the agent's next action is still in the "monster"/"elite"/"boss" state because the game hasn't transitioned yet. The agent tries `end_turn` and gets "Not in combat" error.

This happens at the end of almost every combat (7/9 in the latest run).

### Current mitigation
The "Not in combat" error is caught and treated as benign (not counted as a real error, doesn't increment error_count). This works — the agent moves on cleanly.

### Root cause
The agent fetches state immediately after the killing blow, but the game's state transition hasn't completed yet. The state still reports `state_type: "monster"` so the agent gets combat tools, but the combat is already over server-side.

### Possible fix
After a successful `play_card`, if the result message suggests the enemy died (or result contains certain keywords), skip the next LLM call and re-fetch state after a short delay. Low priority since the current mitigation works.

---

## Issue 4

**Title:** BlockedByHook / BlockedByCardLogic errors — agent can't adapt to unplayable cards

**Labels:** `gameplay`, `priority:medium`

**Body:**

### Problem
Some cards have conditional play restrictions (e.g., Pact's End, cards blocked by enemy hooks like Entangle). The agent tries to play them, gets "BlockedByCardLogic" or "BlockedByHook", and doesn't adapt — it may retry the same card.

### Evidence
- `run_koboldcpp_20260324_185000.jsonl`: Pact's End "BlockedByCardLogic" x2
- `run_koboldcpp_20260324_204818.jsonl`: "BlockedByHook" x3 in boss fight (same card retried)

### Possible approaches
1. **Use `can_play` field from state JSON:** The C# mod calls `card.CanPlay()` and includes the result. If the state already marks unplayable cards, add this info to the markdown output or filter them client-side before sending to LLM.
2. **Error memory:** After a BlockedByCardLogic error, add a note to the prompt: "Card X cannot be played this turn." Prevents retry.
3. **Client-side filter:** Before calling the LLM, remove unplayable cards from the hand description entirely.

### Investigation needed
Check if the state JSON already contains a `can_play` or `playable` field per card in the hand.

---

## Issue 5

**Title:** Post-boss overlay screen not handled — agent stalls after boss kill/death

**Labels:** `bug`, `priority:medium`

**Body:**

### Problem
After defeating a boss (or dying), the game shows an overlay screen (boss relic selection, act transition, or death screen). The agent's `TOOLS_BY_STATE` has no entry for `state_type: "overlay"`, so it falls back to `[PROCEED]`, but proceed may not be available on these screens.

### Evidence
- `run_koboldcpp_20260324_204818.jsonl`: After boss fight, `proceed` returned "No proceed button available or enabled" x2, then agent stalled.
- `run_koboldcpp_20260324_185000.jsonl`: Same pattern after death.

### Fix
Add overlay handling to `TOOLS_BY_STATE`:
```python
"overlay": [PROCEED, SELECT_RELIC, SKIP_RELIC, CONFIRM_SELECTION],
```
This gives the agent enough tools to handle boss relic selection, death screen, and other overlays.

---

## Issue 6

**Title:** combat_summary hp_end always shows "?" — post-combat HP not captured

**Labels:** `bug`, `priority:low`

**Body:**

### Problem
Combat summary logs show `hp_end: "?"` for every fight. The `hp_start` was fixed (now correctly reads from `battle.player.hp`), but `hp_end` fails because after combat ends, the state transitions away from combat and `battle.player.hp` is no longer available.

### Fix
Capture HP from the new state's structure. After combat, the state might be `combat_rewards` or `map` — need to find where player HP lives in those states. Alternatively, capture HP from the last combat state before the transition.
