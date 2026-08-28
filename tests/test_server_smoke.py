"""Smoke test: importing the server registers all expected tools."""

from __future__ import annotations

import asyncio
import inspect
import types

import pytest


def test_server_registers_full_tool_surface():
    from perfmon_mcp import server

    expected = {
        # profiles
        "list_counter_profiles",
        "get_counter_profile",
        # snapshot
        "snapshot_counters",
        "parse_counter_output",
        "discover_counter_sets",
        "parse_counter_sets_output",
        "discover_counter_instances",
        "parse_counter_instances_output",
        "discover_nics",
        "parse_nics_output",
        # capture
        "get_capture_commands",
        "get_capture_instructions",
        "get_capture_status",
        "parse_capture_status_output",
        "get_teardown_commands",
        "parse_teardown_output",
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
        "compute_rate_from_counter",
        "get_per_queue_summary",
        "compare_logs",
        # network lenses
        "get_counter_throughput",
        "get_rss_distribution",
        # evidence
        "get_evidence_status",
        "get_entities",
    }

    # Use the in-memory MCP Client so the tool surface is read the same way a
    # real client would, over both fastmcp 2.x and 3.x. (fastmcp 2.x exposes
    # ``get_tools()`` while 3.x adds ``list_tools()`` on the server object; the
    # Client's ``list_tools`` is stable across the supported range.)
    from fastmcp import Client

    async def _names() -> set[str]:
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            return {t.name for t in tools}

    got = asyncio.run(_names())
    missing = expected - got
    extra = got - expected
    assert not missing, f"Missing tools: {missing}"
    assert not extra, f"Unexpected tools (update test if intentional): {extra}"


def test_server_main_callable():
    from perfmon_mcp import server

    assert callable(server.main)


@pytest.mark.parametrize(
    "module_path, attr",
    [
        # Both are real delegation targets: ``load_blg`` calls ``load_log`` and
        # ``get_counter_throughput`` calls ``get_counter_summary``. The shim must
        # leave every ``@mcp.tool()``-decorated function directly callable so
        # that in-process delegation (and the direct-call tests) keep working
        # under FastMCP 2.x, which otherwise replaces them with a non-callable
        # ``FunctionTool``. See #38.
        ("perfmon_mcp.tools.analyze", "load_log"),
        ("perfmon_mcp.tools.analyze", "load_blg"),
        ("perfmon_mcp.tools.analyze", "get_counter_summary"),
        ("perfmon_mcp.tools.network_lenses", "get_counter_throughput"),
    ],
)
def test_tool_shim_leaves_functions_callable(module_path: str, attr: str) -> None:
    """``@mcp.tool()`` must hand back the underlying function (type ``function``,
    callable), not a non-callable ``FunctionTool`` object."""
    import perfmon_mcp.server  # noqa: F401 — ensures all tools are registered

    module = __import__(module_path, fromlist=[attr])
    fn = getattr(module, attr)
    assert isinstance(fn, types.FunctionType), f"{attr} is {type(fn)!r}, not a function"
    assert callable(fn)
    assert inspect.signature(fn) is not None


def test_fastmcp_sourced_from_standalone_package():
    """FastMCP must come from the standalone ``fastmcp`` package (#38).

    mcp SDK v2 removed the bundled ``mcp.server.fastmcp`` module, so the
    app instance must be built from the ``fastmcp`` package instead. The
    ``mcp`` instance is a ``_PerfmonFastMCP`` shim subclass (defined in
    perfmon's own module), so check the *base* class' provenance rather
    than the subclass' own module.
    """
    from fastmcp import FastMCP

    from perfmon_mcp import app
    from perfmon_mcp.app import mcp

    assert isinstance(mcp, FastMCP)

    # The ``FastMCP`` name bound in app.py must resolve to the standalone package.
    assert app.FastMCP.__module__.split(".")[0] == "fastmcp", app.FastMCP.__module__
    assert not app.FastMCP.__module__.startswith("mcp.server.fastmcp")

    # The shim's base class must likewise be the standalone FastMCP.
    base = app._PerfmonFastMCP.__bases__[0]
    assert base.__name__ == "FastMCP"
    assert base.__module__.split(".")[0] == "fastmcp", base.__module__
    assert not base.__module__.startswith("mcp.server.fastmcp")
