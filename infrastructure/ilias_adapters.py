"""
Infrastructure — concrete Playwright implementations of the domain ports.

Three adapters, each responsible for exactly one bounded context:
  IliasAuthAdapter   — SSO login via Switch edu-ID
  IliasCourseAdapter — reading courses from the ILIAS dashboard
  IliasFileAdapter   — listing and downloading files from ILIAS
"""

import logging
import re
from pathlib import Path

from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from domain.models import ContentItem, Course, CourseFile, LoginCredentials, RefId, VideoItem
from domain.ports import IAuthPort, ICoursePort, IFilePort
from domain.utils import sanitize_filename
from infrastructure.browser import BrowserManager

logger = logging.getLogger(__name__)

# Hostname of the Switch edu-ID login service
_EDU_ID_HOST = "login.eduid.ch"


# ---------------------------------------------------------------------------
# Auth adapter
# ---------------------------------------------------------------------------

class IliasAuthAdapter(IAuthPort):
    """
    Drives the Switch edu-ID SSO login flow through a headless browser.

    Confirmed selectors (ilias.unibe.ch, ILIAS v9.17 / unibe.login.eduid.ch):
      ILIAS page:   select#user_idp, input#wayf_submit_button
      edu-ID page:  input#username (name=j_username), button#button-submit
    """

    def __init__(self, browser: BrowserManager) -> None:
        self._browser = browser

    async def login(self, credentials: LoginCredentials) -> None:
        """
        Full login flow:
          1. ILIAS /login.php  →  select institution  →  click Fortfahren
          2. edu-ID page       →  email  →  Weiter  →  password  →  Anmelden
          3. Consent page      →  accept if present
          4. Verify redirect back to ILIAS
        """
        page = self._browser.page

        # Step 1 – institution selection
        await page.goto(f"{credentials.base_url.rstrip('/')}/login.php")
        await page.wait_for_load_state("networkidle")
        await self._select_institution(page, credentials)
        await page.wait_for_load_state("networkidle")

        # Step 2 – edu-ID credentials
        if _EDU_ID_HOST not in page.url:
            raise RuntimeError(
                f"Expected to land on '{_EDU_ID_HOST}' after institution selection, "
                f"but got: {page.url}"
            )
        logger.info("On Switch edu-ID login page (%s).", page.url)
        await self._enter_email(page, credentials.email)
        await page.wait_for_load_state("networkidle")
        await self._enter_password(page, credentials.password)
        await page.wait_for_load_state("networkidle")

        # Step 3 – consent
        await self._accept_consent(page)
        await page.wait_for_load_state("networkidle")

        # Step 4 – verify
        if credentials.base_url.rstrip("/") not in page.url:
            raise RuntimeError(
                f"Login failed — not redirected back to ILIAS. "
                f"Current URL: {page.url}"
            )
        logger.info("Login successful.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _select_institution(self, page, credentials: LoginCredentials) -> None:
        try:
            await page.select_option(
                "select#user_idp",
                value=credentials.institution_idp,
                timeout=10_000,
            )
            logger.debug("Selected institution '%s'.", credentials.institution_idp)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"Institution dropdown (select#user_idp) not found on {page.url}"
            ) from exc

        try:
            await page.click("input#wayf_submit_button", timeout=5_000)
            logger.debug("Clicked 'Fortfahren'.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Submit button (input#wayf_submit_button) not found on login page."
            ) from exc

    async def _enter_email(self, page, email: str) -> None:
        try:
            field = page.locator("input#username, input[name='j_username']").first
            await field.wait_for(state="visible", timeout=10_000)
            await field.fill(email)
            logger.debug("E-mail filled.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"E-mail field not found on edu-ID page. URL: {page.url}"
            ) from exc

        try:
            await page.click("button#button-submit", timeout=5_000)
            logger.debug("Clicked 'Weiter'.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("'Weiter' button not found after e-mail field.") from exc

    async def _enter_password(self, page, password: str) -> None:
        try:
            pwd = page.locator("input[type='password']").first
            await pwd.wait_for(state="visible", timeout=10_000)
            await pwd.fill(password)
            logger.debug("Password filled.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"Password field did not appear on edu-ID page. URL: {page.url}"
            ) from exc

        # Try multiple selectors — the password page may differ from the email page
        for selector in [
            "button#button-submit",
            "button[type='submit']",
            "input[type='submit']",
            "button.btn-primary",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=5_000)
                    logger.debug("Clicked submit after password via '%s'.", selector)
                    return
            except PlaywrightTimeoutError:
                logger.debug("Password submit selector '%s' timed out.", selector)

        raise RuntimeError(
            f"No submit button found after password field. URL: {page.url}"
        )

    async def _accept_consent(self, page) -> None:
        for selector in [
            "button:has-text('Accept')",
            "button:has-text('Akzeptieren')",
            "button:has-text('Zustimmen')",
            "input[type=submit][value='Accept']",
            "button:has-text('Continue')",
            "button:has-text('Weiter')",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=5_000)
                    logger.debug("Accepted consent via '%s'.", selector)
                    return
            except PlaywrightTimeoutError:
                logger.debug("Consent selector '%s' timed out.", selector)
            except PlaywrightError as exc:
                logger.debug("Consent selector '%s' error: %s", selector, exc)


# ---------------------------------------------------------------------------
# Course adapter
# ---------------------------------------------------------------------------

class IliasCourseAdapter(ICoursePort):
    """Reads the course list from ILIAS via Arbeitsraum → Aktuelles Semester."""

    def __init__(self, browser: BrowserManager, base_url: str, video_quality: str = "lowest") -> None:
        self._browser = browser
        self._base_url = base_url.rstrip("/")
        # "lowest" → smallest file / lowest resolution; "highest" → best quality
        if video_quality not in ("lowest", "highest"):
            raise ValueError(f"video_quality must be 'lowest' or 'highest', got '{video_quality}'")
        self._video_quality = video_quality

    async def get_courses(self) -> list[Course]:
        page = self._browser.page

        # Step 1 – navigate to ILIAS main page
        await page.goto(f"{self._base_url}/ilias.php")
        await page.wait_for_load_state("networkidle")

        # Step 2 – find "Aktuelles Semester" link and navigate to its href directly
        semester_link = page.locator("a:has-text('Aktuelles Semester')").first
        href = await semester_link.get_attribute("href", timeout=10_000)
        if not href:
            raise RuntimeError(
                f"Navigation item 'Aktuelles Semester' not found. URL: {page.url}"
            )
        target = href if href.startswith("http") else f"{self._base_url}/{href.lstrip('/')}"
        await page.goto(target)
        await page.wait_for_load_state("networkidle")
        logger.info("Navigated to 'Aktuelles Semester'. URL: %s", page.url)

        # Step 4 – extract courses from the current page.
        # Course items are rendered as <button data-action="...&ref_id=..."> inside
        # .il-item-title — not as <a> tags.
        courses: list[Course] = []
        seen: set[str] = set()

        selector = ".il-item-title button[data-action*='ref_id']"
        for btn in await page.locator(selector).all():
            action = await btn.get_attribute("data-action") or ""
            title = (await btn.inner_text()).strip()
            if not action or not title:
                continue
            match = re.search(r"ref_id=(\d+)", action)
            if match:
                ref_id = match.group(1)
                if ref_id not in seen:
                    seen.add(ref_id)
                    full_url = (
                        action if action.startswith("http")
                        else f"{self._base_url}/{action.lstrip('/')}"
                    )
                    courses.append(Course(title=title, ref_id=ref_id, url=full_url))

        logger.info("Found %d course(s) in 'Aktuelles Semester'.", len(courses))
        return courses

    async def list_content(self, ref_id: RefId) -> list[ContentItem]:
        page = self._browser.page
        await page.goto(
            f"{self._base_url}/ilias.php"
            f"?ref_id={ref_id}&cmd=view&baseClass=ilrepositorygui"
        )
        await page.wait_for_load_state("networkidle")

        raw: list[dict] = await page.evaluate("""
            () => {
                const blocks = [...document.querySelectorAll('.ilContainerBlock')];
                const inhaltBlock = blocks.find(b => {
                    const h = b.querySelector('h2, h3');
                    return h && h.textContent.trim().toLowerCase() === 'inhalt';
                });
                if (!inhaltBlock) return [];
                // Collect rows from multiple possible CSS classes used across ILIAS versions
                const rowEls = new Set([
                    ...inhaltBlock.querySelectorAll('.ilObjListRow'),
                    ...inhaltBlock.querySelectorAll('.il-std-item'),
                    ...inhaltBlock.querySelectorAll('.il_ContainerListItem'),
                ]);
                const seen = new Set();
                return [...rowEls].map(row => {
                    const link = row.querySelector(
                        '.il_ContainerItemTitle a, .il-item-title a, a[href*="/go/"], a[href*="ref_id="]'
                    );
                    const icon = row.querySelector('img[alt]');
                    if (!link) return null;
                    const url = link.href;
                    if (seen.has(url)) return null;
                    seen.add(url);
                    return {
                        title: link.textContent.trim(),
                        url,
                        item_type: icon ? icon.alt : ''
                    };
                }).filter(Boolean);
            }
        """)

        items: list[ContentItem] = []
        for entry in raw:
            url: str = entry["url"]
            # URL is either /go/{type}/{ref_id} or contains ref_id= as a query param
            match = re.search(r"/go/\w+/(\d+)", url) or re.search(r"ref_id=(\d+)", url)
            if not match:
                continue
            items.append(ContentItem(
                title=entry["title"],
                ref_id=match.group(1),
                url=url,
                item_type=entry["item_type"],
            ))

        logger.info("Found %d content item(s) for ref_id=%s.", len(items), ref_id)
        return items

    async def list_videos(self, ref_id: RefId) -> list[VideoItem]:
        page = self._browser.page
        await page.goto(
            f"{self._base_url}/ilias.php"
            f"?baseClass=ilObjPluginDispatchGUI&cmd=forward"
            f"&ref_id={ref_id}&forwardCmd=showContent"
        )
        await page.wait_for_load_state("networkidle")

        raw: list[dict] = await page.evaluate("""
            () => {
                const rows = [...document.querySelectorAll('table[id*="tbl_xoct"] tbody tr')];
                return rows.map(tr => {
                    const cells = [...tr.querySelectorAll('td')];
                    const eidEl = tr.querySelector('[data-id]');
                    const playEl = tr.querySelector('a[href*="streamVideo"]');
                    // Both video and subtitle links use cmd=download — only distinguishable
                    // by which dropdown button they belong to ("Download" vs "Untertitel").
                    let dlLinks = [];
                    let subtitleUrl = '';
                    for (const dd of tr.querySelectorAll('div.dropdown')) {
                        const btn = dd.querySelector('button.dropdown-toggle');
                        if (!btn) continue;
                        const btnText = btn.textContent.trim();
                        const links = [...dd.querySelectorAll('a[href*="cmd=download"]')];
                        if (btnText.startsWith('Download')) {
                            dlLinks = links;
                        } else if (btnText.startsWith('Untertitel')) {
                            subtitleUrl = links.length > 0 ? links[0].href : '';
                        }
                    }
                    return {
                        title: cells[2] ? cells[2].textContent.trim() : '',
                        event_id: eidEl ? eidEl.dataset.id : '',
                        date: cells[6] ? cells[6].textContent.trim() : '',
                        url: playEl ? playEl.href : '',
                        download_urls: dlLinks.map(a => a.href),
                        subtitle_url: subtitleUrl
                    };
                }).filter(r => r.title && r.url);
            }
        """)

        videos = []
        for r in raw:
            urls: list[str] = r["download_urls"]
            if self._video_quality == "lowest":
                download_url = urls[-1] if urls else ""
            else:  # "highest"
                download_url = urls[0] if urls else ""
            videos.append(VideoItem(
                title=r["title"],
                event_id=r["event_id"],
                date=r["date"],
                url=r["url"],
                download_url=download_url,
                subtitle_url=r.get("subtitle_url", ""),
            ))
        logger.info("Found %d video(s) for ref_id=%s.", len(videos), ref_id)
        return videos


# ---------------------------------------------------------------------------
# File adapter
# ---------------------------------------------------------------------------

class IliasFileAdapter(IFilePort):
    """Lists and downloads files from ILIAS course containers."""

    def __init__(self, browser: BrowserManager, base_url: str, max_filename_len: int = 64) -> None:
        self._browser = browser
        self._base_url = base_url.rstrip("/")
        self._max_filename_len = max_filename_len

    async def list_files(self, ref_id: RefId) -> list[CourseFile]:
        files: list[CourseFile] = []
        seen_urls: set[str] = set()
        await self._collect(str(ref_id), files, set(), seen_urls)
        return files

    async def download(self, file: CourseFile, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        page = self._browser.page
        async with page.expect_download(timeout=7_200_000) as dl_info:
            try:
                await page.goto(file.url)
            except PlaywrightError as exc:
                # Playwright raises when navigation ends with a download instead of a page load.
                # The download event is still captured — this error is expected and safe to ignore.
                if "Download is starting" not in str(exc):
                    raise
        download = await dl_info.value
        raw_name = file.file_name or download.suggested_filename or file.title
        safe_name = sanitize_filename(raw_name, self._max_filename_len)
        save_path = output_dir / safe_name
        await download.save_as(save_path)
        return save_path

    async def download_video(self, video: VideoItem, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        if not video.download_url:
            raise ValueError(f"Video '{video.title}' has no download URL.")
        page = self._browser.page
        async with page.expect_download(timeout=7_200_000) as dl_info:
            try:
                await page.goto(video.download_url)
            except PlaywrightError as exc:
                if "Download is starting" not in str(exc):
                    raise
        download = await dl_info.value
        # Build filename from title + date; fall back to suggested filename
        if video.title:
            safe_title = re.sub(r'[<>:"/\\|?*]', "_", video.title)
            date_part = f"_{video.date}" if video.date else ""
            suffix = Path(download.suggested_filename).suffix or ".mp4"
            raw_name = f"{safe_title}{date_part}{suffix}"
        else:
            raw_name = download.suggested_filename
        safe_name = sanitize_filename(raw_name, self._max_filename_len)
        save_path = output_dir / safe_name
        await download.save_as(save_path)

        # Download subtitle file if available
        if video.subtitle_url:
            await self._download_subtitle(video, output_dir, save_path)

        return save_path

    async def _download_subtitle(self, video: VideoItem, output_dir: Path, video_path: Path) -> None:
        """Download the subtitle file and save it alongside the video with the same stem."""
        page = self._browser.page
        try:
            async with page.expect_download(timeout=60_000) as dl_info:
                try:
                    await page.goto(video.subtitle_url)
                except PlaywrightError as exc:
                    if "Download is starting" not in str(exc):
                        raise
            download = await dl_info.value
            suffix = Path(download.suggested_filename).suffix or ".srt"
            # Use the same stem as the video file so they pair naturally
            subtitle_path = video_path.with_suffix(suffix)
            await download.save_as(subtitle_path)
            logger.info("Saved subtitle: %s", subtitle_path)
        except (PlaywrightError, OSError):
            logger.warning("Failed to download subtitle for '%s'", video.title, exc_info=True)

    async def get_remote_size(self, url: str) -> int | None:
        try:
            response = await self._browser.page.context.request.head(url)
            cl = response.headers.get("content-length")
            return int(cl) if cl else None
        except (PlaywrightError, OSError, ValueError):
            return None

    async def _collect(self, ref_id: str, files: list, visited: set, seen_urls: set | None = None) -> None:
        if ref_id in visited:
            return
        visited.add(ref_id)
        if seen_urls is None:
            seen_urls = set()

        page = self._browser.page
        await page.goto(
            f"{self._base_url}/ilias.php"
            f"?ref_id={ref_id}&cmd=view&baseClass=ilrepositorygui"
        )
        await page.wait_for_load_state("networkidle")

        # Collect file links with type and filename — deduplicate by normalised URL
        raw_files: list[dict] = await page.evaluate("""
            () => {
                const links = [...document.querySelectorAll(
                    'a[href*="cmd=sendfile"], a[href*="goto.php?target=file_"]'
                )];
                return links.map(a => {
                    const row = a.closest('.ilObjListRow, .il_ContainerListItem, li, tr')
                        || a.parentElement;
                    // First il_ItemProperty holds type (e.g. "pdf"), second holds size
                    const props = row
                        ? [...row.querySelectorAll('.il_ItemProperty, .ilItemProperty')]
                        : [];
                    const file_type = props[0] ? props[0].textContent.trim().toLowerCase() : '';
                    // File name from the download link in the action dropdown, or derive from title+type
                    const dlLink = row
                        ? row.querySelector('a[href*="cmd=sendfile"][download], a[download]')
                        : null;
                    const file_name = dlLink ? dlLink.getAttribute('download') : '';
                    return {
                        title: a.textContent.trim(),
                        url: a.href,
                        file_type,
                        file_name
                    };
                });
            }
        """)
        for entry in raw_files:
            href = entry["url"]
            title = entry["title"]
            if not href or not title:
                continue
            norm = re.sub(r"cmdNode=[^&]*&?", "", href)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)
            # Derive filename from title + type if not found in DOM
            file_name = entry["file_name"] or ""
            if not file_name and entry["file_type"] and title:
                safe = re.sub(r'[<>:"/\\|?*]', "_", title)
                file_name = f"{safe}.{entry['file_type']}"
            files.append(CourseFile(
                title=title,
                url=href,
                file_type=entry["file_type"],
                file_name=file_name,
            ))

        # Collect sub-container ref_ids from classic ilrepositorygui <a> links
        sub_ref_ids: set[str] = set()
        for link in await page.locator(
            "a[href*='ref_id'][href*='baseClass=ilrepositorygui']"
        ).all():
            href = await link.get_attribute("href") or ""
            match = re.search(r"ref_id=(\d+)", href)
            if match and match.group(1) != ref_id:
                sub_ref_ids.add(match.group(1))

        # ILIAS v9 renders INHALT folder links as /go/fold/{ref_id} (no baseClass in URL)
        for link in await page.locator("a[href*='/go/fold/']").all():
            href = await link.get_attribute("href") or ""
            match = re.search(r"/go/fold/(\d+)", href)
            if match:
                sub_ref_ids.add(match.group(1))

        for sub_id in sub_ref_ids:
            await self._collect(sub_id, files, visited, seen_urls)
