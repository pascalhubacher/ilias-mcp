"""
Interface layer — MCP tools for authentication.
"""

import logging

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def login(ctx: Context[ServerSession, AppContext]) -> str:
        """Login to ILIAS via Switch edu-ID using the credentials from .env."""
        app = app_from_ctx(ctx)
        app.rate_limiter.check("login")
        logger.info("Tool 'login' called.")
        return await app.auth_service.login(app.credentials)
