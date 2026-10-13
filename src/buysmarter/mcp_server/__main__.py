"""Entry point: ``uv run python -m buysmarter.mcp_server`` (stdio transport)."""

from buysmarter.logsetup import new_run_id, setup_logging
from buysmarter.mcp_server.server import mcp

if __name__ == "__main__":
    setup_logging()
    new_run_id()
    mcp.run(transport="stdio")
