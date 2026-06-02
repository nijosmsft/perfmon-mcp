"""Smoke test: importing the server registers all expected tools."""

from __future__ import annotations

import asyncio


def test_server_registers_full_tool_surface():
    from perfmon_mcp import server

    expected = {
        # profiles
        "list_counter_profiles",
        "get_counter_profile",
        # snapshot
        "snapshot_counters",
        "parse_counter_output",
        # capture
        "get_capture_commands",
        "get_capture_instructions",
        # analyze
        "load_log",
        "load_blg",       # v0.1 deprecated alias kept for compatibility
        "load_csv",
        "list_loaded_logs",
        "unload_log",
        "log_info",
        "list_blgs",
        "analyze",
        "get_counter_summary",
        "get_counter_timeline",
        "get_per_queue_summary",
        "compare_logs",
        # evidence
        "get_evidence_status",
        "get_entities",
    }

    async def _names() -> set[str]:
        tools = await server.mcp.list_tools()
        return {t.name for t in tools}

    got = asyncio.run(_names())
    missing = expected - got
    extra = got - expected
    assert not missing, f"Missing tools: {missing}"
    assert not extra, f"Unexpected tools (update test if intentional): {extra}"


def test_server_main_callable():
    from perfmon_mcp import server

    assert callable(server.main)
