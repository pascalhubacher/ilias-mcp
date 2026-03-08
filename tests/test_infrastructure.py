"""
Unit tests for infrastructure adapters (IliasAuthAdapter, IliasCourseAdapter, IliasFileAdapter).
All Playwright interactions are mocked — no browser or network access required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from domain.models import Course, CourseFile, LoginCredentials, RefId
from infrastructure.browser import BrowserManager
from infrastructure.ilias_adapters import (
    IliasAuthAdapter,
    IliasCourseAdapter,
    IliasFileAdapter,
)

BASE_URL = "https://ilias.unibe.ch"
INSTITUTION = "https://aai-idp.unibe.ch/idp/shibboleth"
EMAIL = "test@unibe.ch"
PASSWORD = "secret"

CREDENTIALS = LoginCredentials(
    email=EMAIL,
    password=PASSWORD,
    institution_idp=INSTITUTION,
    base_url=BASE_URL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_locator(count: int = 0) -> MagicMock:
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    loc.click = AsyncMock()
    loc.fill = AsyncMock()
    loc.wait_for = AsyncMock()
    loc.first = loc
    return loc


def _make_page(url: str = BASE_URL) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.select_option = AsyncMock()
    page.click = AsyncMock()
    page.evaluate = AsyncMock(return_value=[])
    page.locator = MagicMock(return_value=_make_locator())
    return page


def _make_browser(url: str = BASE_URL) -> BrowserManager:
    browser = MagicMock(spec=BrowserManager)
    browser.page = _make_page(url)
    return browser


# ---------------------------------------------------------------------------
# IliasAuthAdapter
# ---------------------------------------------------------------------------

class TestIliasAuthAdapter:

    def _adapter_with_page(self, url: str = "https://unibe.login.eduid.ch/...") -> tuple:
        browser = _make_browser(url=BASE_URL)
        adapter = IliasAuthAdapter(browser)
        adapter._select_institution = AsyncMock()
        adapter._enter_email = AsyncMock()
        adapter._enter_password = AsyncMock()
        adapter._accept_consent = AsyncMock()

        async def advance_url(*_args, **_kwargs):
            browser.page.url = url

        browser.page.wait_for_load_state = AsyncMock(side_effect=advance_url)
        return adapter, browser

    async def test_successful_login(self):
        adapter, browser = self._adapter_with_page(
            url=f"https://unibe.login.eduid.ch/{BASE_URL}/ilias.php"
        )
        call_count = [0]
        urls = [
            "https://unibe.login.eduid.ch/...",
            "https://unibe.login.eduid.ch/...",
            BASE_URL + "/ilias.php",
            BASE_URL + "/ilias.php",
        ]

        async def advancing_wait(*_args, **_kwargs):
            i = call_count[0]
            browser.page.url = urls[min(i, len(urls) - 1)]
            call_count[0] += 1

        browser.page.wait_for_load_state = AsyncMock(side_effect=advancing_wait)
        await adapter.login(CREDENTIALS)  # must not raise

    async def test_not_on_eduid_raises(self):
        browser = _make_browser(url=BASE_URL + "/some-error")
        adapter = IliasAuthAdapter(browser)
        adapter._select_institution = AsyncMock()

        with pytest.raises(RuntimeError, match="login.eduid.ch"):
            await adapter.login(CREDENTIALS)

    async def test_not_redirected_back_raises(self):
        adapter, browser = self._adapter_with_page(
            url="https://unibe.login.eduid.ch/..."
        )
        call_count = [0]
        urls = [
            "https://unibe.login.eduid.ch/...",
            "https://unibe.login.eduid.ch/...",
            "https://other-site.ch/error",
            "https://other-site.ch/error",
        ]

        async def advancing_wait(*_a, **_k):
            browser.page.url = urls[min(call_count[0], len(urls) - 1)]
            call_count[0] += 1

        browser.page.wait_for_load_state = AsyncMock(side_effect=advancing_wait)

        with pytest.raises(RuntimeError, match="Login failed"):
            await adapter.login(CREDENTIALS)

    async def test_select_institution_calls_correct_selectors(self):
        browser = _make_browser()
        adapter = IliasAuthAdapter(browser)
        await adapter._select_institution(browser.page, CREDENTIALS)
        browser.page.select_option.assert_awaited_once_with(
            "select#user_idp", value=INSTITUTION, timeout=10_000
        )
        browser.page.click.assert_awaited_once_with(
            "input#wayf_submit_button", timeout=5_000
        )

    async def test_select_institution_dropdown_timeout_raises(self):
        browser = _make_browser()
        browser.page.select_option = AsyncMock(
            side_effect=PlaywrightTimeoutError("timeout")
        )
        adapter = IliasAuthAdapter(browser)
        with pytest.raises(RuntimeError, match="select#user_idp"):
            await adapter._select_institution(browser.page, CREDENTIALS)

    async def test_enter_email_fills_and_clicks(self):
        browser = _make_browser()
        field = _make_locator(count=1)
        browser.page.locator = MagicMock(return_value=field)
        adapter = IliasAuthAdapter(browser)
        await adapter._enter_email(browser.page, EMAIL)
        field.wait_for.assert_awaited_once_with(state="visible", timeout=10_000)
        field.fill.assert_awaited_once_with(EMAIL)
        browser.page.click.assert_awaited_once_with("button#button-submit", timeout=5_000)

    async def test_enter_email_field_not_found_raises(self):
        browser = _make_browser()
        field = _make_locator(count=1)
        field.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))
        browser.page.locator = MagicMock(return_value=field)
        adapter = IliasAuthAdapter(browser)
        with pytest.raises(RuntimeError, match="E-mail field"):
            await adapter._enter_email(browser.page, EMAIL)

    async def test_enter_password_fills_and_submits(self):
        browser = _make_browser()
        pwd = _make_locator(count=1)
        browser.page.locator = MagicMock(return_value=pwd)
        adapter = IliasAuthAdapter(browser)
        await adapter._enter_password(browser.page, PASSWORD)
        pwd.wait_for.assert_awaited_once_with(state="visible", timeout=10_000)
        pwd.fill.assert_awaited_once_with(PASSWORD)

    async def test_enter_password_not_visible_raises(self):
        browser = _make_browser()
        pwd = _make_locator(count=1)
        pwd.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))
        browser.page.locator = MagicMock(return_value=pwd)
        adapter = IliasAuthAdapter(browser)
        with pytest.raises(RuntimeError, match="Password field"):
            await adapter._enter_password(browser.page, PASSWORD)

    async def test_accept_consent_no_button_passes(self):
        browser = _make_browser()
        browser.page.locator = MagicMock(return_value=_make_locator(count=0))
        adapter = IliasAuthAdapter(browser)
        await adapter._accept_consent(browser.page)  # must not raise

    async def test_accept_consent_clicks_button(self):
        browser = _make_browser()
        btn = _make_locator(count=1)
        browser.page.locator = MagicMock(return_value=btn)
        adapter = IliasAuthAdapter(browser)
        await adapter._accept_consent(browser.page)
        btn.click.assert_awaited_once_with(timeout=5_000)


# ---------------------------------------------------------------------------
# IliasCourseAdapter
# ---------------------------------------------------------------------------

def _make_course_page(course_buttons: list) -> MagicMock:
    """
    Build a page mock for get_courses():
      - locator("a:has-text('Aktuelles Semester')").first.get_attribute("href") → nav href
      - locator(".il-item-title button[data-action*='ref_id']").all() → course buttons
    """
    # Nav locator: returns a semester link with a data href
    nav_locator = _make_locator(count=1)
    nav_locator.get_attribute = AsyncMock(return_value="ilias.php?semester=FS2026")

    course_locator = MagicMock()
    course_locator.all = AsyncMock(return_value=course_buttons)

    def dispatch(selector: str):
        if "il-item-title" in selector:
            return course_locator
        return nav_locator  # Aktuelles Semester

    page = _make_page()
    page.locator = MagicMock(side_effect=dispatch)
    return page


class TestIliasCourseAdapter:

    async def test_returns_courses(self):
        btn = MagicMock()
        btn.get_attribute = AsyncMock(return_value="ilias.php?ref_id=42&baseClass=ilrepositorygui")
        btn.inner_text = AsyncMock(return_value=" Mathematik I ")

        browser = _make_browser()
        browser.page = _make_course_page([btn])
        adapter = IliasCourseAdapter(browser, BASE_URL)

        courses = await adapter.get_courses()
        assert len(courses) == 1
        assert isinstance(courses[0], Course)
        assert courses[0].ref_id == "42"
        assert courses[0].title == "Mathematik I"

    async def test_deduplicates_ref_ids(self):
        btn = MagicMock()
        btn.get_attribute = AsyncMock(return_value="ilias.php?ref_id=42&baseClass=ilrepositorygui")
        btn.inner_text = AsyncMock(return_value="Kurs A")

        browser = _make_browser()
        browser.page = _make_course_page([btn, btn])
        adapter = IliasCourseAdapter(browser, BASE_URL)

        assert len(await adapter.get_courses()) == 1

    async def test_aktuelles_semester_not_found_raises(self):
        nav_locator = _make_locator(count=1)
        nav_locator.get_attribute = AsyncMock(return_value=None)  # href missing

        page = _make_page()
        page.locator = MagicMock(return_value=nav_locator)

        browser = _make_browser()
        browser.page = page
        adapter = IliasCourseAdapter(browser, BASE_URL)

        with pytest.raises(RuntimeError, match="Aktuelles Semester"):
            await adapter.get_courses()


# ---------------------------------------------------------------------------
# IliasFileAdapter
# ---------------------------------------------------------------------------

class TestIliasFileAdapter:

    async def test_returns_files(self):
        browser = _make_browser()
        adapter = IliasFileAdapter(browser, BASE_URL)

        # _collect uses page.evaluate() to collect file links
        browser.page.evaluate = AsyncMock(return_value=[{
            "title": "Vorlesung01.pdf",
            "url": "https://ilias.unibe.ch/ilias.php?cmd=sendfile&ref_id=99",
            "file_type": "pdf",
            "file_name": "Vorlesung01.pdf",
        }])
        empty_locator = MagicMock()
        empty_locator.all = AsyncMock(return_value=[])
        browser.page.locator = MagicMock(return_value=empty_locator)

        files = await adapter.list_files(RefId("42"))
        assert len(files) == 1
        assert isinstance(files[0], CourseFile)
        assert files[0].title == "Vorlesung01.pdf"

    async def test_cycle_detection_prevents_infinite_loop(self):
        browser = _make_browser()
        adapter = IliasFileAdapter(browser, BASE_URL)

        # No files — evaluate returns empty list
        browser.page.evaluate = AsyncMock(return_value=[])

        # Sub-container link points back to the same ref_id → cycle
        folder_link = MagicMock()
        folder_link.get_attribute = AsyncMock(
            return_value="ilias.php?ref_id=42&baseClass=ilrepositorygui"
        )
        folder_locator = MagicMock()
        folder_locator.all = AsyncMock(return_value=[folder_link])

        empty_locator = MagicMock()
        empty_locator.all = AsyncMock(return_value=[])

        def dispatch(selector):
            return empty_locator if "/go/fold/" in selector else folder_locator

        browser.page.locator = MagicMock(side_effect=dispatch)

        files = await adapter.list_files(RefId("42"))
        assert files == []
