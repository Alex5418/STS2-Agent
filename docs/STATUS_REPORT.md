# STS2-Agent 阶段性总结报告

> 日期: 2026-04-03
> 作者: AlexWang + Claude

---

## 1. 项目概述

STS2-Agent 是一个用本地 LLM 自主玩 Slay the Spire 2 的 AI agent。通过 [STS2MCP](https://github.com/Gennadiyev/STS2MCP) 的 C# 游戏 mod 暴露的 REST API 与游戏交互，不依赖云端 API。

```
本地 LLM (KoboldCPP / Ollama)
    │ OpenAI-compatible API
    ▼
agent.py (Python 主循环)
    │ HTTP REST API (localhost:15526)
    ▼
STS2MCP C# mod (BepInEx，运行在游戏进程内)
    │
    ▼
Slay the Spire 2
```

---

## 2. 已完成的工作

### 2.1 核心 Agent 循环 (`agent.py`)

- **状态驱动循环**: 每次迭代获取游戏状态 → 根据 `state_type` 选择 prompt 和 tools → 调用 LLM → 解析 tool call → 执行动作
- **支持的游戏状态**: monster/elite/boss (战斗), map (地图), event (事件), shop (商店), rest_site (篝火), combat_rewards/card_reward (奖励), card_select (选牌), relic_select (遗物选择), treasure (宝箱)
- **多后端支持**: CLI 参数 `--backend ollama|koboldcpp` 或 `--url` 快速切换 LLM 后端
- **JSONL 日志**: 每局生成独立日志文件，记录每个动作、推理、时间戳、token 用量

### 2.2 可靠性机制

| 机制 | 说明 |
|------|------|
| 能量守卫 | 客户端校验出牌费用，不足时自动 end_turn，避免 EnergyCostTooHigh 错误 |
| 自动 end_turn | 检测手牌无可出之牌时跳过 LLM 调用，直接结束回合 |
| Text parser fallback | LLM 未返回结构化 tool call 时，从文本中正则提取 (KoboldCPP 兼容) |
| 回合等待 | `is_play_phase` 轮询，等待敌人行动结束再出牌 |
| 循环检测 | 连续失败达到阈值后 force advance |
| 选牌防重复 | 追踪 `selected_card_indices`，防止 toggle 同一张牌导致死循环 |
| 空响应重试 | 清空 history 用干净上下文重试，避免空 assistant 消息污染 |

### 2.3 地图路径规划 (`map_planner.py`)

- Act 1 全自动路径选择（评分系统：避开 Elite，偏好 Rest/Shop）
- Act 2+ 作为 hint 提供给 LLM 参考

### 2.4 战斗数学预计算 (`_compute_combat_hints`)

从游戏状态 JSON 动态解析每张手牌的实际伤害/格挡值（游戏引擎已算好力量、敏捷、虚弱等加成），agent 端额外处理易伤 (Vulnerable) 乘数。

输出示例:
```
COMBAT MATH (pre-computed, trust these numbers):
- Total incoming damage this turn: 14
- Your block: 5 → net damage if no more block: 9
- Hand cards (effective values):
  [0] Defend (cost 1): 5block
  [1] Strike (cost 1): 9dmg
  [2] Bash (cost 2): 12dmg
- Kill thresholds:
  Jaw Worm (JAW_WORM_0): 30HP + 0block = 30 to kill (VULNERABLE: only need 20 raw damage)
```

### 2.5 Reasoning in Tool Calls

所有 tool 定义包含 `reasoning` 参数，LLM 将推理放在 tool call 内部而非 response text 中。解决了 Gemma4 推理文本过长挤掉 tool call 的问题，同时保留了可观测性。

### 2.6 Prompt 体系 (`prompts.py`)

- **系统 prompt**: Turn/Action 术语定义, 核心原则, 能量管理, 药水使用
- **战斗 addendum**: 防御/进攻策略, 多步出牌示例, 格挡持续性提醒
- **事件 addendum**: 选项优先级 (遗物 > 移牌 > 免费资源 > HP 交换), 多步事件处理
- **其他**: 地图/奖励/商店/篝火各有专用 prompt

---

## 3. 已测试的模型

| 模型 | 后端 | Tool Calling | 速度 | 战斗表现 | 备注 |
|------|------|-------------|------|---------|------|
| Qwen3.5-27B (Q4_K_M) | KoboldCPP | 需要 text parser fallback | 慢 (30-50s/动作) | 能打过 Act 1 Boss | `<think>` 标签分离推理 |
| Gemma4-26b | Ollama | 原生支持 | 中等 (5-15s/动作) | 能完成 Act 1 战斗，Boss 前死亡 | 需要 repeat_penalty 控制重复 |

### Gemma4 特有问题及解决方案

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| "Wait, I'll play X" 重复 80+ 次 | 模型重复倾向 + max_tokens 过大 | `repeat_penalty=1.15`, `max_tokens=512` |
| 推理文本挤掉 tool call | 推理和 tool call 在同一输出流 | reasoning 参数移入 tool call |
| 空响应 (finish=length) | 推理写完 512 tokens 未到 tool call | prompt 禁止文本输出 + clean retry |
| 连续空响应卡死 | nudge 消息污染 history | 清空 history 重试替代 nudge |

---

## 4. 当前性能指标 (最新一局 Gemma4)

```
结果: Act 1 第 ~11 层死亡 (未到 Boss)
战斗: 5 场 | 总动作: 103 | 成功率: 91%
HP 曲线: 80 → 67(-13) → 64(-9) → 55(-15) → 27(-34) → 20(-42) → 死亡
自动 end_turn: 25 次 (有效节省 LLM 调用)
空响应: 13 次 (均通过 clean retry 恢复)
```

### 对比 Qwen3.5 历史最佳

```
结果: Act 1 通关 (击败 Boss)
战斗: ~10 场 | 成功率: ~88%
HP 管理: 能到达 Boss，但 Boss 战后低血
```

---

## 5. 已知问题

### 5.1 高优先级

| 问题 | 影响 | 状态 |
|------|------|------|
| 事件后连续调用 proceed 失败 | 每次事件浪费 3-5 步 | 未修复 — 模型不理解事件多步结构 |
| 多怪战斗 HP 崩盘 | 后期 2-3 怪战斗掉 30-40 血 | 未修复 — 缺乏优先级目标选择策略 |
| 卡组构筑无规划 | 拿了不协同的牌 (如无消耗体系拿 Feel No Pain) | 未修复 — 需要 deck analyzer |

### 5.2 中优先级

| 问题 | 影响 | 状态 |
|------|------|------|
| 空响应 (finish=length) | 每场战斗 ~2 次，浪费 15-30 秒 | 部分缓解 — clean retry 有效但治标 |
| Post-boss overlay 未处理 | Boss 后遗物选择可能卡住 | 未修复 — 需加 overlay state |
| 战斗速度慢 | 简单回合仍需 LLM 调用 | 部分改善 — auto_end_turn 省了空手回合 |

### 5.3 低优先级

| 问题 | 影响 |
|------|------|
| `combat_summary` 的 `hp_end` 有时为 "?" | 日志分析不便 |
| BlockedByCardLogic 错误未处理 | 偶发，影响小 |

---

## 6. 计划中的工具链 (来自 `docs/ARCHITECTURE.md`)

```
Phase 1 (高价值, ~5h)           Phase 2 (~6h)              Phase 3 (~5h)
┌─────────────────────┐   ┌──────────────────────┐   ┌────────────────────┐
│ combat_calc (完善)   │   │ wiki_lookup (卡/遗物) │   │ wiki_boss_guide    │
│ - 全卡牌模拟        │   │ wiki_rate_card_reward │   │ deck_sim_add       │
│ - 多回合预测        │   │                      │   │ Agent prompt 重写   │
│                     │   │                      │   │                    │
│ deck_analyze        │   │                      │   │                    │
│ - 组成分析          │   │                      │   │                    │
│ - 抽牌概率          │   │                      │   │                    │
└─────────────────────┘   └──────────────────────┘   └────────────────────┘
```

### 已有草稿代码

- `docs/combat_calc.py` — `CombatSimulator` 类，支持伤害/格挡计算、状态效果、序列模拟
- `docs/deck_analyzer.py` — 概率计算框架
- `docs/wiki.py` — 查询接口定义
- `docs/helpers.py` — 共用工具 (状态获取、intent 解析、手牌查找)

### 与当前 `_compute_combat_hints` 的关系

`_compute_combat_hints` 是轻量内联方案，从卡牌 `description` 动态解析伤害/格挡值。它覆盖了 `combat_calc` Phase 1 的核心价值（消除 LLM 算术错误），但不支持：
- 序列模拟 ("如果我打 A 再打 B 会怎样")
- 特殊卡牌效果 (过牌、消耗、生成牌)
- 多回合规划

---

## 7. 运行历史

共 26 次测试运行：
- **KoboldCPP + Qwen3.5**: 12 次 (2026-03-24 ~ 03-26)，最大日志 106KB
- **Ollama + Gemma4**: 9 次 (2026-04-03)，最大日志 54KB
- 空日志 2 次 (启动即崩溃)

---

## 8. 下一步建议

### 短期 (立即可做)

1. **修复事件 proceed 循环** — 在 agent 端检测 "No proceed button" 后自动尝试 `choose_event_option(0)` 而非继续 proceed
2. **多怪战斗策略** — 在 combat hints 中加入目标优先级建议 (低血怪优先击杀减少受击面)
3. **降低空响应率** — 进一步调整 `max_tokens` 或尝试 Gemma4 的其他量化版本

### 中期 (需要开发)

4. **完善 `combat_calc`** — 从草稿升级为可用模块，支持所有卡牌的序列模拟
5. **卡组构筑指导** — 基础的 archetype 检测 ("你在走力量流，优先拿 X")
6. **Post-boss overlay 处理** — 加入 `overlay` state 支持

### 长期 (架构升级)

7. **规则引擎 + LLM 混合** — 简单回合 (只有 Strike/Defend) 用规则引擎秒算，复杂回合才调 LLM
8. **Wiki 数据库** — 卡牌/遗物/敌人知识库，消除模型的游戏知识盲区
9. **多回合规划** — 结合牌堆信息预测未来 2-3 回合的最优策略

---

## 9. 项目文件结构

```
STS2-Agent/
├── agent.py           # 主循环 (~850 行)
├── config.py          # 模型/游戏/日志配置
├── prompts.py         # 系统 prompt + 状态 addendum
├── tools.py           # OpenAI-format tool 定义 + 路由
├── game_api.py        # REST API HTTP 封装
├── map_planner.py     # Act 1 地图路径规划
├── auto_restart.py    # 崩溃自动重启
├── test_setup.py      # 连通性测试
├── CLAUDE.md          # 项目说明
├── docs/
│   ├── ARCHITECTURE.md    # 工具链架构设计
│   ├── STATUS_REPORT.md   # 本报告
│   ├── github_issues.md   # 已知问题追踪
│   ├── combat_calc.py     # 战斗模拟器 (草稿)
│   ├── deck_analyzer.py   # 牌组分析器 (草稿)
│   ├── wiki.py            # Wiki 查询 (草稿)
│   ├── helpers.py         # 共用工具函数
│   └── INTEGRATION.py     # 集成示例
└── logs/                  # JSONL 运行日志 (26 次运行)
```
