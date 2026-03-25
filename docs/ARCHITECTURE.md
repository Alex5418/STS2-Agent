# STS2 AI Toolkit — Architecture Design

> Supplementary MCP tools to improve AI agent win-rate in Slay the Spire 2.
> Designed to extend the existing `mcp/server.py` without modifying the C# mod.

---

## 1. Problem Statement

The current STS2 MCP setup gives the AI agent **game actions** (play card, end turn, etc.) and **raw game state** (hand, enemies, HP, etc.), but the agent must rely on pure LLM reasoning for:

- **Damage/block math** — Strength, Dexterity, Vulnerable, Weak, multi-hit, etc.
- **Card knowledge** — synergies, tier lists, archetype fit
- **Deck composition** — draw probability, average cost, archetype balance
- **Strategic planning** — "should I take this card?" / "can I kill this turn?"

LLMs are notoriously unreliable at arithmetic and tracking state across long contexts. These tools replace guesswork with deterministic computation.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Claude Code / Agent                │
│                                                      │
│  Uses MCP tools:                                     │
│    - Game Actions  (existing: play_card, end_turn..) │
│    - get_game_state (existing)                       │
│    - combat_calc    (NEW)                            │
│    - wiki_lookup    (NEW)                            │
│    - deck_analyze   (NEW)                            │
└──────────┬───────────────────────────┬───────────────┘
           │ MCP (stdio)              │ MCP (stdio)
           ▼                          ▼
┌─────────────────────┐   ┌────────────────────────────┐
│  mcp/server.py      │   │  mcp/tools/               │
│  (existing actions)  │   │    combat_calc.py          │
│  Talks to game HTTP  │   │    wiki.py                 │
│  localhost:15526     │   │    deck_analyzer.py         │
│                      │   │                            │
│  NEW: registers      │   │  mcp/data/                 │
│  toolkit tools from  │   │    cards.json              │
│  tools/ submodule    │   │    relics.json             │
│                      │   │    enemies.json            │
│                      │   │    synergies.json          │
└──────────────────────┘   └────────────────────────────┘
```

**Key design decisions:**

1. **All tools live in `mcp/tools/`** — separate Python modules registered into the existing FastMCP server
2. **Wiki data is static JSON** in `mcp/data/` — no SQLite needed (STS2 has ~200-300 cards per character, JSON is fast enough and easier to version-control)
3. **Combat calculator pulls live state** via the existing HTTP GET, then computes locally in Python
4. **No C# changes required** — everything works through the existing REST API

---

## 3. Tool #1: Combat Calculator (`combat_calc`)

### Purpose
Answer: "If I play cards X, Y, Z in this order, what happens?"

### MCP Tool Interface

```python
@mcp.tool()
async def combat_calc(
    card_sequence: list[str],     # Card names or indices, e.g. ["Strike", "Bash", "Strike"]
    targets: list[str] | None,    # Per-card target entity_ids (None = auto for AoE/self)
    use_potions: list[int] | None # Potion slots to use before cards (e.g. [0] for Flex)
) -> str:
    """
    Simulate playing a sequence of cards and return:
    - Total damage dealt per enemy (after Strength, Vulnerable, Weak, multi-hit)
    - Total block gained (after Dexterity, Frail)
    - Energy remaining after sequence
    - Per-enemy: can_kill (bool), remaining_hp, overkill
    - Cards left in hand after sequence
    """
```

### Core Computation Module (`mcp/tools/combat_calc.py`)

```python
# Damage formula (per STS2 mechanics):
#   base = card_base_damage + player_strength
#   if enemy_vulnerable: base *= 1.5 (rounded down)
#   if player_weak:      base *= 0.75 (rounded down)
#   total = base * hits - enemy_block
#
# Block formula:
#   base = card_base_block + player_dexterity
#   if player_frail: base *= 0.75 (rounded down)

class CombatSimulator:
    def __init__(self, game_state: dict):
        """Initialize from get_game_state(format='json') response."""
        self.player = game_state["battle"]["player"]
        self.enemies = game_state["battle"]["enemies"]
        self.hand = self.player["hand"]
        self.energy = self.player["energy"]

    def simulate_sequence(self, cards, targets, potions) -> SimResult:
        """
        Returns SimResult with:
          - per_enemy: {entity_id: {damage_dealt, remaining_hp, can_kill, overkill}}
          - player: {block_gained, total_block, energy_remaining, hp_after_enemy_turn}
          - enemy_turn_estimate: {total_incoming_damage, damage_after_block}
          - hand_after: [remaining card names]
        """

    def estimate_enemy_damage(self) -> int:
        """Parse intents to estimate incoming damage this turn."""

    def can_kill_all(self, cards, targets) -> bool:
        """Quick check: does this sequence kill all enemies?"""
```

### Status Effect Handling

The simulator needs to track these modifiers (extracted from game state `status` arrays):

| Effect | Source | Formula Impact |
|--------|--------|---------------|
| Strength | Player status | +N to all attack damage |
| Dexterity | Player status | +N to all block gained |
| Vulnerable | Enemy status | Damage taken ×1.5 |
| Weak | Player/Enemy status | Damage dealt ×0.75 |
| Frail | Player status | Block gained ×0.75 |
| Ritual | Enemy status | Enemy gains N Strength/turn (for multi-turn planning) |
| Thorns | Enemy status | N damage to player per attack card |
| Plated Armor | Enemy status | N block gained per turn (if not attacked) |

### Data Flow

```
Agent calls combat_calc(["Bash", "Strike", "Strike"], ["jaw_worm_0", ...])
  │
  ├─► Fetch current game state via HTTP GET (json format)
  ├─► Parse player stats (Strength, Dex, energy, hand)
  ├─► Parse enemy stats (HP, block, Vulnerable stacks, etc.)
  ├─► For each card in sequence:
  │     ├─ Validate: is card in hand? Enough energy?
  │     ├─ Apply card effect (damage/block/status)
  │     ├─ Remove from simulated hand, deduct energy
  │     └─ Track cumulative state
  ├─► Estimate enemy turn damage from intents
  └─► Return structured result
```

---

## 4. Tool #2: Card/Relic Wiki (`wiki_lookup`)

### Purpose
Answer: "What does this card/relic do?", "What synergizes with X?", "Is this card good for my archetype?"

### MCP Tool Interface

```python
@mcp.tool()
async def wiki_lookup(
    query: str,           # Card/relic/enemy name or keyword
    category: str = "any" # "card", "relic", "enemy", "keyword", "synergy", "any"
) -> str:
    """
    Look up game knowledge:
    - Card: full stats, upgrade effect, tier rating, synergy tags, archetype fit
    - Relic: effect, tier rating, best-with notes
    - Enemy: HP range, move patterns, key mechanics, tips
    - Keyword: mechanic explanation (Exhaust, Ethereal, Innate, etc.)
    - Synergy: given a card/relic, list what combos well with it
    """

@mcp.tool()
async def wiki_boss_guide(
    boss_name: str,
    character: str | None = None  # Auto-detect from game state if None
) -> str:
    """
    Detailed boss fight strategy:
    - Boss move pattern / phases
    - Recommended deck composition
    - Key cards to have / avoid
    - Potion usage timing
    - Common mistakes
    """

@mcp.tool()
async def wiki_rate_card_reward(
    card_names: list[str],  # The 3 card choices offered
    context: str = "auto"   # "auto" fetches current deck/relics/floor
) -> str:
    """
    Rate each card choice in context of current deck:
    - Base tier rating (S/A/B/C/D)
    - Contextual adjustment (synergies with current deck, anti-synergies)
    - Recommendation: pick or skip
    - Reasoning
    """
```

### Data Schema (`mcp/data/cards.json`)

```jsonc
{
  "strike_r": {
    "name": "Strike",
    "character": "Ironclad",    // or "Colorless", "Curse", etc.
    "type": "Attack",
    "rarity": "Basic",
    "cost": 1,
    "base_damage": 6,
    "base_block": 0,
    "hits": 1,
    "target": "single",
    "description": "Deal 6 damage.",
    "upgraded": {
      "name": "Strike+",
      "base_damage": 9,
      "description": "Deal 9 damage."
    },
    "keywords": ["starter"],
    "tier": "D",               // General power rating
    "synergy_tags": ["basic"],
    "archetypes": [],
    "notes": "Remove ASAP. Basic attack with no scaling."
  },
  "bash": {
    "name": "Bash",
    "character": "Ironclad",
    "type": "Attack",
    "rarity": "Basic",
    "cost": 2,
    "base_damage": 8,
    "base_block": 0,
    "hits": 1,
    "target": "single",
    "description": "Deal 8 damage. Apply 2 Vulnerable.",
    "effects": [
      {"type": "damage", "value": 8, "target": "single"},
      {"type": "apply_status", "status": "vulnerable", "value": 2, "target": "single"}
    ],
    "upgraded": {
      "base_damage": 10,
      "effects": [
        {"type": "damage", "value": 10, "target": "single"},
        {"type": "apply_status", "status": "vulnerable", "value": 3, "target": "single"}
      ]
    },
    "keywords": ["starter", "vulnerable"],
    "tier": "B",
    "synergy_tags": ["vulnerable", "frontload_damage"],
    "archetypes": ["strength"],
    "notes": "Good early-game. Upgrade priority is medium."
  }
  // ... more cards
}
```

### Data Schema (`mcp/data/enemies.json`)

```jsonc
{
  "jaw_worm": {
    "name": "Jaw Worm",
    "type": "monster",
    "act": 1,
    "hp_range": [40, 44],   // min-max at Ascension 0
    "moves": {
      "chomp": {"type": "attack", "damage": 11, "description": "Deals 11 damage"},
      "bellow": {"type": "buff", "description": "Gains 3 Strength and 6 Block"},
      "drool": {"type": "debuff_attack", "damage": 7, "description": "Deals 7, applies Wound"}
    },
    "pattern": "Opens with Chomp (~50%) or Bellow. Alternates. Never Bellow twice in a row.",
    "tips": "Kill fast before Strength stacks. Prioritize damage over block on turn 1.",
    "danger_rating": 2  // 1-5 scale
  }
  // ... more enemies
}
```

### Data Schema (`mcp/data/synergies.json`)

```jsonc
{
  "exhaust": {
    "enablers": ["True Grit", "Fiend Fire", "Burning Pact", "Sentinel"],
    "payoffs": ["Feel No Pain", "Dark Embrace", "Barricade+Entrench"],
    "description": "Exhaust synergy: remove cards from deck during combat for benefits"
  },
  "strength": {
    "enablers": ["Inflame", "Demon Form", "Spot Weakness", "Limit Break"],
    "payoffs": ["Heavy Blade", "Sword Boomerang", "Reaper", "Whirlwind"],
    "description": "Strength scaling: stack Strength then use multi-hit or X-cost attacks"
  }
  // ... more synergy archetypes
}
```

### Data Population Strategy

Since STS2 is in early access and card data changes, the wiki data needs a practical population approach:

1. **Bootstrap from game state**: Write a one-time script that calls `get_game_state` across multiple runs to extract card names, costs, descriptions, and types
2. **Manual enrichment**: Add tier ratings, synergy tags, boss strategies manually based on community knowledge (STS2 subreddit, wikis)
3. **Agent self-learning**: After boss fights, the agent can propose updates to wiki data via a `wiki_suggest_update` tool (writes to a staging file for human review)
4. **Versioned with the repo**: JSON files in `mcp/data/` are git-tracked, easy to diff and merge

---

## 5. Tool #3: Deck Analyzer (`deck_analyze`)

### Purpose
Answer: "What's my deck's identity?", "Should I add this card?", "What's my draw probability?"

### MCP Tool Interface

```python
@mcp.tool()
async def deck_analyze(
    analysis_type: str = "full"
    # Options: "full", "composition", "draw_probability", "archetype", "weakness"
) -> str:
    """
    Analyze current deck. Automatically fetches deck state from game.

    - composition: card count by type (Attack/Skill/Power), cost curve,
                   upgraded count, average cost, deck size assessment
    - draw_probability: chance of drawing key cards in opening hand (5 cards),
                        chance of seeing specific card within N turns
    - archetype: detected archetype(s) and completion percentage
                 (e.g. "Strength 60% — have Inflame, Spot Weakness, need Heavy Blade")
    - weakness: identified gaps (e.g. "no AoE", "no scaling", "too many 2-cost cards")
    - full: all of the above
    """

@mcp.tool()
async def deck_sim_add(
    card_name: str     # The card being considered
) -> str:
    """
    Simulate adding a card to current deck and report impact:
    - New composition stats vs current
    - Archetype fit delta (does it push toward or away from an archetype?)
    - Average cost change
    - Draw probability impact on key cards
    - Verdict: "Strongly take" / "Take" / "Skip" / "Strongly skip"
    """

@mcp.tool()
async def deck_draw_probability(
    card_names: list[str],  # Cards to check
    turns: int = 1          # Within how many turns
) -> str:
    """
    Calculate probability of drawing specific card(s) within N turns.
    Uses hypergeometric distribution on current draw pile composition.

    Returns per-card:
    - P(draw in opening hand of 5)
    - P(draw within 2 turns = 10 cards)
    - P(draw within 3 turns = 15 cards)
    - Copies in deck / draw pile / discard
    """
```

### Core Computation Module (`mcp/tools/deck_analyzer.py`)

```python
from math import comb  # for hypergeometric distribution

class DeckAnalyzer:
    def __init__(self, game_state: dict):
        """
        Build deck model from game state.
        In combat: use draw_pile + discard_pile + hand + exhaust_pile.
        Out of combat: use full deck from player data.
        """

    def composition(self) -> dict:
        """
        Returns:
        {
            "total_cards": 28,
            "by_type": {"Attack": 12, "Skill": 10, "Power": 4, "Status": 1, "Curse": 1},
            "by_cost": {0: 3, 1: 14, 2: 7, 3: 3, "X": 1},
            "average_cost": 1.43,
            "upgraded_count": 6,
            "upgraded_pct": 21.4,
            "assessment": "Slightly bloated (28 cards). Consider removing Strikes."
        }
        """

    def detect_archetypes(self, wiki_data: dict) -> list[dict]:
        """
        Cross-reference deck cards against synergy definitions.
        Returns ranked archetypes with completion %.
        """

    def draw_probability(self, card_name: str, draw_count: int = 5) -> float:
        """
        Hypergeometric: P(at least 1 copy in draw_count cards).
        P = 1 - C(N-K, n) / C(N, n)
        where N=draw_pile_size, K=copies_in_draw_pile, n=draw_count
        """

    def identify_weaknesses(self, wiki_data: dict) -> list[str]:
        """
        Check for common gaps:
        - No AoE (all attacks are single-target)
        - No scaling (no Strength/Dex gain, no Powers)
        - No card draw (risk of bricking)
        - Too expensive (avg cost > 1.8)
        - No status removal (Curses/Statuses with no way to exhaust)
        """
```

---

## 6. File Structure

```
mcp/
├── server.py                # Existing — add imports for new tools
├── pyproject.toml           # Add no new deps (only stdlib + existing httpx)
│
├── tools/                   # NEW directory
│   ├── __init__.py
│   ├── combat_calc.py       # Combat simulator
│   ├── wiki.py              # Wiki lookup engine
│   ├── deck_analyzer.py     # Deck analysis + probability
│   └── helpers.py           # Shared: fetch game state, parse status effects
│
└── data/                    # NEW directory — static game knowledge
    ├── README.md            # Data format docs, how to contribute
    ├── cards/
    │   ├── ironclad.json    # Per-character card data
    │   ├── silent.json
    │   ├── defect.json
    │   ├── watcher.json
    │   ├── regent.json      # STS2 new character
    │   └── colorless.json
    ├── relics.json
    ├── enemies.json
    ├── bosses.json
    ├── synergies.json
    └── keywords.json        # Game keyword definitions
```

---

## 7. Integration with Existing `server.py`

Minimal changes to `server.py` — just import and register:

```python
# At top of server.py, add:
from tools.combat_calc import register_combat_tools
from tools.wiki import register_wiki_tools
from tools.deck_analyzer import register_deck_tools

# After mcp = FastMCP("sts2"), add:
register_combat_tools(mcp, _sp_url)
register_wiki_tools(mcp)
register_deck_tools(mcp, _sp_url)
```

Each module's `register_*_tools(mcp, ...)` function calls `@mcp.tool()` internally, keeping `server.py` clean.

---

## 8. Agent Prompt Updates

Update `AGENTS.md` to teach the agent WHEN to use each tool:

```markdown
## AI Toolkit Usage

### Before every combat turn:
1. Call `combat_calc` with your planned card sequence to verify damage numbers
2. If `can_kill_all` is true, skip blocking entirely — go all offense
3. If not, call `combat_calc` again with a defensive sequence to compare

### When choosing card rewards:
1. Call `wiki_rate_card_reward` with the 3 options — it auto-checks your deck
2. If verdict is "Skip", skip without hesitation
3. Trust tier + context rating over instinct

### When entering a new act or after a boss:
1. Call `deck_analyze(analysis_type="full")` to review deck health
2. Address any weaknesses identified before the next boss

### When encountering an unknown enemy/boss:
1. Call `wiki_boss_guide` or `wiki_lookup` for move patterns
2. Plan first 2-3 turns based on known opener patterns

### When entering a shop:
1. Call `deck_analyze(analysis_type="weakness")` to know what to buy
2. Prioritize filling identified gaps over "good in a vacuum" cards
```

---

## 9. Implementation Priority & Effort Estimate

| Phase | Tool | Effort | Impact on Win-Rate |
|-------|------|--------|--------------------|
| **Phase 1** | `combat_calc` (basic: damage/block math) | 2-3 hours | **High** — eliminates arithmetic errors |
| **Phase 1** | `deck_analyze` (composition + draw probability) | 2-3 hours | **Medium** — better card reward decisions |
| **Phase 2** | `wiki_lookup` (cards + relics + keywords) | 4-6 hours (mostly data entry) | **Medium** — contextual decisions |
| **Phase 2** | `wiki_rate_card_reward` | 1-2 hours (builds on wiki data) | **High** — card selection is #1 run factor |
| **Phase 3** | `wiki_boss_guide` | 2-3 hours (data entry) | **Medium** — prevents boss-fight mistakes |
| **Phase 3** | `deck_sim_add` | 1-2 hours (builds on analyzer) | **Medium** — shop/reward optimization |
| **Phase 3** | Agent prompt overhaul | 1-2 hours | **High** — teaches agent to use tools |

**Phase 1 total: ~5 hours → biggest bang for buck**

---

## 10. Open Questions / Future Extensions

1. **STS2 character coverage**: The current STS2 early access may have different characters than STS1. Wiki data should start with whatever character the agent is playing, then expand.

2. **Card data extraction automation**: Could we add a C# endpoint to the mod that dumps all card/relic definitions from the game's data files? This would auto-populate `mcp/data/` without manual entry. (Low priority — manual entry is fine for Phase 1.)

3. **Multi-turn planning**: The combat calculator currently simulates one turn. A future extension could simulate 2-3 turns ahead (factoring in draw pile probabilities and enemy move patterns) for boss fights.

4. **Path planning on map**: A `map_evaluate_path` tool that scores different paths through the map based on current HP, deck strength, and upcoming encounters. This is complex but very high value.

5. **Run history / meta-learning**: Store win/loss data with deck snapshots across runs. Over time, build a statistical model of which cards/strategies correlate with wins. This is the "long game" improvement.

---

## 11. Getting Started — Phase 1 Checklist

```
[ ] Create mcp/tools/ directory with __init__.py
[ ] Implement mcp/tools/helpers.py (shared game state fetcher)
[ ] Implement mcp/tools/combat_calc.py (damage/block simulation)
[ ] Implement mcp/tools/deck_analyzer.py (composition + probability)
[ ] Register tools in server.py
[ ] Create mcp/data/ with placeholder card data for one character
[ ] Update AGENTS.md with tool usage instructions
[ ] Test: run a combat encounter with agent using new tools
[ ] Iterate: fix edge cases found during play
```
