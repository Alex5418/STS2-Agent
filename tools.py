"""Tool definitions for the STS2 agent.

Tools are organized by game state_type so the LLM only sees
relevant actions for the current screen.
"""
from __future__ import annotations


REASONING_PARAM = {
    "reasoning": {
        "type": "string",
        "description": "Brief reasoning for this action (1-2 sentences). Think step by step: what is the situation, why this action.",
    },
}


def _tool(name: str, desc: str, params: dict, required: list[str] | None = None):
    """Helper to build an OpenAI-format tool definition."""
    all_params = {**REASONING_PARAM, **params}
    schema = {"type": "object", "properties": all_params}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": schema},
    }


# ──────────────────────────────────────────────
# Combat tools
# ──────────────────────────────────────────────

PLAY_CARD = _tool(
    "play_card",
    "Play a card from hand. Provide card_index (0-based). For single-target attacks, also provide target (entity_id like 'jaw_worm_0').",
    {
        "card_index": {"type": "integer", "description": "Index of card in hand"},
        "target": {"type": "string", "description": "Entity ID of target enemy (for single-target cards)"},
    },
    required=["card_index"],
)

END_TURN = _tool(
    "end_turn",
    "End your current turn. Do this after playing all cards you want to play.",
    {},
)

USE_POTION = _tool(
    "use_potion",
    "Use a potion from your potion slots. Use buff potions BEFORE playing cards.",
    {
        "slot": {"type": "integer", "description": "Potion slot index"},
        "target": {"type": "string", "description": "Target entity_id (for enemy-targeted potions)"},
    },
    required=["slot"],
)

# ──────────────────────────────────────────────
# In-combat card selection (exhaust/discard prompts)
# ──────────────────────────────────────────────

COMBAT_SELECT_CARD = _tool(
    "combat_select_card",
    "Select a card from hand during an in-combat prompt (e.g. 'select a card to exhaust').",
    {"card_index": {"type": "integer", "description": "Index of card to select"}},
    required=["card_index"],
)

COMBAT_CONFIRM = _tool(
    "combat_confirm_selection",
    "Confirm the in-combat card selection after selecting enough cards.",
    {},
)

# ──────────────────────────────────────────────
# Map
# ──────────────────────────────────────────────

CHOOSE_MAP_NODE = _tool(
    "choose_map_node",
    "Choose which map node to travel to next. Use the index from next_options.",
    {"index": {"type": "integer", "description": "Index from next_options list"}},
    required=["index"],
)

# ──────────────────────────────────────────────
# Rewards
# ──────────────────────────────────────────────

CLAIM_REWARD = _tool(
    "claim_reward",
    "Claim a reward (gold, potion, relic, or card). Card rewards open a card selection screen.",
    {"index": {"type": "integer", "description": "Reward index"}},
    required=["index"],
)

PICK_CARD = _tool(
    "pick_card_reward",
    "Pick a card from the card reward selection to add to your deck.",
    {"card_index": {"type": "integer", "description": "Index of card to pick"}},
    required=["card_index"],
)

SKIP_CARD = _tool(
    "skip_card_reward",
    "Skip the card reward without adding any card to your deck.",
    {},
)

PROCEED = _tool(
    "proceed",
    "Proceed to the map from the current screen (rewards, rest site, shop, or treasure).",
    {},
)

# ──────────────────────────────────────────────
# Rest site
# ──────────────────────────────────────────────

REST_OPTION = _tool(
    "choose_rest_option",
    "Choose a rest site option (Rest to heal, Smith to upgrade, etc.).",
    {"index": {"type": "integer", "description": "Option index"}},
    required=["index"],
)

# ──────────────────────────────────────────────
# Shop
# ──────────────────────────────────────────────

SHOP_PURCHASE = _tool(
    "shop_purchase",
    "Purchase an item from the shop.",
    {"index": {"type": "integer", "description": "Item index in shop inventory"}},
    required=["index"],
)

# ──────────────────────────────────────────────
# Event
# ──────────────────────────────────────────────

EVENT_OPTION = _tool(
    "choose_event_option",
    "Choose an event option. Also used for the Proceed button after events resolve.",
    {"index": {"type": "integer", "description": "Option index"}},
    required=["index"],
)

ADVANCE_DIALOGUE = _tool(
    "advance_dialogue",
    "Advance dialogue in ancient events. Call repeatedly until options appear.",
    {},
)

# ──────────────────────────────────────────────
# Card selection overlays (upgrade, transform, etc.)
# ──────────────────────────────────────────────

SELECT_CARD = _tool(
    "select_card",
    "Toggle a card's selection in the card selection screen (upgrade, transform, remove). "
    "WARNING: This is a toggle — calling it on an already-selected card will DESELECT it. "
    "To select 2 cards, call select_card with TWO DIFFERENT indices (e.g. index=0, then index=1). "
    "Do NOT call the same index twice or you will deselect it.",
    {"index": {"type": "integer", "description": "Card index in the grid"}},
    required=["index"],
)

CONFIRM_SELECTION = _tool(
    "confirm_selection",
    "Confirm the current card selection. Only call this AFTER you have selected the required number of cards.",
    {},
)

# ──────────────────────────────────────────────
# Relic selection (boss relic screen)
# ──────────────────────────────────────────────

SELECT_RELIC = _tool(
    "select_relic",
    "Select a relic from the boss relic selection screen. Pick is immediate.",
    {"index": {"type": "integer", "description": "Relic index from the relic list"}},
    required=["index"],
)

SKIP_RELIC = _tool(
    "skip_relic_selection",
    "Skip relic selection without picking any relic.",
    {},
)

# ──────────────────────────────────────────────
# Treasure
# ──────────────────────────────────────────────

CLAIM_TREASURE = _tool(
    "treasure_claim_relic",
    "Claim a relic from the treasure chest.",
    {"index": {"type": "integer", "description": "Relic index"}},
    required=["index"],
)


# ══════════════════════════════════════════════
# Tool routing by state type
# ══════════════════════════════════════════════

TOOLS_BY_STATE = {
    "monster":         [PLAY_CARD, END_TURN, USE_POTION],
    "elite":           [PLAY_CARD, END_TURN, USE_POTION],
    "boss":            [PLAY_CARD, END_TURN, USE_POTION],
    "hand_select":     [COMBAT_SELECT_CARD, COMBAT_CONFIRM],
    "map":             [CHOOSE_MAP_NODE],
    "combat_rewards":  [CLAIM_REWARD, PROCEED],
    "card_reward":     [PICK_CARD, SKIP_CARD],
    "rest_site":       [REST_OPTION, PROCEED],
    "shop":            [SHOP_PURCHASE, PROCEED],
    "event":           [EVENT_OPTION, ADVANCE_DIALOGUE, PROCEED],
    "card_select":     [SELECT_CARD, CONFIRM_SELECTION],
    "relic_select":    [SELECT_RELIC, SKIP_RELIC],
    "treasure":        [CLAIM_TREASURE, PROCEED],
}


def get_tools_for_state(state_type: str) -> list[dict]:
    """Return the appropriate tool set for the current game state."""
    return TOOLS_BY_STATE.get(state_type, [PROCEED])
