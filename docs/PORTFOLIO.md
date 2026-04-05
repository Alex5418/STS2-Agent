# STS2-Agent: Engineering an AI Agent for a Complex Strategy Game

> A technical write-up for portfolio / interview discussions.
> Covers design decisions, problems encountered, solutions applied, and lessons learned.

---

## What I Built

An autonomous AI agent that plays [Slay the Spire 2](https://store.steampowered.com/app/2868840/Slay_the_Spire_2/) — a roguelike deckbuilder where you make hundreds of sequential decisions across combat, deck construction, resource management, and map navigation. The agent runs entirely on local hardware (no cloud API), using open-weight LLMs (Qwen3.5-27B, Gemma4-26B) as the decision engine.

The system has three layers:

```
Local LLM (Ollama / KoboldCPP)
    │  OpenAI-compatible API
    ▼
Python Agent (agent.py, ~850 lines)
    │  REST API
    ▼
C# Game Mod (STS2MCP, BepInEx)  →  Slay the Spire 2
```

The agent observes game state, reasons about strategy, and executes actions in real-time — playing cards, navigating the map, choosing rewards, shopping, and resting — all without human intervention.

---

## Why This Project Is Interesting

Slay the Spire is a hard problem for AI agents because it requires:

- **Multi-turn planning** under partial information (draw pile is shuffled)
- **Arithmetic reasoning** with stacking modifiers (Strength + Vulnerable + Weak multipliers)
- **Long-horizon strategy** — card choices on floor 3 affect boss fights on floor 17
- **State machine navigation** — the agent must handle 13+ distinct game screens
- **Error recovery** — the game doesn't wait for you; actions can fail, states can change mid-turn

Unlike board games (Chess, Go) where MCTS excels, STS has a branching factor that makes tree search impractical. Unlike Atari games where RL works, STS requires symbolic reasoning about card text and status effects. This puts it squarely in the "LLM agent" sweet spot — but with real constraints on latency, reliability, and reasoning quality.

---

## Problems I Encountered and How I Solved Them

### Problem 1: The LLM Can't Do Math

**Situation**: The agent needed to calculate damage like `(6 + 2 Strength) × 1.5 Vulnerable × 0.75 Weak = 9`. Local 27B models got this wrong ~40% of the time, leading to bad combat decisions — the agent would fail to recognize lethal opportunities or underestimate incoming damage.

**Solution**: I moved all arithmetic out of the LLM. The agent pre-computes a "COMBAT MATH" block by parsing each card's `description` field from the game state (which already reflects Strength, Dexterity, Weak, Frail, and relic bonuses as computed by the game engine), then applies Vulnerable (the only enemy-side modifier not in card descriptions) on top. The LLM receives ready-to-use numbers:

```
COMBAT MATH (pre-computed, trust these numbers):
- Total incoming damage this turn: 14
- Hand cards (effective values):
  [0] Defend (cost 1): 5block
  [1] Strike (cost 1): 9dmg
  [2] Bash (cost 2): 12dmg
- Kill thresholds:
  Jaw Worm: 18HP + 0block = 18 to kill (VULNERABLE: only need 12 raw damage)
```

**Key design choice**: I parse from the game's own card descriptions rather than maintaining a separate card database. This means new cards, upgrades, and relic interactions are automatically handled — no hardcoded values, no data synchronization issues.

**Takeaway**: LLMs are bad at arithmetic but great at comparison and planning. Pre-compute the math, let the LLM decide strategy.

---

### Problem 2: Model-Specific Failure Modes

**Situation**: When I switched from Qwen3.5-27B (via KoboldCPP) to Gemma4-26B (via Ollama), the agent broke in completely new ways — even though both models "support tool calling."

Gemma4 had three distinct failure modes:

| Failure | Root Cause | Symptom |
|---------|-----------|---------|
| "Wait, I'll play Strike" repeated 80× | No repetition penalty by default | 35-second turns producing no action |
| Reasoning text filled entire output, tool call truncated | Reasoning and tool call compete for tokens | `finish_reason=length`, no tool call parsed |
| Empty responses after auto_end_turn | `repeat_penalty` too aggressive (1.3) | Model "afraid" to output tokens it had used before |

**Solutions**:

1. **Repetition**: Added `repeat_penalty=1.15` via Ollama's `extra_body` API parameter. 1.3 was too aggressive (caused empty outputs); 1.15 suppresses loops without silencing the model.

2. **Token competition**: Moved reasoning INTO the tool call as a `reasoning` parameter, so the model outputs `play_card(reasoning="Enemy attacks 14, need block first", card_index=0)` in a single structured output. No more text-before-tool-call pattern.

3. **Empty responses**: Replaced the "nudge" retry mechanism (which appended empty assistant messages to history, confusing the model further) with a clean context retry — wipe history and let the model start fresh.

**Takeaway**: "Supports tool calling" means very different things across models. Production agent systems need model-specific adaptation layers, not one-size-fits-all prompts.

---

### Problem 3: Wasted LLM Calls

**Situation**: The agent called the LLM for every single card play. A typical 3-card turn required 3 LLM calls (5-15 seconds each). But many of these calls were trivial — the turn was over, no cards were playable, or there was only one obvious choice.

**Solution**: A layered approach to minimize unnecessary LLM invocations:

1. **Auto end_turn**: Before calling the LLM, check if any card in hand has `cost <= remaining_energy`. If not, skip the LLM and end the turn directly. This alone eliminated ~30% of combat LLM calls.

2. **Client-side energy tracking**: Track energy spent across card plays within a turn. Block obviously-unaffordable card plays before they hit the API, and auto-end when energy is exhausted.

3. **State-aware tool routing**: Only expose relevant tools per game state (combat tools in combat, shop tools in shop). This reduces the LLM's decision space and prevents invalid cross-state calls.

**Result**: Average LLM calls per combat dropped from ~20 to ~12 with no loss in decision quality.

**Takeaway**: The cheapest LLM call is the one you don't make. Deterministic checks should always gate non-deterministic reasoning.

---

### Problem 4: The Toggle Trap

**Situation**: When the game asked the agent to "select 2 cards to remove," the `select_card` API is a toggle — selecting an already-selected card *deselects* it. The agent would do:

```
select_card(0)  → Strike selected    (1 selected)
select_card(0)  → Strike deselected  (0 selected)
select_card(0)  → Strike selected    (1 selected)
select_card(0)  → Strike deselected  (0 selected)
... infinite loop
```

**Solution**: Two layers of defense:

1. **Tool description**: Explicitly documented the toggle behavior and warned against repeated indices.
2. **Agent-side guard**: Track `selected_card_indices`. If the model tries to select an already-selected index, automatically redirect to the next unselected card. The loop becomes impossible regardless of model behavior.

**Takeaway**: Don't trust the LLM to follow instructions about stateful interactions. If a failure mode can happen, add a programmatic guard — it's cheaper than debugging prompt engineering.

---

### Problem 5: Turn vs. Action Confusion

**Situation**: The agent treated each card play as a "new turn." After playing Defend (gaining 5 block), it would say "I already have 5 block from the *previous turn's* action" — but it was the same turn. This led to double-blocking (wasting energy on unnecessary Defend cards) and incorrect damage calculations.

**Root cause**: The prompts used "turn" ambiguously. The one-card-per-response architecture made the model think each response was a new turn.

**Solution**: Defined explicit terminology in the system prompt:
- **Turn** = one full round (get energy → play cards → end_turn)
- **Action** = playing one card (multiple actions per turn)

Added multi-step combat examples showing Block persisting across actions within the same turn.

**Takeaway**: When your architecture introduces abstraction layers (one LLM call ≠ one game turn), you must explicitly teach the model about the mapping. Implicit understanding doesn't transfer.

---

### Problem 6: Event State Machines

**Situation**: Game events are multi-step — you choose an option, then the event shows a result screen with a "Proceed" option. The agent would choose the option correctly, then call `proceed()` (which is a different API endpoint), fail because there's no proceed button — the "Proceed" is actually `choose_event_option(0)`.

This caused 3-5 wasted steps per event interaction.

**Solution**: Improved the event prompt with explicit instructions about multi-step events. Still an open issue — a more robust fix would be agent-side detection of "No proceed button" errors followed by automatic fallback to `choose_event_option(0)`.

**Takeaway**: Game UI patterns that are obvious to humans (a button labeled "Proceed" is an event option, not a navigation action) are invisible to LLMs without explicit mapping.

---

## Architecture Decisions Worth Discussing

### Why Local LLMs Instead of Cloud APIs?

1. **Latency**: Even at 5-15s per call locally, cloud API round-trips would add network latency and rate-limit concerns for an agent making 100+ calls per game.
2. **Cost**: A full game run uses ~100K tokens. At cloud API prices, iterative development would be expensive.
3. **Experimentation**: I tested 4 different models (Qwen3.5, Gemma4, Phi4, GLM4). Local deployment makes A/B testing trivial — `python agent.py --backend ollama --model gemma4:26b`.
4. **The constraint is the feature**: Making a 27B parameter model play a complex strategy game well is a harder and more interesting engineering problem than throwing GPT-4 at it.

### Why One Card Per LLM Call?

The alternative — outputting an entire turn plan in one call — seems more efficient. But in STS2:

- Playing a card can trigger draw effects, changing your hand mid-turn
- Discovery effects let you choose generated cards dynamically
- The "right" play for card #3 depends on what cards #1 and #2 actually did

The game state is non-deterministic within a turn. One-card-per-call ensures the LLM always sees the actual current state, not a predicted one.

### Why Pre-compute Instead of a Calculator Tool?

I could have given the LLM a `combat_calc` tool to call. Instead, I pre-compute hints and inject them into every combat prompt. Reasons:

1. **Fewer LLM calls**: A tool-call pattern requires the LLM to decide to call the tool, wait for results, then decide what to play. That's 2-3 calls instead of 1.
2. **Local models often fumble multi-step tool use**: Calling tool A, reading its output, then calling tool B based on A's output requires strong instruction-following. 27B models struggle here.
3. **The information is always useful**: There's no combat decision where damage/block numbers are irrelevant. Pre-computing avoids the "forgot to check" failure mode.

---

## What I'd Do Differently / Next Steps

1. **Hybrid rule engine + LLM**: Simple turns (all Defend when enemy attacks, all Strike when enemy buffs) should be handled by deterministic rules. LLM should only engage for genuinely complex decisions.

2. **Card knowledge database**: The agent has no idea what cards *do* beyond their description text. A structured database of card synergies, tier ratings, and archetype tags would dramatically improve deck-building decisions.

3. **Multi-turn combat planning**: Currently the agent is greedy — it optimizes the current turn without considering draw probability or enemy patterns. A 2-3 turn lookahead would be transformative for boss fights.

4. **Evaluation framework**: Right now I evaluate by watching runs and reading logs. An automated benchmark (run 50 games, measure win rate, average floor reached, HP efficiency) would enable systematic optimization.

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Agent | Python 3.9, OpenAI SDK |
| LLM backends | Ollama (Gemma4-26B), KoboldCPP (Qwen3.5-27B) |
| Game integration | STS2MCP (C# BepInEx mod, REST API) |
| Hardware | RTX 4090 (local inference) |
| Logging | JSONL with per-action timestamps and token tracking |

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Action success rate | 91% |
| Models tested | 4 (Qwen3.5-27B, Gemma4-26B, Phi4-14B, GLM4-9B) |
| Game states handled | 13 types |
| Reliability mechanisms | 7 (energy guard, auto end_turn, text parser fallback, play phase wait, loop detection, toggle guard, clean retry) |
| Lines of agent code | ~850 |
| Test runs logged | 26 |
