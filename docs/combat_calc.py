"""Combat Calculator — deterministic damage/block simulation for STS2.

Eliminates LLM arithmetic errors by computing exact outcomes for
planned card sequences.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .helpers import fetch_game_state, get_status_amount, parse_intent_damage, find_card_in_hand


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EnemySnapshot:
    entity_id: str
    name: str
    hp: int
    max_hp: int
    block: int
    vulnerable: int  # stacks
    weak: int
    artifact: int
    thorns: int

    @staticmethod
    def from_state(e: dict) -> "EnemySnapshot":
        statuses = e.get("status", [])
        return EnemySnapshot(
            entity_id=e["entity_id"],
            name=e.get("name", "?"),
            hp=e["hp"],
            max_hp=e["max_hp"],
            block=e.get("block", 0),
            vulnerable=get_status_amount(statuses, "vulnerable"),
            weak=get_status_amount(statuses, "weak"),
            artifact=get_status_amount(statuses, "artifact"),
            thorns=get_status_amount(statuses, "thorns"),
        )


@dataclass
class PlayerSnapshot:
    hp: int
    max_hp: int
    block: int
    energy: int
    max_energy: int
    strength: int
    dexterity: int
    weak: int
    frail: int
    vulnerable: int

    @staticmethod
    def from_state(p: dict) -> "PlayerSnapshot":
        statuses = p.get("status", [])
        return PlayerSnapshot(
            hp=p["hp"],
            max_hp=p["max_hp"],
            block=p.get("block", 0),
            energy=p.get("energy", 0),
            max_energy=p.get("max_energy", 0),
            strength=get_status_amount(statuses, "strength"),
            dexterity=get_status_amount(statuses, "dexterity"),
            weak=get_status_amount(statuses, "weak"),
            frail=get_status_amount(statuses, "frail"),
            vulnerable=get_status_amount(statuses, "vulnerable"),
        )


@dataclass
class EnemyResult:
    entity_id: str
    name: str
    damage_dealt: int = 0
    remaining_hp: int = 0
    remaining_block: int = 0
    can_kill: bool = False
    overkill: int = 0


@dataclass
class SimResult:
    enemies: dict[str, EnemyResult] = field(default_factory=dict)
    block_gained: int = 0
    total_block: int = 0
    energy_remaining: int = 0
    cards_played: list[str] = field(default_factory=list)
    hand_remaining: list[str] = field(default_factory=list)
    incoming_damage_estimate: int = 0
    net_damage_after_block: int = 0
    errors: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["## Combat Simulation Result\n"]

        if self.errors:
            lines.append("### Warnings")
            for err in self.errors:
                lines.append(f"- ⚠️ {err}")
            lines.append("")

        lines.append("### Cards Played")
        lines.append(", ".join(self.cards_played) if self.cards_played else "(none)")
        lines.append(f"\n**Energy remaining:** {self.energy_remaining}")
        lines.append("")

        lines.append("### Enemy Outcomes")
        for eid, er in self.enemies.items():
            kill = "💀 KILL" if er.can_kill else ""
            lines.append(
                f"- **{er.name}** (`{eid}`): "
                f"{er.damage_dealt} dmg dealt → "
                f"HP {er.remaining_hp} / Block {er.remaining_block} "
                f"{kill}"
            )
        lines.append("")

        lines.append("### Defense")
        lines.append(f"- Block gained this sequence: {self.block_gained}")
        lines.append(f"- Total block: {self.total_block}")
        lines.append(f"- Estimated incoming damage: {self.incoming_damage_estimate}")
        lines.append(f"- Net damage after block: {max(0, self.net_damage_after_block)}")
        lines.append("")

        if self.hand_remaining:
            lines.append("### Hand Remaining")
            lines.append(", ".join(self.hand_remaining))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------

class CombatSimulator:
    """Simulates card play sequences without modifying actual game state."""

    def __init__(self, battle_state: dict):
        player_data = battle_state["player"]
        self.player = PlayerSnapshot.from_state(player_data)
        self.enemies: dict[str, EnemySnapshot] = {}
        for e in battle_state.get("enemies", []):
            snap = EnemySnapshot.from_state(e)
            self.enemies[snap.entity_id] = snap

        # Copy hand as list of dicts for card lookup
        self.hand = list(player_data.get("hand", []))
        self.incoming_damage = sum(
            parse_intent_damage(e) for e in battle_state.get("enemies", [])
        )

    def calc_attack_damage(self, base_damage: int, hits: int, target: EnemySnapshot) -> int:
        """Calculate total damage for an attack card."""
        per_hit = base_damage + self.player.strength
        if self.player.weak > 0:
            per_hit = math.floor(per_hit * 0.75)
        if target.vulnerable > 0:
            per_hit = math.floor(per_hit * 1.5)
        per_hit = max(0, per_hit)

        total = per_hit * hits
        # Subtract block first
        damage_to_hp = max(0, total - target.block)
        return total  # return raw damage (let result track block separately)

    def calc_block(self, base_block: int) -> int:
        """Calculate block gained from a skill card."""
        block = base_block + self.player.dexterity
        if self.player.frail > 0:
            block = math.floor(block * 0.75)
        return max(0, block)

    def apply_damage_to_enemy(self, enemy: EnemySnapshot, raw_damage: int) -> int:
        """Apply damage to enemy, accounting for block. Returns damage dealt to HP."""
        if raw_damage <= enemy.block:
            enemy.block -= raw_damage
            return 0
        damage_to_hp = raw_damage - enemy.block
        enemy.block = 0
        enemy.hp -= damage_to_hp
        return damage_to_hp

    def simulate(
        self,
        card_sequence: list[str | int],
        targets: list[str | None] | None = None,
    ) -> SimResult:
        """
        Simulate playing a sequence of cards.

        Args:
            card_sequence: Card names or hand indices to play in order.
            targets: Per-card target entity_ids. None entries = auto-target
                     (first alive enemy for single-target, all for AoE).

        Returns:
            SimResult with full breakdown.
        """
        result = SimResult()
        result.energy_remaining = self.player.energy
        result.total_block = self.player.block

        # Initialize enemy results
        for eid, e in self.enemies.items():
            result.enemies[eid] = EnemyResult(
                entity_id=eid,
                name=e.name,
                remaining_hp=e.hp,
                remaining_block=e.block,
            )

        # Track which hand cards are consumed (by index, to handle duplicates)
        hand_copy = list(self.hand)
        targets = targets or [None] * len(card_sequence)

        for i, card_ref in enumerate(card_sequence):
            # Find card in hand
            card = find_card_in_hand(hand_copy, card_ref)
            if card is None:
                result.errors.append(f"Card '{card_ref}' not found in hand")
                continue

            card_name = card.get("name", "?")
            card_cost_str = card.get("cost", "0")
            card_type = card.get("type", "").lower()
            target_type = card.get("target_type", "").lower()

            # Parse energy cost
            try:
                card_cost = int(card_cost_str)
            except (ValueError, TypeError):
                card_cost = 0  # X-cost or special

            # Check energy
            if card_cost > result.energy_remaining:
                result.errors.append(
                    f"Not enough energy for '{card_name}' "
                    f"(need {card_cost}, have {result.energy_remaining})"
                )
                continue

            # Deduct energy
            result.energy_remaining -= card_cost
            result.cards_played.append(card_name)
            hand_copy.remove(card)

            # Resolve target
            target_id = targets[i] if i < len(targets) else None
            target_enemy = None
            if target_id and target_id in self.enemies:
                target_enemy = self.enemies[target_id]
            elif "enemy" in target_type and self.enemies:
                # Auto-target: first alive enemy
                for e in self.enemies.values():
                    if e.hp > 0:
                        target_enemy = e
                        break

            # --- Apply card effects (simplified model) ---
            # This is a heuristic parser. For precise effects, the wiki
            # data should provide structured effect definitions.
            #
            # The card description is all we have from game state.
            # We parse "Deal X damage" and "Gain X Block" patterns.
            desc = card.get("description", "").lower()

            # Damage
            if "attack" in card_type or "deal" in desc:
                base_dmg = self._parse_damage(desc)
                hits = self._parse_hits(desc)
                if base_dmg > 0 and target_enemy:
                    raw = self.calc_attack_damage(base_dmg, hits, target_enemy)
                    dealt = self.apply_damage_to_enemy(target_enemy, raw)
                    er = result.enemies[target_enemy.entity_id]
                    er.damage_dealt += dealt
                    er.remaining_hp = target_enemy.hp
                    er.remaining_block = target_enemy.block
                    if target_enemy.hp <= 0:
                        er.can_kill = True
                        er.overkill = abs(target_enemy.hp)

            # Block
            if "block" in desc or "skill" in card_type:
                base_block = self._parse_block(desc)
                if base_block > 0:
                    gained = self.calc_block(base_block)
                    result.block_gained += gained
                    result.total_block += gained

        # Hand remaining
        result.hand_remaining = [c.get("name", "?") for c in hand_copy]

        # Enemy turn estimate
        result.incoming_damage_estimate = self.incoming_damage
        result.net_damage_after_block = self.incoming_damage - result.total_block

        return result

    # --- Description parsers (heuristic) ---

    @staticmethod
    def _parse_damage(desc: str) -> int:
        """Extract base damage from card description like 'deal 6 damage'."""
        import re
        m = re.search(r"deal\s+(\d+)\s+damage", desc)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _parse_hits(desc: str) -> int:
        """Extract hit count from description like '4 times' or 'x3'."""
        import re
        m = re.search(r"(\d+)\s+times", desc)
        if m:
            return int(m.group(1))
        m = re.search(r"x\s*(\d+)", desc)
        if m:
            return int(m.group(1))
        return 1

    @staticmethod
    def _parse_block(desc: str) -> int:
        """Extract base block from description like 'gain 5 block'."""
        import re
        m = re.search(r"gain\s+(\d+)\s+block", desc)
        return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------

def register_combat_tools(mcp, get_base_url):
    """Register combat calculator tools with the MCP server."""

    @mcp.tool()
    async def combat_calc(
        card_sequence: list[str],
        targets: list[str] | None = None,
    ) -> str:
        """Simulate playing a sequence of cards and calculate exact outcomes.

        Returns per-enemy damage dealt, block gained, energy remaining,
        can-kill assessment, and estimated incoming damage after your turn.
        Use this BEFORE playing cards to verify your plan.

        Args:
            card_sequence: Card names to play in order (e.g. ["Bash", "Strike", "Strike"]).
            targets: Per-card target entity_id (e.g. ["jaw_worm_0", ...]). None = auto-target.
        """
        try:
            base_url = get_base_url()
            state = await fetch_game_state(base_url)
            if state.get("state_type") not in ("monster", "elite", "boss"):
                return "Error: Not in combat. combat_calc only works during battles."

            battle = state.get("battle")
            if not battle or not battle.get("is_play_phase"):
                return "Error: Not in play phase."

            sim = CombatSimulator(battle)
            result = sim.simulate(card_sequence, targets)
            return result.to_markdown()

        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def combat_can_kill(target: str | None = None) -> str:
        """Quick check: what's the maximum damage you can deal this turn?

        Tries all attack cards in hand against the target (or strongest enemy)
        and reports if lethal is possible. Use this to decide offense vs defense.

        Args:
            target: Entity ID to check. None = check the enemy with lowest HP.
        """
        try:
            base_url = get_base_url()
            state = await fetch_game_state(base_url)
            if state.get("state_type") not in ("monster", "elite", "boss"):
                return "Error: Not in combat."

            battle = state.get("battle")
            if not battle:
                return "Error: No battle state."

            sim = CombatSimulator(battle)

            # Find target
            if target and target in sim.enemies:
                check_enemy = sim.enemies[target]
            else:
                # Lowest HP enemy
                check_enemy = min(sim.enemies.values(), key=lambda e: e.hp)

            # Collect all playable attack cards sorted by damage potential
            attacks = []
            energy_left = sim.player.energy
            for card in sim.hand:
                if card.get("can_play") is not True:
                    continue
                ctype = card.get("type", "").lower()
                desc = card.get("description", "").lower()
                if "attack" in ctype or "deal" in desc:
                    try:
                        cost = int(card.get("cost", 0))
                    except (ValueError, TypeError):
                        cost = 0
                    base_dmg = CombatSimulator._parse_damage(desc)
                    hits = CombatSimulator._parse_hits(desc)
                    attacks.append((card, cost, base_dmg, hits))

            # Greedy: play highest damage-per-energy first
            attacks.sort(key=lambda x: (x[2] * x[3]) / max(x[1], 1), reverse=True)

            total_damage = 0
            cards_used = []
            for card, cost, base_dmg, hits in attacks:
                if cost > energy_left:
                    continue
                raw = sim.calc_attack_damage(base_dmg, hits, check_enemy)
                total_damage += raw
                energy_left -= cost
                cards_used.append(card.get("name", "?"))

            effective = max(0, total_damage - check_enemy.block)
            can_kill = effective >= check_enemy.hp

            lines = [
                f"## Kill Check: {check_enemy.name} (`{check_enemy.entity_id}`)",
                f"Enemy HP: {check_enemy.hp} | Block: {check_enemy.block}",
                f"Max damage possible: {total_damage} (after block: {effective})",
                f"**{'💀 LETHAL — go all offense!' if can_kill else '❌ Cannot kill this turn'}**",
                f"Cards needed: {', '.join(cards_used) if cards_used else '(no attacks available)'}",
            ]
            return "\n".join(lines)

        except Exception as e:
            return f"Error: {e}"
