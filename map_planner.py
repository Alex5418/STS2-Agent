"""Map path planner for STS2 agent.

Computes optimal paths through the act map using DFS + scoring.
Act 1: deterministic override (bypass LLM).
Act 2/3: hint injection (LLM has autonomy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Node type weights ──

BASE_NODE_WEIGHTS: dict[str, float] = {
    "Monster":  -5,
    "Unknown":  +8,
    "Shop":     +12,
    "RestSite": +15,   # Base — dynamically adjusted by HP
    "Treasure": +20,
    "Ancient":  +5,
    "Boss":      0,
    "Elite":     0,    # Handled separately per act
}

ELITE_PENALTY_BY_ACT: dict[int, float] = {
    1: -40,
    2: -20,
    3: -10,
}


@dataclass
class MapNode:
    col: int
    row: int
    type: str
    children: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class PathRecommendation:
    next_index: int          # index for choose_map_node
    path: list[MapNode]      # full path from next step to boss
    score: float
    explanation: str


class MapPlanner:
    """Stateful map planner — caches path per act, recomputes on act change or detour."""

    MAX_PATHS = 500  # Safety cap for path enumeration

    def __init__(self):
        self._cached_act: Optional[int] = None
        self._graph: dict[tuple[int, int], MapNode] = {}
        self._boss_pos: Optional[tuple[int, int]] = None
        self._recommended_path: Optional[list[tuple[int, int]]] = None
        self._cached_rec: Optional[PathRecommendation] = None

    def get_recommendation(self, state_json: dict) -> Optional[tuple[int, str]]:
        """Main entry point. Returns (next_options_index, explanation) or None."""
        map_data = state_json.get("map")
        if not map_data:
            return None

        run = state_json.get("run", {})
        act = run.get("act", 1)
        nodes_raw = map_data.get("nodes", [])
        next_options = map_data.get("next_options", [])

        if not nodes_raw or not next_options:
            return None

        # Single option — no planning needed
        if len(next_options) == 1:
            node_type = next_options[0].get("type", "?")
            return (next_options[0]["index"], f"Only one path available ({node_type})")

        # Check if we need to recompute
        current_pos = map_data.get("current_position")
        needs_recompute = (
            act != self._cached_act
            or self._recommended_path is None
            or not self._is_on_path(current_pos)
        )

        if needs_recompute:
            self._build_graph(nodes_raw, map_data.get("boss"))
            rec = self._plan(map_data, act)
            if rec is None:
                return None
            self._cached_act = act
            self._cached_rec = rec
            self._recommended_path = [(n.col, n.row) for n in rec.path]
            return (rec.next_index, rec.explanation)

        # Cached — find which next_option is on the planned path
        for opt in next_options:
            pos = (opt["col"], opt["row"])
            if pos in self._recommended_path:
                node_type = opt.get("type", "?")
                return (opt["index"], f"Following planned path → {node_type}")

        # Current position not leading to cached path — recompute
        self._build_graph(nodes_raw, map_data.get("boss"))
        rec = self._plan(map_data, act)
        if rec is None:
            return None
        self._cached_rec = rec
        self._recommended_path = [(n.col, n.row) for n in rec.path]
        return (rec.next_index, rec.explanation)

    def _is_on_path(self, current_pos: Optional[dict]) -> bool:
        """Check if current position is on the cached recommended path."""
        if current_pos is None or self._recommended_path is None:
            return False
        pos = (current_pos.get("col"), current_pos.get("row"))
        # Check if any node on the path is reachable from current position
        # (current_pos itself or a predecessor)
        return pos in self._recommended_path

    def _build_graph(self, nodes_raw: list[dict], boss_data: Optional[dict]):
        """Parse nodes list into adjacency dict."""
        self._graph = {}
        max_row = -1
        max_row_node = None

        for n in nodes_raw:
            col, row = n.get("col", 0), n.get("row", 0)
            children = []
            for c in n.get("children", []):
                if isinstance(c, list) and len(c) >= 2:
                    children.append((c[0], c[1]))
                elif isinstance(c, dict):
                    children.append((c.get("col", 0), c.get("row", 0)))

            node = MapNode(col=col, row=row, type=n.get("type", "Unknown"), children=children)
            self._graph[(col, row)] = node

            if row > max_row:
                max_row = row
                max_row_node = (col, row)

        # Determine boss position
        if boss_data:
            self._boss_pos = (boss_data.get("col", 0), boss_data.get("row", 0))
        elif max_row_node:
            self._boss_pos = max_row_node
        else:
            self._boss_pos = None

    def _plan(self, map_data: dict, act: int) -> Optional[PathRecommendation]:
        """Core planning: enumerate paths from each next_option, score, return best."""
        if self._boss_pos is None:
            return None

        next_options = map_data.get("next_options", [])
        player = map_data.get("player", {})
        hp = player.get("hp", 80)
        max_hp = player.get("max_hp", 80)
        hp_pct = hp / max(max_hp, 1)

        best_rec: Optional[PathRecommendation] = None

        for opt in next_options:
            start = (opt["col"], opt["row"])
            if start not in self._graph:
                continue

            paths = self._enumerate_paths(start)
            for path in paths:
                score = self._score_path(path, act, hp_pct)
                if best_rec is None or score > best_rec.score:
                    explanation = self._build_explanation(path, act, hp_pct)
                    best_rec = PathRecommendation(
                        next_index=opt["index"],
                        path=path,
                        score=score,
                        explanation=explanation,
                    )

        return best_rec

    def _enumerate_paths(self, start: tuple[int, int]) -> list[list[MapNode]]:
        """DFS from start to boss. Returns all complete paths, capped at MAX_PATHS."""
        results: list[list[MapNode]] = []
        self._dfs(start, [], results)
        return results

    def _dfs(self, pos: tuple[int, int], current_path: list[MapNode],
             results: list[list[MapNode]]):
        """Recursive DFS with path accumulation."""
        if len(results) >= self.MAX_PATHS:
            return

        node = self._graph.get(pos)
        if node is None:
            return

        current_path = current_path + [node]

        if pos == self._boss_pos or not node.children:
            results.append(current_path)
            return

        for child_pos in node.children:
            self._dfs(child_pos, current_path, results)

    def _score_path(self, path: list[MapNode], act: int, hp_pct: float) -> float:
        """Score a full path: sum of node scores + diversity bonus."""
        score = sum(self._score_node(n, act, hp_pct) for n in path)

        # Diversity bonus: reward variety of non-Monster types
        variety = {n.type for n in path if n.type not in ("Monster", "Boss")}
        score += len(variety) * 3

        return score

    def _score_node(self, node: MapNode, act: int, hp_pct: float) -> float:
        """Score a single node considering act and HP."""
        ntype = node.type

        if ntype == "Elite":
            base = ELITE_PENALTY_BY_ACT.get(act, -10)
            # Elite penalty worsens as HP drops
            return base * (1.0 + (1.0 - hp_pct) * 0.5)

        if ntype == "RestSite":
            # RestSite value increases as HP drops
            return 15 + (1.0 - hp_pct) * 25

        return BASE_NODE_WEIGHTS.get(ntype, 0)

    def _build_explanation(self, path: list[MapNode], act: int, hp_pct: float) -> str:
        """One-line human-readable rationale."""
        # Count node types on path
        type_counts: dict[str, int] = {}
        for n in path:
            type_counts[n.type] = type_counts.get(n.type, 0) + 1

        # Build route summary (abbreviated)
        route = " → ".join(n.type for n in path[:6])
        if len(path) > 6:
            route += f" → ...({len(path)} nodes)"

        # Highlight key factors
        parts = [route]

        elite_count = type_counts.get("Elite", 0)
        rest_count = type_counts.get("RestSite", 0)
        shop_count = type_counts.get("Shop", 0)

        if elite_count == 0:
            parts.append("no Elites")
        elif elite_count == 1:
            parts.append("1 Elite")
        else:
            parts.append(f"{elite_count} Elites")

        if rest_count > 0:
            parts.append(f"{rest_count} Rest")
        if shop_count > 0:
            parts.append(f"{shop_count} Shop")

        score = self._score_path(path, act, hp_pct)
        parts.append(f"score={score:.0f}")

        return " | ".join(parts)
