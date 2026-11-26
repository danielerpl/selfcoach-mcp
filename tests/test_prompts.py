import asyncio

from selfcoach_mcp.server import mcp

EXPECTED = {"start", "today", "plan", "week", "log", "progress", "goal", "coach_report"}


def test_all_commands_registered_and_render():
    prompts = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert EXPECTED <= prompts
    for name in EXPECTED:
        result = asyncio.run(mcp.get_prompt(name, {}))
        text = result.messages[0].content.text
        assert len(text) > 100
        assert "Reply in" in text


def test_scheduling_commands_require_consent():
    for name in ("today", "plan", "week"):
        text = asyncio.run(mcp.get_prompt(name, {})).messages[0].content.text
        assert "preview=True" in text
