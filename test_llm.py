import asyncio

from app import llm


def test_provider_defaults_to_openai_gpt4o_mini():
    assert llm.PROVIDER == "openai"
    assert llm.MODEL == "gpt-4o-mini"


def test_complete_with_tools_returns_message_dict():
    async def fake(system, user, tools, temperature):
        return {"content": "ok", "tool_calls": [
            {"function": {"name": "update_term", "arguments": '{"term":"rent"}'}}]}

    original = llm._route
    llm._route = fake
    try:
        out = asyncio.run(llm.complete_with_tools("sys", "usr", [], 0.1))
    finally:
        llm._route = original
    assert out["content"] == "ok"
    assert out["tool_calls"][0]["function"]["name"] == "update_term"


def test_sarvam_provider_is_still_selectable():
    assert "sarvam" in llm.PROVIDERS


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nllm sound\n")
