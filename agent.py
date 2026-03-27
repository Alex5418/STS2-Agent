"""STS2 Local Agent — Main game loop.

Usage:
    python agent.py                    # Run with default model
    python agent.py --model phi4:14b   # Run with specific model
"""

import argparse
import json
import re
import time
import os
from datetime import datetime

from openai import OpenAI
from game_api import GameAPI
from tools import get_tools_for_state
from prompts import get_prompt_for_state
from config import (
    OLLAMA_BASE_URL, OLLAMA_API_KEY, ACTIVE_MODEL,
    LLM_TEMPERATURE, LLM_MAX_TOKENS,
    MAX_RETRIES_PER_ACTION, MAX_HISTORY_TURNS,
    TURN_TIMEOUT_SECONDS,
    LOG_DIR, LOG_THINKING,
)
from map_planner import MapPlanner


# ──────────────────────────────────────────────
# Logger
# ──────────────────────────────────────────────

class RunLogger:
    """Logs every LLM call and game action to JSONL."""

    def __init__(self, model_name: str):
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_model = model_name.replace(":", "-").replace("/", "-")
        self.path = os.path.join(LOG_DIR, f"run_{safe_model}_{ts}.jsonl")
        self.file = open(self.path, "a", encoding="utf-8")
        print(f"[LOG] Writing to {self.path}")

    def log(self, entry: dict):
        entry["timestamp"] = datetime.now().isoformat()
        self.file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


# ──────────────────────────────────────────────
# Text-based tool call parser (KoboldCPP fallback)
# ──────────────────────────────────────────────

# Pattern 1: KoboldCPP "Made a function call ... to X with arguments = {...}"
_TEXT_TOOL_RE = re.compile(
    r'(?:Made a function call|function call)\s+\S+\s+to\s+(\w+)\s+with\s+arguments\s*=\s*(\{[^}]*\})',
    re.IGNORECASE,
)

# Pattern 2: Raw JSON tool call in text: {"name": "play_card", "arguments": {...}}
_JSON_TOOL_RE = re.compile(
    r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})\s*\}',
)

# Pattern 3: Simple function-like syntax: play_card({"card_index": 1, "target": "..."})
_FUNC_TOOL_RE = re.compile(
    r'\b(play_card|end_turn|use_potion|choose_map_node|claim_reward|pick_card_reward|'
    r'skip_card_reward|proceed|choose_rest_option|shop_purchase|choose_event_option|'
    r'advance_dialogue|select_card|confirm_selection|treasure_claim_relic|'
    r'select_relic|skip_relic_selection|combat_select_card|combat_confirm_selection)'
    r'\s*\(\s*(\{[^)]*\})\s*\)',
)

# Known tools that take no arguments — if model mentions them, we can call with {}
_NO_ARG_TOOLS = {"end_turn", "proceed", "skip_card_reward", "advance_dialogue",
                 "confirm_selection", "combat_confirm_selection", "skip_relic_selection"}

# Pattern 4: Just mentions a no-arg tool name like "I'll call end_turn"
_BARE_TOOL_RE = re.compile(
    r'\b(end_turn|proceed|skip_card_reward|advance_dialogue|confirm_selection|'
    r'combat_confirm_selection|skip_relic_selection)\b'
)


def _try_parse_json_block(content: str):
    """Extract tool call from ```json [...] ``` code blocks.

    Handles the common KoboldCPP pattern where the model outputs:
        ```json
        [{"name": "play_card", "arguments": {"card_index": 1, "target": "SEAPUNK_0"}}]
        ```
    or without the array wrapper.
    """
    # Find JSON code blocks
    blocks = re.findall(r'```(?:json)?\s*\n?([\s\S]*?)```', content)
    for block in blocks:
        block = block.strip()
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        # Could be a list of tool calls or a single one
        if isinstance(data, list) and len(data) > 0:
            data = data[0]  # Take first tool call

        if isinstance(data, dict) and "name" in data:
            name = data["name"]
            args = data.get("arguments", data.get("args", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            return name, args
    return None


def parse_tool_from_text(content: str):
    """Try to extract a tool call from LLM text output (KoboldCPP fallback).

    Tries multiple patterns in order of specificity.
    """
    # Pattern 0 (highest priority): ```json [...] ``` code blocks
    # This is the most common KoboldCPP format based on actual logs
    result = _try_parse_json_block(content)
    if result:
        return result

    # Pattern 1: KoboldCPP "Made a function call..." format
    m = _TEXT_TOOL_RE.search(content)
    if m:
        name = m.group(1)
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            args = {}
        return name, args

    # Pattern 2: Raw JSON {"name": "...", "arguments": {...}}
    m = _JSON_TOOL_RE.search(content)
    if m:
        name = m.group(1)
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            args = {}
        return name, args

    # Pattern 3: function_name({...})
    m = _FUNC_TOOL_RE.search(content)
    if m:
        name = m.group(1)
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            args = {}
        return name, args

    # Pattern 4: Bare no-arg tool name mentioned in text
    m = _BARE_TOOL_RE.search(content)
    if m:
        return m.group(1), {}

    return None


def extract_reasoning(content: str):
    """Extract reasoning from LLM output.

    Returns (thinking, visible_text):
    - thinking: content inside <think>...</think> tags
    - visible_text: everything outside <think> tags, stripped of tool call text
    """
    thinking = ""
    visible = content

    if "<think>" in content and "</think>" in content:
        start = content.index("<think>")
        end = content.index("</think>") + 8
        thinking = content[start + 7:end - 8].strip()
        visible = (content[:start] + content[end:]).strip()

    # Remove tool call text from visible output — not real reasoning
    visible = _TEXT_TOOL_RE.sub("", visible)
    visible = _JSON_TOOL_RE.sub("", visible)
    visible = _FUNC_TOOL_RE.sub("", visible).strip()

    return thinking, visible


# ──────────────────────────────────────────────
# Tool executor
# ──────────────────────────────────────────────

def execute_tool_call(game: GameAPI, name: str, args: dict) -> str:
    """Execute a tool call against the game API. Returns result as string."""
    try:
        dispatch = {
            "play_card":                lambda: game.play_card(args["card_index"], args.get("target")),
            "end_turn":                 lambda: game.end_turn(),
            "use_potion":               lambda: game.use_potion(args["slot"], args.get("target")),
            "combat_select_card":       lambda: game.combat_select_card(args["card_index"]),
            "combat_confirm_selection": lambda: game.combat_confirm_selection(),
            "choose_map_node":          lambda: game.choose_map_node(args["index"]),
            "claim_reward":             lambda: game.claim_reward(args["index"]),
            "pick_card_reward":         lambda: game.pick_card_reward(args["card_index"]),
            "skip_card_reward":         lambda: game.skip_card_reward(),
            "proceed":                  lambda: game.proceed(),
            "choose_rest_option":       lambda: game.choose_rest_option(args["index"]),
            "shop_purchase":            lambda: game.shop_purchase(args["index"]),
            "choose_event_option":      lambda: game.choose_event_option(args["index"]),
            "advance_dialogue":         lambda: game.advance_dialogue(),
            "select_card":              lambda: game.select_card(args["index"]),
            "confirm_selection":        lambda: game.confirm_selection(),
            "treasure_claim_relic":     lambda: game.treasure_claim_relic(args["index"]),
            "select_relic":             lambda: game.select_relic(args["index"]),
            "skip_relic_selection":     lambda: game.skip_relic_selection(),
        }

        if name not in dispatch:
            return json.dumps({"status": "error", "error": f"Unknown tool: {name}"})

        result = dispatch[name]()
        return json.dumps(result)

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

COMBAT_STATES = ("monster", "elite", "boss")


def _parse_combat_info(state_json: dict) -> dict:
    """Extract energy and card costs from state JSON for client-side validation.

    Returns dict with:
      - energy: int or None
      - hand: list of {index, name, cost} dicts (cost is int, or -1 for X-cost)
      - is_play_phase: bool or None
    """
    battle = state_json.get("battle", {})
    player = battle.get("player", {})

    energy = player.get("energy")
    is_play_phase = battle.get("is_play_phase")

    hand = []
    for card in player.get("hand", []):
        cost_str = str(card.get("cost", "0"))
        try:
            cost = int(cost_str)
        except ValueError:
            cost = -1  # X-cost or unplayable
        hand.append({
            "index": card.get("index", len(hand)),
            "name": card.get("name", "?"),
            "cost": cost,
        })

    return {"energy": energy, "hand": hand, "is_play_phase": is_play_phase}


def run_agent(model: str):
    """Main agent loop."""
    print(f"\n{'='*60}")
    print(f"  STS2 Local Agent")
    print(f"  Model: {model}")
    print(f"{'='*60}\n")

    llm = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
    game = GameAPI()
    logger = RunLogger(model)
    map_planner = MapPlanner()

    # Conversation history — resets each new screen context
    history = []
    last_state_type = None
    error_count = 0
    step = 0
    last_action_key = None     # "tool_name|args" string for loop detection
    repeat_count = 0           # How many times same action repeated

    # ── Combat tracking (for structured logging) ──
    combat_turn = 0           # Turn counter within a combat
    combat_actions = []       # Actions taken this combat (for summary)
    combat_hp_start = None    # HP at combat start
    last_combat_hp = None     # Last known HP during combat (for hp_end)

    # ── Energy guard (client-side tracking to prevent EnergyCostTooHigh) ──
    remaining_energy = None   # Set at turn start from state JSON
    hand_costs = {}           # card_index → cost (int, -1 for X)

    # ── Act tracking ──
    current_floor = 0

    try:
        while True:
            step += 1

            # ── 1. Get game state ──
            try:
                state_text = game.get_state("markdown")
                state_json = game.get_state_json()
                state_type = state_json.get("state_type", "unknown")
            except Exception as e:
                print(f"[ERROR] Can't reach game: {e}")
                print("  Is STS2 running with the mod enabled?")
                time.sleep(3)
                continue

            # Handle terminal states
            if state_type == "menu":
                print("[INFO] No run in progress. Start a run in STS2!")
                time.sleep(5)
                continue

            if state_type == "unknown":
                print(f"[INFO] Unknown state, waiting... ({state_json})")
                time.sleep(2)
                continue

            # Parse combat info for energy guard and play phase detection
            combat_info = _parse_combat_info(state_json) if state_type in COMBAT_STATES else {}

            # Wait for player turn — poll until is_play_phase=True or state changes
            if state_type in COMBAT_STATES:
                is_play_phase = combat_info.get("is_play_phase")
                if is_play_phase is False:
                    for _wait in range(8):
                        time.sleep(1.0)
                        try:
                            state_json = game.get_state_json()
                            new_type = state_json.get("state_type", "unknown")
                            if new_type != state_type:
                                break  # State changed (combat ended, etc.)
                            battle = state_json.get("battle", {})
                            if battle.get("is_play_phase") is True:
                                # Re-fetch markdown for LLM with updated state
                                state_text = game.get_state("markdown")
                                combat_info = _parse_combat_info(state_json)
                                break
                        except Exception:
                            pass
                    else:
                        # Still not our turn after 8s — re-enter main loop
                        continue

                # Initialize energy tracking at turn start
                if combat_info.get("energy") is not None:
                    remaining_energy = combat_info["energy"]
                    hand_costs = {c["index"]: c["cost"] for c in combat_info.get("hand", [])}

            # Track floor for act transitions
            run_info = state_json.get("run", {})
            floor = run_info.get("floor", 0)
            if floor and floor != current_floor:
                current_floor = floor

            # Helper: extract player HP from state JSON
            def _get_player_hp():
                return state_json.get("battle", {}).get("player", {}).get("hp", "?")

            # Update last known combat HP every step we're in combat
            if state_type in COMBAT_STATES:
                hp = _get_player_hp()
                if hp != "?":
                    last_combat_hp = hp

            # ── 2. Detect state transitions ──
            if state_type != last_state_type:
                # --- Combat just ended → log summary ---
                if last_state_type in COMBAT_STATES and state_type not in COMBAT_STATES:
                    # Use cached HP from last combat step (post-combat state won't have battle.player.hp)
                    hp_now = last_combat_hp if last_combat_hp is not None else "?"
                    summary = {
                        "event": "combat_summary",
                        "enemy_type": last_state_type,
                        "turns": combat_turn,
                        "hp_start": combat_hp_start,
                        "hp_end": hp_now,
                        "total_actions": len(combat_actions),
                        "actions": combat_actions[-20:],  # Last 20 actions to keep log manageable
                    }
                    logger.log(summary)
                    print(f"  [Combat Summary] {last_state_type} | {combat_turn} turns | HP: {combat_hp_start} → {hp_now} | {len(combat_actions)} actions")

                # --- Entering combat → reset trackers ---
                if state_type in COMBAT_STATES:
                    combat_turn = 0
                    combat_actions = []
                    combat_hp_start = _get_player_hp()
                    last_combat_hp = None

                history = []
                last_state_type = state_type
                error_count = 0
                print(f"\n--- State: {state_type} (floor {current_floor}) ---")

            # Track combat turns (each new state fetch in combat = new turn)
            if state_type in COMBAT_STATES:
                combat_turn += 1

            # ── 3. Build messages ──
            system_prompt = get_prompt_for_state(state_type)
            tools = get_tools_for_state(state_type)

            if not tools:
                print(f"[WARN] No tools for state '{state_type}', trying proceed...")
                try:
                    game.proceed()
                except Exception:
                    pass
                time.sleep(1)
                continue

            # ── Map planner: Act 1 bypass / Act 2+ hint ──
            map_rec = None
            if state_type == "map":
                map_rec = map_planner.get_recommendation(state_json)
                act = state_json.get("run", {}).get("act", 1)

                if map_rec is not None and act == 1:
                    idx, explanation = map_rec
                    print(f"  [MapPlanner] Act 1 override: index {idx} — {explanation}")
                    logger.log({
                        "step": step, "event": "map_planner_override",
                        "act": act, "chosen_index": idx, "explanation": explanation,
                    })
                    try:
                        result = execute_tool_call(game, "choose_map_node", {"index": idx})
                        print(f"  [Step {step}] choose_map_node({{'index': {idx}}}) ✓ (planner)")
                    except Exception as e:
                        print(f"  [Step {step}] Map planner execute failed: {e}")
                    time.sleep(1.5)
                    continue

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-MAX_HISTORY_TURNS * 2:])

            # Build user message with optional hints
            user_content = f"Current game state:\n\n{state_text}\n\n"

            # Hint: map planner recommendation for Act 2+
            if state_type == "map" and map_rec is not None:
                idx, explanation = map_rec
                user_content += f"MAP HINT: Path analysis recommends index {idx}. {explanation}. You may override if you have good strategic reasons.\n\n"

            # Hint: remind about 0-cost cards when energy is 0
            if state_type in COMBAT_STATES and remaining_energy == 0:
                zero_cost_cards = [c for c in combat_info.get("hand", []) if c["cost"] == 0]
                if zero_cost_cards:
                    names = ", ".join(f"{c['name']} (index {c['index']})" for c in zero_cost_cards)
                    user_content += f"REMINDER: You have 0 energy but can still play these 0-cost cards: {names}\n\n"

            user_content += "Choose your action."
            messages.append({"role": "user", "content": user_content})

            # ── 4. Call LLM ──
            t0 = time.time()
            try:
                response = llm.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=LLM_TEMPERATURE,
                    max_tokens=LLM_MAX_TOKENS,
                    timeout=TURN_TIMEOUT_SECONDS,
                )
            except Exception as e:
                err_str = str(e)
                if "10053" in err_str or "timed out" in err_str.lower() or "timeout" in err_str.lower():
                    print(f"[WARN] LLM timeout ({TURN_TIMEOUT_SECONDS}s). Retrying with shorter max_tokens...")
                    # Retry once with fewer tokens to get a faster response
                    try:
                        response = llm.chat.completions.create(
                            model=model,
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            temperature=LLM_TEMPERATURE,
                            max_tokens=512,  # Force shorter response
                            timeout=TURN_TIMEOUT_SECONDS,
                        )
                    except Exception as e2:
                        print(f"[ERROR] LLM retry also failed: {e2}")
                        # Force advance to avoid getting stuck
                        _force_advance(game, state_type, logger, step)
                        error_count = 0
                        time.sleep(2)
                        continue
                else:
                    print(f"[ERROR] LLM call failed: {e}")
                    time.sleep(2)
                    continue

            latency = int((time.time() - t0) * 1000)
            msg = response.choices[0].message
            content = msg.content or ""

            # ── 5. Extract reasoning ──
            thinking, visible_text = extract_reasoning(content)
            reasoning = thinking or visible_text  # Prefer <think> content, fall back to visible text

            # Log reasoning once per turn (not per card play)
            if reasoning and LOG_THINKING:
                print(f"  [Reason] {reasoning[:300]}")
                logger.log({
                    "step": step,
                    "event": "turn_reasoning",
                    "state_type": state_type,
                    "combat_turn": combat_turn if state_type in COMBAT_STATES else None,
                    "thinking": thinking or None,
                    "reasoning": visible_text or None,
                    "latency_ms": latency,
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(response.usage, "completion_tokens", None),
                })

            # ── 6. Parse tool calls ──
            tool_calls_to_process = []

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    raw_args = tc.function.arguments
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            parsed_args = {}
                    elif isinstance(raw_args, dict):
                        parsed_args = raw_args
                    else:
                        parsed_args = {}
                    tool_calls_to_process.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": parsed_args,
                    })

            # Fallback: if structured parsing yielded nothing, try text parsing
            # This covers: msg.tool_calls was None/empty, OR was truthy but malformed
            if not tool_calls_to_process and content:
                parsed = parse_tool_from_text(content)
                if parsed:
                    tool_name, tool_args = parsed
                    print(f"  [Step {step}] Recovered tool from text: {tool_name}({tool_args})")
                    tool_calls_to_process.append({
                        "id": f"fallback_{step}",
                        "name": tool_name,
                        "args": tool_args,
                    })

            if not tool_calls_to_process:
                # First miss: nudge the model to act with a direct follow-up
                if error_count == 0:
                    print(f"  [Step {step}] No tool call, nudging...")
                    # Add the model's text response to history, then send a nudge
                    history.append({"role": "assistant", "content": content})
                    history.append({"role": "user", "content": (
                        "You must call a tool now. Pick the best available action "
                        "and call it. Do not respond with text."
                    )})
                    error_count += 1
                    continue  # Re-enter loop (state will be re-fetched, nudge is in history)

                # Subsequent misses: log and count
                print(f"  [Step {step}] No tool call. LLM said: {content[:200]}")
                logger.log({
                    "step": step, "state_type": state_type,
                    "event": "no_tool_call", "content": content[:500],
                    "latency_ms": latency,
                })
                error_count += 1
                if error_count >= MAX_RETRIES_PER_ACTION:
                    _force_advance(game, state_type, logger, step)
                    error_count = 0
                time.sleep(1)
                continue

            # ── 7. Execute tool calls (single-tool mode: only first call) ──
            # Small models can't predict index shifts after card plays, so execute
            # one action at a time and re-fetch state for the next decision.
            if len(tool_calls_to_process) > 1:
                print(f"  [Step {step}] Model returned {len(tool_calls_to_process)} tool calls, executing only first")
            tool_calls_to_process = tool_calls_to_process[:1]

            for tc_info in tool_calls_to_process:
                tool_name = tc_info["name"]
                args = tc_info["args"]
                tc_id = tc_info["id"]

                # ── Energy guard: block play_card if insufficient energy ──
                if tool_name == "play_card" and state_type in COMBAT_STATES and remaining_energy is not None:
                    card_idx = args.get("card_index")
                    card_cost = hand_costs.get(card_idx, -1)  # -1 = unknown/X-cost, let it through
                    if card_cost >= 0 and card_cost > remaining_energy:
                        msg = f"Blocked: card {card_idx} costs {card_cost} energy but you have {remaining_energy}. Ending turn."
                        print(f"  [Step {step}] {tool_name}({args}) ✗ {msg}")
                        logger.log({
                            "step": step, "event": "energy_guard",
                            "state_type": state_type, "tool": tool_name, "args": args,
                            "card_cost": card_cost, "remaining_energy": remaining_energy,
                        })
                        # Auto end turn instead of sending a doomed request
                        _force_advance(game, state_type, logger, step)
                        error_count = 0
                        break

                print(f"  [Step {step}] {tool_name}({args}) ", end="")
                result_str = execute_tool_call(game, tool_name, args)
                result = json.loads(result_str)

                status = result.get("status", "?")
                error_msg = result.get("error", "")

                if status == "ok":
                    print(f"✓ {result.get('message', '')}")
                    error_count = 0
                    # After skipping card reward, auto-proceed to avoid claim→skip loop
                    if tool_name == "skip_card_reward":
                        print(f"  [Step {step}] Auto-proceed after skip")
                        try:
                            game.proceed()
                        except Exception:
                            pass
                    # After rest site action (Smith/Rest), auto-proceed to avoid retry errors
                    if tool_name == "choose_rest_option":
                        print(f"  [Step {step}] Auto-proceed after rest option")
                        time.sleep(1)  # Give game time to process
                        try:
                            game.proceed()
                        except Exception:
                            pass
                    # After selecting a card in hand_select/card_select, auto-confirm
                    # Prevents infinite select loop (e.g. Armaments upgrade prompt)
                    if tool_name in ("combat_select_card", "select_card"):
                        print(f"  [Step {step}] Auto-confirm after card selection")
                        try:
                            if tool_name == "combat_select_card":
                                game.combat_confirm_selection()
                            else:
                                game.confirm_selection()
                        except Exception:
                            pass
                    # Decrement energy on successful card play
                    if tool_name == "play_card" and remaining_energy is not None:
                        card_idx = args.get("card_index")
                        card_cost = hand_costs.get(card_idx, 0)
                        if card_cost >= 0:
                            remaining_energy = max(0, remaining_energy - card_cost)
                else:
                    print(f"✗ {error_msg}")
                    error_count += 1

                # Log action (no thinking here — reasoning is logged separately above)
                logger.log({
                    "step": step,
                    "event": "action",
                    "state_type": state_type,
                    "tool": tool_name,
                    "args": args,
                    "result_status": status,
                    "result_message": result.get("message") or error_msg,
                })

                # Track combat actions for summary
                if state_type in COMBAT_STATES:
                    combat_actions.append(f"{tool_name}({args}) → {status}")

                # Add to history
                history.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": tc_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(args)}}
                ]})
                history.append({"role": "tool", "tool_call_id": tc_id, "content": result_str})

                # "Not in combat" → combat ended naturally, not a real error
                if "Not in combat" in error_msg:
                    print(f"  [Step {step}] Combat already ended, moving on")
                    error_count = 0
                    break

                # EnergyCostTooHigh → immediate end_turn
                if "EnergyCostTooHigh" in error_msg and state_type in COMBAT_STATES:
                    print(f"  [Step {step}] Out of energy, auto end_turn")
                    _force_advance(game, state_type, logger, step)
                    error_count = 0
                    break

            # ── Loop detection: same action repeated too many times → force proceed ──
            if tool_calls_to_process:
                action_key = f"{tool_calls_to_process[0]['name']}|{tool_calls_to_process[0]['args']}"
                if action_key == last_action_key:
                    repeat_count += 1
                    if repeat_count >= 3:
                        print(f"  [Step {step}] Loop detected ({repeat_count}x {tool_calls_to_process[0]['name']}), forcing proceed")
                        _force_advance(game, state_type, logger, step)
                        repeat_count = 0
                        last_action_key = None
                        continue
                else:
                    repeat_count = 0
                    last_action_key = action_key

            # Pause after map navigation (game needs time to transition)
            if tool_calls_to_process and tool_calls_to_process[-1]["name"] == "choose_map_node":
                time.sleep(1.5)
            else:
                time.sleep(0.5)

            # Safety: if too many errors, force end turn or proceed
            if error_count >= MAX_RETRIES_PER_ACTION:
                _force_advance(game, state_type, logger, step)
                error_count = 0
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n[INFO] Agent stopped by user (Ctrl+C)")
    finally:
        # Log final combat summary if we were mid-combat
        if last_state_type in COMBAT_STATES and combat_actions:
            logger.log({
                "event": "combat_summary",
                "enemy_type": last_state_type,
                "turns": combat_turn,
                "hp_start": combat_hp_start,
                "total_actions": len(combat_actions),
                "actions": combat_actions[-20:],
                "note": "run ended mid-combat",
            })
        logger.close()
        print(f"[INFO] Log saved to {logger.path}")


def _force_advance(game: GameAPI, state_type: str, logger: RunLogger, step: int):
    """Force the game forward (end turn, confirm selection, or proceed). Logs the action."""
    if state_type in COMBAT_STATES:
        action = "end_turn"
        print(f"  [Step {step}] Forcing end_turn")
        try:
            game.end_turn()
        except Exception:
            pass
    elif state_type in ("card_select", "hand_select"):
        action = "confirm_selection"
        print(f"  [Step {step}] Forcing confirm_selection")
        try:
            game.confirm_selection()
        except Exception:
            pass
    else:
        action = "proceed"
        print(f"  [Step {step}] Forcing proceed")
        try:
            game.proceed()
        except Exception:
            pass
    logger.log({
        "step": step,
        "event": "forced_action",
        "state_type": state_type,
        "tool": f"forced_{action}",
        "args": {},
        "result_status": "forced",
        "result_message": f"Auto {action} after errors",
    })


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STS2 Local Agent")
    parser.add_argument("--model", default=ACTIVE_MODEL, help="Ollama model name")
    args = parser.parse_args()
    run_agent(args.model)
