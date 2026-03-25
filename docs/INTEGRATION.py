# Integration Guide: Wiring AI Toolkit into server.py
#
# Copy the `tools/` and `data/` directories into your `mcp/` folder.
# Then apply these changes to `mcp/server.py`:

# ============================================================
# 1. Add these imports AFTER the existing imports (line ~8):
# ============================================================

# from tools.combat_calc import register_combat_tools
# from tools.wiki import register_wiki_tools
# from tools.deck_analyzer import register_deck_tools


# ============================================================
# 2. Add these lines AFTER `mcp = FastMCP("sts2")` (line ~10):
# ============================================================

# def _get_base_url() -> str:
#     """Closure so tools can resolve the current base URL."""
#     return _base_url
#
# register_combat_tools(mcp, _get_base_url)
# register_wiki_tools(mcp)
# register_deck_tools(mcp, _get_base_url)


# ============================================================
# 3. Final file structure should be:
# ============================================================
#
# mcp/
# ├── server.py            (modified — 6 lines added)
# ├── pyproject.toml       (no changes needed)
# ├── tools/
# │   ├── __init__.py
# │   ├── helpers.py
# │   ├── combat_calc.py
# │   ├── deck_analyzer.py
# │   └── wiki.py
# └── data/
#     ├── keywords.json
#     ├── synergies.json
#     ├── relics.json       (empty, populate as you go)
#     ├── enemies.json      (empty, populate as you go)
#     └── cards/
#         └── (per-character JSON files, populate as you go)


# ============================================================
# 4. Update AGENTS.md — add this section:
# ============================================================
#
# ## AI Toolkit (supplementary tools)
#
# You have access to 3 analytical tools in addition to game actions.
# Use them PROACTIVELY — don't try to do math or recall card data from memory.
#
# ### combat_calc / combat_can_kill
# - Call `combat_can_kill` at the START of every combat turn
# - If lethal: go all offense, skip blocking
# - If not lethal: call `combat_calc` with your planned sequence to verify
# - ALWAYS verify damage math with the calculator, never mental-math
#
# ### deck_analyze / deck_draw_probability
# - Call `deck_analyze("full")` when choosing card rewards
# - Call `deck_draw_probability` when deciding between key cards
# - Check weakness analysis before boss fights
#
# ### wiki_lookup
# - Call for any card/relic/enemy you're unsure about
# - Check synergies when evaluating card rewards
# - Look up boss patterns before boss fights


# ============================================================
# 5. Update .claude/commands/playsts2.md — add to Gameplay Loop:
# ============================================================
#
# - **Combat**: FIRST call `combat_can_kill`. If lethal, play all attacks.
#   If not, call `combat_calc` with planned sequence. Read intents.
# - **Rewards**: Call `deck_analyze` before picking cards. Skip if nothing fits.
# - **Boss Prep**: Call `wiki_lookup` for boss patterns. Call `deck_analyze("weakness")`.
