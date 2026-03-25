"""HTTP wrapper for the STS2 game mod REST API."""

import httpx
from config import GAME_API_URL


class GameAPI:
    """Thin wrapper around the STS2 mod HTTP endpoints."""

    def __init__(self, base_url: str = GAME_API_URL):
        self.base_url = base_url

    def get_state(self, fmt: str = "markdown") -> str:
        """Fetch current game state as markdown or JSON string."""
        r = httpx.get(self.base_url, params={"format": fmt}, timeout=10)
        r.raise_for_status()
        return r.text

    def get_state_json(self) -> dict:
        """Fetch current game state as parsed dict."""
        r = httpx.get(self.base_url, params={"format": "json"}, timeout=10)
        r.raise_for_status()
        return r.json()

    def post_action(self, action: str, **kwargs) -> dict:
        """Send an action to the game and return the result."""
        body = {"action": action, **kwargs}
        r = httpx.post(self.base_url, json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    # --- Convenience methods (map to tool names) ---

    def play_card(self, card_index: int, target: str | None = None) -> dict:
        args = {"card_index": card_index}
        if target:
            args["target"] = target
        return self.post_action("play_card", **args)

    def end_turn(self) -> dict:
        return self.post_action("end_turn")

    def use_potion(self, slot: int, target: str | None = None) -> dict:
        args = {"slot": slot}
        if target:
            args["target"] = target
        return self.post_action("use_potion", **args)

    def choose_map_node(self, index: int) -> dict:
        return self.post_action("choose_map_node", index=index)

    def claim_reward(self, index: int) -> dict:
        return self.post_action("claim_reward", index=index)

    def pick_card_reward(self, card_index: int) -> dict:
        return self.post_action("select_card_reward", card_index=card_index)

    def skip_card_reward(self) -> dict:
        return self.post_action("skip_card_reward")

    def proceed(self) -> dict:
        return self.post_action("proceed")

    def choose_rest_option(self, index: int) -> dict:
        return self.post_action("choose_rest_option", index=index)

    def shop_purchase(self, index: int) -> dict:
        return self.post_action("shop_purchase", index=index)

    def choose_event_option(self, index: int) -> dict:
        return self.post_action("choose_event_option", index=index)

    def advance_dialogue(self) -> dict:
        return self.post_action("advance_dialogue")

    def select_card(self, index: int) -> dict:
        return self.post_action("select_card", index=index)

    def confirm_selection(self) -> dict:
        return self.post_action("confirm_selection")

    def cancel_selection(self) -> dict:
        return self.post_action("cancel_selection")

    def combat_select_card(self, card_index: int) -> dict:
        return self.post_action("combat_select_card", card_index=card_index)

    def combat_confirm_selection(self) -> dict:
        return self.post_action("combat_confirm_selection")

    def treasure_claim_relic(self, index: int) -> dict:
        return self.post_action("claim_treasure_relic", index=index)

    def select_relic(self, index: int) -> dict:
        return self.post_action("select_relic", index=index)

    def skip_relic_selection(self) -> dict:
        return self.post_action("skip_relic_selection")
