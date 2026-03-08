"""
Infrastructure — Playwright browser lifecycle management.
"""

import logging
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    Playwright,
)

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Manages a single headless Chromium browser instance.
    One shared instance is created per MCP server lifespan and injected
    into all infrastructure adapters.
    """

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def start(self) -> None:
        """Launch the browser. Must be called before accessing .page."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        logger.debug("Browser started.")

    async def stop(self) -> None:
        """Close the browser and release Playwright resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.debug("Browser stopped.")

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        return self._page
