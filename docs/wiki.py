"""Wiki Lookup — local game knowledge base for STS2.

Provides card ratings, synergy info, enemy patterns, and boss guides.
Data lives in mcp/data/*.json files.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

_cache: dict[str, dict] = {}


def _load(filename: str) -> dict:
    """Load and cache a JSON data file."""
    if filename not in _cache:
        path = DATA_DIR / filename
        if path.exists():
            _cache[filename] = json.loads(path.read_text(encoding="utf-8"))
        else:
            _cache[filename] = {}
    return _cache[filename]


def lookup_card(query: str) -> dict | None:
    """Find a card by name (case-insensitive, partial match)."""
    # Check all character card files
    for f in DATA_DIR.glob("cards/*.json"):
        cards = json.loads(f.read_text(encoding="utf-8"))
        for card_id, card in cards.items():
            if query.lower() in card.get("name", "").lower():
                card["_id"] = card_id
                card["_source"] = f.stem
                return card
    return None


def lookup_relic(query: str) -> dict | None:
    """Find a relic by name."""
    relics = _load("relics.json")
    for rid, relic in relics.items():
        if query.lower() in relic.get("name", "").lower():
            relic["_id"] = rid
            return relic
    return None


def lookup_enemy(query: str) -> dict | None:
    """Find an enemy by name."""
    enemies = _load("enemies.json")
    for eid, enemy in enemies.items():
        if query.lower() in enemy.get("name", "").lower():
            enemy["_id"] = eid
            return enemy
    return None


def lookup_keyword(query: str) -> dict | None:
    """Find a game keyword definition."""
    keywords = _load("keywords.json")
    for kid, kw in keywords.items():
        if query.lower() in kid.lower() or query.lower() in kw.get("name", "").lower():
            kw["_id"] = kid
            return kw
    return None


def lookup_synergy(query: str) -> dict | None:
    """Find synergy info for an archetype or keyword."""
    synergies = _load("synergies.json")
    for sid, syn in synergies.items():
        if query.lower() in sid.lower():
            syn["_id"] = sid
            return syn
    return None


def format_card(card: dict) -> str:
    """Format card data as markdown."""
    lines = [
        f"## {card['name']} ({card.get('_source', '?').title()})",
        f"**Type:** {card.get('type')} | **Rarity:** {card.get('rarity')} | "
        f"**Cost:** {card.get('cost')} | **Tier:** {card.get('tier', '?')}",
        f"**Description:** {card.get('description', 'N/A')}",
    ]
    if card.get("upgraded"):
        upg = card["upgraded"]
        lines.append(f"**Upgraded:** {upg.get('description', upg.get('name', ''))}")
    if card.get("synergy_tags"):
        lines.append(f"**Synergies:** {', '.join(card['synergy_tags'])}")
    if card.get("archetypes"):
        lines.append(f"**Archetypes:** {', '.join(card['archetypes'])}")
    if card.get("notes"):
        lines.append(f"**Notes:** {card['notes']}")
    return "\n".join(lines)


def format_enemy(enemy: dict) -> str:
    """Format enemy data as markdown."""
    lines = [
        f"## {enemy['name']}",
        f"**Type:** {enemy.get('type')} | **Act:** {enemy.get('act')} | "
        f"**HP:** {enemy.get('hp_range', '?')} | **Danger:** {'⭐' * enemy.get('danger_rating', 1)}",
    ]
    if enemy.get("pattern"):
        lines.append(f"**Pattern:** {enemy['pattern']}")
    if enemy.get("moves"):
        lines.append("**Moves:**")
        for name, move in enemy["moves"].items():
            lines.append(f"  - **{name}**: {move.get('description', '?')}")
    if enemy.get("tips"):
        lines.append(f"**Tips:** {enemy['tips']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------

def register_wiki_tools(mcp):
    """Register wiki lookup tools with the MCP server."""

    @mcp.tool()
    async def wiki_lookup(
        query: str,
        category: str = "any",
    ) -> str:
        """Look up game knowledge: cards, relics, enemies, keywords, or synergies.

        Use this when you need to understand a card's mechanics, check enemy
        patterns, or find synergies for deck building.

        Args:
            query: Name or keyword to search (e.g. "Bash", "Vulnerable", "Jaw Worm").
            category: Filter by type — "card", "relic", "enemy", "keyword", "synergy", or "any".
        """
        results = []

        if category in ("card", "any"):
            card = lookup_card(query)
            if card:
                results.append(format_card(card))

        if category in ("relic", "any"):
            relic = lookup_relic(query)
            if relic:
                results.append(
                    f"## Relic: {relic['name']}\n"
                    f"**Description:** {relic.get('description', 'N/A')}\n"
                    f"**Tier:** {relic.get('tier', '?')}"
                )

        if category in ("enemy", "any"):
            enemy = lookup_enemy(query)
            if enemy:
                results.append(format_enemy(enemy))

        if category in ("keyword", "any"):
            kw = lookup_keyword(query)
            if kw:
                results.append(
                    f"## Keyword: {kw.get('name', query)}\n"
                    f"{kw.get('description', 'No description.')}"
                )

        if category in ("synergy", "any"):
            syn = lookup_synergy(query)
            if syn:
                lines = [f"## Synergy: {syn.get('_id', query).title()}"]
                lines.append(syn.get("description", ""))
                if syn.get("enablers"):
                    lines.append(f"**Enablers:** {', '.join(syn['enablers'])}")
                if syn.get("payoffs"):
                    lines.append(f"**Payoffs:** {', '.join(syn['payoffs'])}")
                results.append("\n".join(lines))

        if not results:
            return (
                f"No results found for '{query}' in category '{category}'.\n"
                f"The wiki data may not have this entry yet. "
                f"Wiki data files are in mcp/data/ — contributions welcome!"
            )

        return "\n\n---\n\n".join(results)
