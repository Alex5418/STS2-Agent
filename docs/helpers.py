"""Shared helpers for STS2 AI toolkit tools."""

import json
import httpx


async def fetch_game_state(base_url: str, fmt: str = "json") -> dict:
    """Fetch current game state from the STS2 mod HTTP server."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{base_url}/api/v1/singleplayer", params={"format": fmt})
        r.raise_for_status()
        return r.json() if fmt == "json" else {"raw": r.text}


def get_status_amount(statuses: list[dict], status_id: str) -> int:
    """Extract a status effect's amount from a creature's status list.

    Returns 0 if the status is not present.
    Common status IDs: 'strength', 'dexterity', 'vulnerable', 'weak',
    'frail', 'ritual', 'thorns', 'plated_armor', 'metallicize',
    'artifact', 'intangible'
    """
    for s in statuses:
        # Match by ID (lowercase) — the mod uses the entry ID
        sid = s.get("id", "").lower()
        if sid == status_id.lower():
            amount = s.get("amount", 0)
            return amount if amount != -1 else 999  # -1 = indefinite
    return 0


def parse_intent_damage(enemy: dict) -> int:
    """Estimate total damage from an enemy's current intents.

    Parses intent labels for damage numbers. Returns 0 if enemy is
    buffing/debuffing/sleeping.
    """
    total = 0
    for intent in enemy.get("intents", []):
        itype = intent.get("type", "").lower()
        if "attack" not in itype and "aggressive" not in itype:
            continue

        label = intent.get("label", "")
        # Labels typically look like "12" or "7x3"
        if not label:
            continue

        label = label.strip()
        if "x" in label.lower():
            parts = label.lower().split("x")
            try:
                dmg = int(parts[0].strip())
                hits = int(parts[1].strip())
                total += dmg * hits
            except (ValueError, IndexError):
                pass
        else:
            try:
                total += int(label)
            except ValueError:
                pass
    return total


def find_card_in_hand(hand: list[dict], card_ref: str | int) -> dict | None:
    """Find a card in hand by index (int) or name (str).

    For name matching, returns the first unmatched card with that name
    (handles duplicates).
    """
    if isinstance(card_ref, int):
        if 0 <= card_ref < len(hand):
            return hand[card_ref]
        return None

    name = card_ref.lower().strip()
    for card in hand:
        if card.get("name", "").lower().strip() == name:
            return card
    return None
