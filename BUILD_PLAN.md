# STS2 Local Agent — Build Plan

> 从零搭建一个本地 LLM agent 来玩杀戮尖塔2
> 适用于：有 LM Studio 经验但没写过 agent 的开发者
> 硬件：RTX 4090 + Ollama

---

## 你需要理解的核心概念

一个 agent 本质上就是一个 **while 循环**：

```
while 游戏没结束:
    1. 看一眼游戏状态（GET 请求）
    2. 把状态发给 LLM，问"下一步干什么？"
    3. LLM 回复一个 tool call（比如 "play_card(0, target='jaw_worm_0')"）
    4. 你的代码解析这个 tool call，发 POST 请求给游戏执行
    5. 把执行结果告诉 LLM
    6. 回到第 1 步
```

就这么简单。没有框架、没有 MCP 协议、没有魔法。你写的是 **中间人**——
左手跟 LLM 对话，右手操控游戏。

---

## 前置准备

```bash
# 1. 安装 Ollama（如果还没装）
# Windows: 去 https://ollama.com 下载安装包

# 2. 拉模型
ollama pull qwen3.5:27b     # 主力模型
ollama pull phi4:14b          # 对比实验用
ollama pull glm4:9b           # 对比实验用

# 3. 启动 Ollama 服务（安装后自动启动，但确认一下）
ollama serve
# API 在 http://localhost:11434/v1

# 4. 创建项目目录
mkdir E:\SlayTheSpire2Agent\local-agent
cd E:\SlayTheSpire2Agent\local-agent

# 5. Python 环境
python -m venv .venv
.venv\Scripts\activate
pip install httpx openai    # 只需要这两个库
```

---

## Phase 0: 先确认两端都通（30 分钟）

在写 agent 之前，分别测试 LLM 和游戏 API 是否正常。

### 测试 Ollama

```python
# test_llm.py
from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

response = client.chat.completions.create(
    model="qwen3.5:27b",
    messages=[{"role": "user", "content": "说一句话证明你能用中文回复"}],
)
print(response.choices[0].message.content)
```

### 测试游戏 API

```python
# test_game.py
import httpx

r = httpx.get("http://localhost:15526/api/v1/singleplayer", params={"format": "markdown"})
print(r.text[:500])  # 看到游戏状态就说明 mod 在工作
```

### 测试 Tool Calling

这是最关键的测试——确认模型能正确输出 function call 格式：

```python
# test_tool_call.py
from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

tools = [
    {
        "type": "function",
        "function": {
            "name": "play_card",
            "description": "Play a card from hand",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_index": {"type": "integer", "description": "Card index in hand"},
                    "target": {"type": "string", "description": "Target entity_id"},
                },
                "required": ["card_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_turn",
            "description": "End the current turn",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

response = client.chat.completions.create(
    model="qwen3.5:27b",
    messages=[
        {"role": "system", "content": "You are playing Slay the Spire 2. Use tools to take actions."},
        {"role": "user", "content": "Your hand: [0] Strike (1 energy) [1] Defend (1 energy). Enemy: Jaw Worm, 42 HP. You have 3 energy. Play your turn."},
    ],
    tools=tools,
)

msg = response.choices[0].message
if msg.tool_calls:
    for tc in msg.tool_calls:
        print(f"Tool: {tc.function.name}, Args: {tc.function.arguments}")
else:
    print(f"No tool call! Response: {msg.content}")
```

如果这三个都通过，你就可以开始写 agent 了。

---

## Phase 1: 最小可运行 Agent（2-3 小时）

目标：能走完一场普通怪物战斗。

### 文件结构

```
local-agent/
├── agent.py          # 主循环（~200 行）
├── game_api.py       # 游戏 HTTP 调用封装（~80 行）
├── tools.py          # Tool 定义（~100 行）
├── prompts.py        # System prompt（~50 行）
└── config.py         # 配置（模型名、URL 等）
```

### 核心架构

agent.py 的主循环只做 5 件事：

```python
# agent.py 伪代码
while True:
    # 1. 获取游戏状态
    state = game_api.get_state()

    # 2. 根据状态类型选择工具集
    tools = tools.get_tools_for_state(state["state_type"])

    # 3. 问 LLM 该干什么
    response = llm.chat(
        system_prompt + state_description,
        tools=tools,
        conversation_history
    )

    # 4. 解析并执行 tool call
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = game_api.execute(tool_call.name, tool_call.args)
            # 5. 把结果加到对话历史
            conversation_history.append(tool_result)
    else:
        # LLM 没有调工具，可能是在思考或出错了
        handle_no_tool_call(response)
```

### Tool 定义策略

不要一次暴露 30 个 tool 给 LLM——根据当前游戏状态只给相关的：

```
state_type = "monster"  → [play_card, end_turn, use_potion]
state_type = "map"      → [choose_map_node]
state_type = "rewards"  → [claim_reward, skip_card, proceed]
state_type = "rest_site" → [choose_rest_option, proceed]
state_type = "shop"     → [shop_purchase, proceed]
state_type = "event"    → [choose_event_option, advance_dialogue]
```

这样 LLM 不会在战斗中尝试 `choose_map_node`，也不会在地图上尝试 `play_card`。

---

## Phase 2: 完整游戏循环（2-3 小时）

在 Phase 1 基础上加入所有非战斗场景的处理。

### 新增内容
- 地图选择逻辑
- 奖励领取逻辑
- 休息点逻辑
- 商店逻辑
- 事件逻辑
- 卡牌选择覆层逻辑

### 关键：状态机思维

```
                    ┌──────────┐
                    │   Map    │◄──────────────────┐
                    └────┬─────┘                    │
                         │ choose_node              │ proceed
              ┌──────────┼──────────┐               │
              ▼          ▼          ▼               │
         ┌────────┐ ┌────────┐ ┌────────┐          │
         │Monster │ │ Event  │ │  Shop  │──────────┤
         │Combat  │ │        │ │        │          │
         └───┬────┘ └───┬────┘ └────────┘          │
             │          │                           │
             ▼          │                           │
        ┌────────┐      │                           │
        │Rewards │──────┴───────────────────────────┘
        └────────┘
```

你的 agent 就是在这些状态之间跳转，每种状态有不同的 tool 集合。

---

## Phase 3: 观察与调试（持续）

这是最有价值的部分——观察不同模型如何"思考"。

### 日志系统

每次 LLM 调用都记录：

```python
{
    "timestamp": "2026-03-23T19:30:00",
    "model": "qwen3.5:27b",
    "state_type": "monster",
    "state_summary": "Round 2, Player 65/80 HP, Jaw Worm 28/42 HP",
    "prompt_tokens": 1200,
    "completion_tokens": 85,
    "tool_calls": [{"name": "play_card", "args": {"card_index": 2, "target": "jaw_worm_0"}}],
    "thinking": "<think>敌人意图攻击11点伤害，我有3能量...</think>",  # Qwen3 思考过程
    "latency_ms": 2400
}
```

### A/B 测试框架

```python
# 配置文件切换模型
MODELS = {
    "qwen3.5-27b": {"model": "qwen3.5:27b", "temperature": 0.3},
    "phi4-14b":     {"model": "phi4:14b",     "temperature": 0.3},
    "glm4-9b":      {"model": "glm4:9b",      "temperature": 0.3},
}

# 同一场战斗，不同模型各跑一次，对比日志
```

### 你会观察到的有趣差异

- Qwen3.5 的 `<think>` 块会暴露完整的推理链——你能看到它如何算伤害、权衡攻防
- Phi-4 可能数学算得更准但 tool call 格式偶尔出错
- GLM-4 小模型在简单战斗中可能表现不错，但复杂 boss 战会明显力不从心
- 不同模型对 "skip card reward" 的决策逻辑会很不一样

---

## Phase 4: 加入辅助工具（可选，Phase 1-3 完成后）

这就是我们之前设计的 toolkit——combat_calc、deck_analyzer、wiki。
Phase 1-3 不需要它们也能跑，但加入后可以显著提升表现。

把它们定义为额外的 tool 注册给 LLM：

```python
# 辅助工具不调游戏 API，而是在 Python 里本地计算
tools_combat = [
    play_card_tool,
    end_turn_tool,
    use_potion_tool,
    combat_calc_tool,      # NEW: 本地计算伤害
    combat_can_kill_tool,  # NEW: 本地判断能否击杀
]
```

LLM 调 `combat_calc` → 你的 Python 代码本地算 → 返回结果给 LLM →
LLM 根据结果决定打哪些牌。

---

## 常见坑 & 解决方案

### 坑 1: LLM 不调工具，直接说话
- 原因：system prompt 不够强，或模型把 tool call 当成可选
- 解方：在 system prompt 里加 "You MUST use a tool on every turn. Never respond with plain text."

### 坑 2: LLM 调了不存在的工具
- 原因：幻觉，或者记住了之前看到的工具名
- 解方：解析 tool call 时做白名单验证，调不存在的就返回错误让它重试

### 坑 3: LLM 参数格式错误（比如 card_index 传了字符串）
- 原因：小模型 JSON 格式不稳定
- 解方：用 try/except 包裹参数解析，出错就构造友好的错误信息返回

### 坑 4: 战斗中循环卡死（同一张牌打不出去）
- 原因：牌打不出去（能量不够、不能 target），但 LLM 不看错误信息
- 解方：连续 3 次相同的错误就强制 end_turn

### 坑 5: 对话历史太长，context 爆了
- 原因：每轮都加入完整游戏状态，context 很快超过 32K
- 解方：每场新战斗清空历史；战斗中只保留最近 5 轮对话

### 坑 6: Ollama 的 tool call 返回格式跟 OpenAI 不完全一致
- 原因：Ollama 的 OpenAI 兼容层有时候把 arguments 当 string 返回而不是 dict
- 解方：统一做一层解析——如果 arguments 是 string 就 json.loads 它

---

## 运行清单

```
[ ] Phase 0: Ollama 安装并拉取模型
[ ] Phase 0: 三个测试脚本全部通过
[ ] Phase 1: agent.py 主循环能跑通一场战斗
[ ] Phase 1: 日志能记录每次 LLM 调用
[ ] Phase 2: 能完整走完 Act 1 的一条路径
[ ] Phase 2: 处理所有 state_type（包括 event、shop 等）
[ ] Phase 3: 用两个以上模型跑同一局，对比日志
[ ] Phase 4: （可选）接入 combat_calc 工具
```
