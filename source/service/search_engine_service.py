import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from playwright.async_api import  Page, TimeoutError as PlaywrightTimeoutError


# =============================================================================
# Search Engine
# =============================================================================


class SearchEngine(Enum):
    GOOGLE = "google"
    DUCKDUCKGO = "duckduckgo"


@dataclass
class SearchConfig:
    max_retries: int = 3
    search_timeout: int = 10000
    results_wait_time: float = 2.0
    screenshot_on_error: bool = False
    screenshot_dir: str = "./debug_screenshots"


@dataclass
class SearchResult:
    urls: list[str]
    query: str
    engine: SearchEngine
    success: bool
    error: Optional[str] = None


class WebSearcher:
    SEARCH_ENGINE_DOMAINS = frozenset({
        "google.com", "google.co", "gstatic.com", "youtube.com",
        "duckduckgo.com", "improving.duckduckgo.com",
        "accounts.google", "policies.google", "support.google",
        "webcache.googleusercontent", "translate.google",
    })

    GOOGLE_RESULT_SELECTORS = [
        "div.g a[href]",
        "div.yuRUbf a[href]",
        "div[data-sokoban-container] a[href]",
        "a[jsname][href]",
        "h3 a[href]",
        "div#search a[href]",
    ]

    GOOGLE_CONTAINER_SELECTORS = [
        "#search",
        "#rso",
        "div#search",
        "div#rcnt",
    ]

    def __init__(self, page: Page, config: Optional[SearchConfig] = None):
        self._page = page
        self._config = config or SearchConfig()

    async def search(
        self,
        query: str,
        engine: SearchEngine = SearchEngine.DUCKDUCKGO,
    ) -> SearchResult:
        search_method = (
            self._search_duckduckgo
            if engine == SearchEngine.DUCKDUCKGO
            else self._search_google
        )

        for attempt in range(self._config.max_retries):
            try:
                urls = await search_method(query)
                return SearchResult(
                    urls=urls,
                    query=query,
                    engine=engine,
                    success=True,
                )
            except Exception as e:
                if attempt == self._config.max_retries - 1:
                    return SearchResult(
                        urls=[],
                        query=query,
                        engine=engine,
                        success=False,
                        error=str(e),
                    )
                await asyncio.sleep(1)

        return SearchResult(
            urls=[],
            query=query,
            engine=engine,
            success=False,
            error="Max retries exceeded",
        )

    async def _search_duckduckgo(self, query: str) -> list[str]:
        await self._page.goto("https://duckduckgo.com/")

        search_box = self._page.locator('input[name="q"]')
        await search_box.wait_for(timeout=self._config.search_timeout)
        await search_box.fill(query)
        await search_box.press("Enter")

        results_container = self._page.locator("ol.react-results--main")
        await results_container.wait_for(timeout=self._config.search_timeout)

        await asyncio.sleep(self._config.results_wait_time)

        result_links = self._page.locator('article[data-testid="result"] a[href]')
        urls = await self._extract_urls_from_locator(result_links)

        return self._deduplicate_urls(urls)

    async def _search_google(self, query: str) -> list[str]:
        await self._page.goto("https://www.google.com/")

        await self._handle_google_cookie_popup()

        search_box = self._page.locator('input[name="q"]')
        await search_box.wait_for(timeout=self._config.search_timeout)
        await search_box.fill(query)
        await search_box.press("Enter")

        await self._wait_for_google_results()
        await asyncio.sleep(self._config.results_wait_time)

        urls = await self._extract_google_results()

        if not urls:
            urls = await self._fallback_extract_all_links()

        return self._deduplicate_urls(urls)

    async def _handle_google_cookie_popup(self) -> None:
        cookie_button_selectors = [
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
            "button:has-text('Reject all')",
        ]

        for selector in cookie_button_selectors:
            try:
                button = self._page.locator(selector)
                await button.click(timeout=3000)
                return
            except PlaywrightTimeoutError:
                continue

    async def _wait_for_google_results(self) -> bool:
        for selector in self.GOOGLE_CONTAINER_SELECTORS:
            try:
                container = self._page.locator(selector)
                await container.wait_for(timeout=5000)
                return True
            except PlaywrightTimeoutError:
                continue

        if self._config.screenshot_on_error:
            await self._save_debug_screenshot("google_no_results")

        return False

    async def _extract_google_results(self) -> list[str]:
        for selector in self.GOOGLE_RESULT_SELECTORS:
            try:
                locator = self._page.locator(selector)
                count = await locator.count()

                if count > 0:
                    urls = await self._extract_urls_from_locator(locator)
                    if urls:
                        return urls
            except Exception:
                continue

        return []

    async def _fallback_extract_all_links(self) -> list[str]:
        all_links = self._page.locator("a[href]")
        urls = []

        count = await all_links.count()
        for i in range(count):
            try:
                link = all_links.nth(i)
                href = await link.get_attribute("href")
                text = await link.inner_text()

                if (
                    href
                    and href.startswith("http")
                    and not self._is_search_engine_url(href)
                    and text
                    and len(text.strip()) > 3
                ):
                    urls.append(href)
            except Exception:
                continue

        return urls

    async def _extract_urls_from_locator(self, locator) -> list[str]:
        urls = []
        count = await locator.count()

        for i in range(count):
            try:
                element = locator.nth(i)
                href = await element.get_attribute("href")

                if (
                    href
                    and href.startswith("http")
                    and not self._is_search_engine_url(href)
                ):
                    urls.append(href)
            except Exception:
                continue

        return urls

    def _is_search_engine_url(self, url: str) -> bool:
        url_lower = url.lower()
        return any(domain in url_lower for domain in self.SEARCH_ENGINE_DOMAINS)

    def _deduplicate_urls(self, urls: list[str]) -> list[str]:
        return list(dict.fromkeys(urls))

    async def _save_debug_screenshot(self, name: str) -> None:
        if self._config.screenshot_on_error:
            Path(self._config.screenshot_dir).mkdir(parents=True, exist_ok=True)
            path = Path(self._config.screenshot_dir) / f"{name}.png"
            await self._page.screenshot(path=str(path))

