"""Deck Analyzer — composition analysis and draw probability for STS2.

Provides deterministic answers to questions like:
- "What does my deck look like?"
- "What's my chance of drawing X in the opening hand?"
- "Should I add this card?"
"""

from __future__ import annotations

from math import comb
from collections import Counter

from .helpers import fetch_game_state


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

class DeckAnalyzer:
    """Analyzes deck composition and draw probabilities."""

    def __init__(self, game_state: dict):
        """Build deck model from game state.

        In combat: reconstructs full deck from hand + draw + discard + exhaust.
        Out of combat: uses deck info from map/rest/shop/rewards state.
        """
        self.in_combat = game_state.get("state_type") in ("monster", "elite", "boss")
        self.cards: list[dict] = []  # Full deck card list
        self.draw_pile: list[dict] = []
        self.hand: list[dict] = []
        self.discard_pile: list[dict] = []
        self.exhaust_pile: list[dict] = []

        if self.in_combat:
            battle = game_state.get("battle", {})
            player = battle.get("player", {})
            self.hand = player.get("hand", [])
            self.draw_pile = player.get("draw_pile", [])
            self.discard_pile = player.get("discard_pile", [])
            self.exhaust_pile = player.get("exhaust_pile", [])
            # Full deck = everything still in play (exclude exhausted)
            self.cards = self.hand + self.draw_pile + self.discard_pile
        else:
            # Out of combat: try to get deck from various state types
            # The game state includes deck info in draw_pile on map screens
            for section in ("map", "rest_site", "shop", "rewards"):
                data = game_state.get(section, {})
                player = data.get("player", {})
                if player:
                    break
            # Note: out-of-combat, full deck is not directly exposed.
            # We'd need to track it via card rewards. For now, this is
            # a known limitation — recommend calling during combat or
            # after game state exposes full deck.

    def composition(self) -> dict:
        """Analyze deck composition by type, cost, and overall metrics."""
        if not self.cards:
            return {"error": "No deck data available. Call during combat for full analysis."}

        by_type = Counter()
        by_cost = Counter()
        upgraded = 0
        total_cost = 0
        cost_count = 0

        for card in self.cards:
            ctype = card.get("type", "Unknown")
            by_type[ctype] += 1

            cost_str = str(card.get("cost", card.get("description", ""))).strip()
            if cost_str == "X":
                by_cost["X"] += 1
            else:
                try:
                    c = int(cost_str)
                    by_cost[c] += 1
                    total_cost += c
                    cost_count += 1
                except (ValueError, TypeError):
                    by_cost["?"] += 1

            if card.get("is_upgraded"):
                upgraded += 1

        total = len(self.cards)
        avg_cost = round(total_cost / cost_count, 2) if cost_count > 0 else 0

        # Assessment
        assessment_notes = []
        if total > 30:
            assessment_notes.append(f"⚠️ Deck is bloated ({total} cards). Consider removing weak cards.")
        elif total < 15:
            assessment_notes.append(f"✓ Very lean deck ({total} cards). Great draw consistency.")
        else:
            assessment_notes.append(f"Deck size is reasonable ({total} cards).")

        if avg_cost > 1.8:
            assessment_notes.append("⚠️ High average cost. Risk of bricking on low-energy turns.")
        if by_type.get("Power", 0) == 0:
            assessment_notes.append("⚠️ No Power cards. Deck has no scaling.")
        if by_type.get("Attack", 0) / max(total, 1) > 0.65:
            assessment_notes.append("⚠️ Attack-heavy. May lack defensive options.")

        return {
            "total_cards": total,
            "by_type": dict(by_type),
            "by_cost": {str(k): v for k, v in sorted(by_cost.items(), key=lambda x: str(x[0]))},
            "average_cost": avg_cost,
            "upgraded_count": upgraded,
            "upgraded_pct": round(upgraded / total * 100, 1) if total > 0 else 0,
            "assessment": " | ".join(assessment_notes),
        }

    def draw_probability(self, card_name: str, draw_count: int = 5) -> dict:
        """Calculate probability of drawing at least one copy of a card.

        Uses hypergeometric distribution:
            P(X >= 1) = 1 - C(N-K, n) / C(N, n)
        where:
            N = draw pile size
            K = copies of target card in draw pile
            n = number of cards drawn

        Args:
            card_name: Name of the card to check.
            draw_count: Number of cards drawn (default 5 = opening hand).
        """
        if not self.in_combat:
            return {"error": "Draw probability requires combat state (need draw pile info)."}

        pile = self.draw_pile
        n_total = len(pile)

        if n_total == 0:
            return {"card": card_name, "error": "Draw pile is empty."}

        # Count copies in draw pile
        copies = sum(
            1 for c in pile
            if c.get("name", "").lower().strip() == card_name.lower().strip()
        )

        # Also count in full deck for context
        copies_in_deck = sum(
            1 for c in self.cards
            if c.get("name", "").lower().strip() == card_name.lower().strip()
        )
        copies_in_hand = sum(
            1 for c in self.hand
            if c.get("name", "").lower().strip() == card_name.lower().strip()
        )
        copies_in_discard = sum(
            1 for c in self.discard_pile
            if c.get("name", "").lower().strip() == card_name.lower().strip()
        )

        # Calculate probability for different draw counts
        probs = {}
        for n_draw in [5, 10, 15]:
            actual_draw = min(n_draw, n_total)
            if copies == 0 or actual_draw == 0:
                probs[f"in_{n_draw}_cards"] = 0.0
            else:
                # P(X >= 1) = 1 - C(N-K, n) / C(N, n)
                p_miss = comb(n_total - copies, actual_draw) / comb(n_total, actual_draw)
                probs[f"in_{n_draw}_cards"] = round(1 - p_miss, 4)

        return {
            "card": card_name,
            "copies_in_draw_pile": copies,
            "copies_in_hand": copies_in_hand,
            "copies_in_discard": copies_in_discard,
            "copies_in_deck_total": copies_in_deck,
            "draw_pile_size": n_total,
            "probabilities": probs,
        }

    def identify_weaknesses(self) -> list[str]:
        """Identify common deck weaknesses."""
        if not self.cards:
            return ["Cannot analyze: no deck data."]

        issues = []
        names = [c.get("name", "").lower() for c in self.cards]
        types = [c.get("type", "").lower() for c in self.cards]
        descs = [c.get("description", "").lower() for c in self.cards]

        # Check for AoE
        has_aoe = any("all enemies" in d for d in descs)
        if not has_aoe:
            issues.append("No AoE damage — multi-enemy fights will be slow and painful.")

        # Check for scaling
        has_scaling = any(
            any(kw in d for kw in ["strength", "dexterity", "demon form", "limit break"])
            for d in descs
        )
        has_powers = "power" in types
        if not has_scaling and not has_powers:
            issues.append("No scaling — deck will struggle in long boss fights.")

        # Check for card draw
        has_draw = any("draw" in d and "card" in d for d in descs)
        if not has_draw:
            issues.append("No card draw — risk of dead hands, especially with high-cost cards.")

        # Check for status/curse handling
        has_exhaust = any("exhaust" in d for d in descs)
        has_curses = any(t == "curse" for t in types) or any(t == "status" for t in types)
        if has_curses and not has_exhaust:
            issues.append("Have Curses/Statuses but no Exhaust — they'll clog the deck.")

        # Starter card ratio
        starter_count = sum(1 for n in names if n in ("strike", "defend", "bash"))
        total = len(self.cards)
        if starter_count / total > 0.4:
            issues.append(f"Still {starter_count}/{total} starter cards — consider removing Strikes.")

        if not issues:
            issues.append("No major weaknesses detected. Deck looks solid.")

        return issues


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_composition(comp: dict) -> str:
    """Format composition dict as markdown."""
    if "error" in comp:
        return f"Error: {comp['error']}"

    lines = [
        "## Deck Composition\n",
        f"**Total cards:** {comp['total_cards']} | "
        f"**Average cost:** {comp['average_cost']} | "
        f"**Upgraded:** {comp['upgraded_count']} ({comp['upgraded_pct']}%)\n",
        "### By Type",
    ]
    for t, count in sorted(comp["by_type"].items()):
        pct = round(count / comp["total_cards"] * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines.append(f"  {t:10s} {count:3d}  {bar} {pct}%")

    lines.append("\n### By Cost")
    for cost, count in sorted(comp["by_cost"].items(), key=lambda x: str(x[0])):
        lines.append(f"  Cost {cost}: {'■' * count} ({count})")

    lines.append(f"\n### Assessment\n{comp['assessment']}")
    return "\n".join(lines)


def format_probability(prob: dict) -> str:
    """Format probability dict as markdown."""
    if "error" in prob:
        return f"**{prob['card']}**: {prob['error']}"

    lines = [f"## Draw Probability: {prob['card']}\n"]
    lines.append(f"Copies — draw pile: {prob['copies_in_draw_pile']} | "
                 f"hand: {prob['copies_in_hand']} | "
                 f"discard: {prob['copies_in_discard']} | "
                 f"total: {prob['copies_in_deck_total']}")
    lines.append(f"Draw pile size: {prob['draw_pile_size']}\n")

    for label, p in prob["probabilities"].items():
        n = label.replace("in_", "").replace("_cards", "")
        pct = round(p * 100, 1)
        lines.append(f"  P(draw in {n:>2s} cards): {pct:5.1f}%")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------

def register_deck_tools(mcp, get_base_url):
    """Register deck analysis tools with the MCP server."""

    @mcp.tool()
    async def deck_analyze(
        analysis_type: str = "full",
    ) -> str:
        """Analyze the current deck composition and identify strengths/weaknesses.

        Best called during combat (has full deck visibility) or when making
        card reward decisions.

        Args:
            analysis_type: What to analyze.
                "composition" — card counts by type, cost curve, avg cost
                "weakness" — identify gaps (no AoE, no scaling, etc.)
                "full" — both composition and weakness analysis
        """
        try:
            base_url = get_base_url()
            state = await fetch_game_state(base_url)
            analyzer = DeckAnalyzer(state)

            sections = []
            if analysis_type in ("full", "composition"):
                comp = analyzer.composition()
                sections.append(format_composition(comp))

            if analysis_type in ("full", "weakness"):
                weaknesses = analyzer.identify_weaknesses()
                lines = ["## Deck Weaknesses\n"]
                for w in weaknesses:
                    lines.append(f"- {w}")
                sections.append("\n".join(lines))

            return "\n\n---\n\n".join(sections) if sections else "Unknown analysis_type"

        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def deck_draw_probability(
        card_names: list[str],
        turns: int = 1,
    ) -> str:
        """Calculate probability of drawing specific card(s) from the draw pile.

        Uses exact hypergeometric distribution. Only works during combat
        (needs draw pile information).

        Args:
            card_names: Card names to check (e.g. ["Bash", "Inflame"]).
            turns: Number of turns to check (1 turn = 5 cards drawn).
        """
        try:
            base_url = get_base_url()
            state = await fetch_game_state(base_url)

            if state.get("state_type") not in ("monster", "elite", "boss"):
                return "Error: Draw probability requires being in combat."

            analyzer = DeckAnalyzer(state)
            results = []
            for name in card_names:
                prob = analyzer.draw_probability(name, draw_count=5 * turns)
                results.append(format_probability(prob))

            return "\n\n".join(results)

        except Exception as e:
            return f"Error: {e}"
