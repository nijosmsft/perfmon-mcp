"""Perfmon MCP Server.

Provides tools for capturing and analyzing Windows performance
counters (PDH .blg logs). Uses Get-Counter / logman / relog.exe
shellouts and pandas for aggregation.
"""

from perfmon_mcp.app import mcp  # noqa: F401 - re-export for backward compat

# Register all tool modules - each module calls @mcp.tool() on import.
import perfmon_mcp.tools.profiles  # noqa: F401, E402
import perfmon_mcp.tools.snapshot  # noqa: F401, E402
import perfmon_mcp.tools.capture  # noqa: F401, E402
import perfmon_mcp.tools.analyze  # noqa: F401, E402
import perfmon_mcp.tools.network_lenses  # noqa: F401, E402
import perfmon_mcp.tools.evidence  # noqa: F401, E402  - optional evidence-store federation


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
