"""Phase 0: Verify LLM + Game API + Tool Calling all work.

Run this BEFORE starting the agent:
    python test_setup.py
"""

import json
import sys
from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, ACTIVE_MODEL, GAME_API_URL


def test_llm():
    """Test 1: Can we talk to the LLM?"""
    print(f"\n[Test 1] Connecting to LLM at {OLLAMA_BASE_URL} (model: {ACTIVE_MODEL})...")
    try:
        from openai import OpenAI
        client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
        response = client.chat.completions.create(
            model=ACTIVE_MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: LLM_OK"}],
            max_tokens=20,
        )
        text = response.choices[0].message.content.strip()
        if text:
            print(f"  ✓ LLM works! Response: {text}")
            return True
        else:
            print(f"  ✗ LLM returned empty response")
            return False
    except Exception as e:
        print(f"  ✗ LLM failed: {e}")
        print(f"    → Is KoboldCPP running at {OLLAMA_BASE_URL}?")
        return False


def test_game_api():
    """Test 2: Can we reach the STS2 mod?"""
    print(f"\n[Test 2] Connecting to STS2 game mod at {GAME_API_URL}...")
    try:
        import httpx
        r = httpx.get(GAME_API_URL, params={"format": "json"}, timeout=5)
        data = r.json()
        state = data.get("state_type", "?")
        print(f"  ✓ Game API works! State: {state}")
        if state == "menu":
            print("    (Start a run in STS2 to continue)")
        return True
    except Exception as e:
        print(f"  ✗ Game API failed: {e}")
        print("    → Is STS2 running with the mod enabled?")
        return False


def test_tool_calling():
    """Test 3: Can the model produce valid tool calls?"""
    print(f"\n[Test 3] Testing tool calling with {ACTIVE_MODEL}...")
    try:
        from openai import OpenAI
        client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)

        tools = [{
            "type": "function",
            "function": {
                "name": "play_card",
                "description": "Play a card from hand",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_index": {"type": "integer"},
                        "target": {"type": "string"},
                    },
                    "required": ["card_index"],
                },
            },
        }, {
            "type": "function",
            "function": {
                "name": "end_turn",
                "description": "End current turn",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

        response = client.chat.completions.create(
            model=ACTIVE_MODEL,
            messages=[
                {"role": "system", "content": "You are playing a card game. You MUST use a tool."},
                {"role": "user", "content": "Hand: [0] Strike (1 energy, deals 6 damage). Enemy: Slime, 12 HP. Energy: 3. Play a card."},
            ],
            tools=tools,
            temperature=0.1,
        )

        msg = response.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                print(f"  ✓ Tool call works! → {tc.function.name}({args})")
            return True
        else:
            print(f"  ✗ No tool call produced. Response: {msg.content[:200]}")
            print("    → Model may need different prompting for tool calls")
            return False

    except Exception as e:
        print(f"  ✗ Tool calling test failed: {e}")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("  STS2 Agent — Setup Verification")
    print("=" * 50)

    results = {
        "LLM": test_llm(),
        "Game API": test_game_api(),
        "Tool Calling": test_tool_calling(),
    }

    print("\n" + "=" * 50)
    print("  Results")
    print("=" * 50)
    all_pass = True
    for name, ok in results.items():
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name}")
        if not ok:
            all_pass = False

    if all_pass:
        print("\n  All tests passed! Run the agent with:")
        print("    python agent.py")
    else:
        print("\n  Some tests failed. Fix the issues above before running the agent.")

    sys.exit(0 if all_pass else 1)
