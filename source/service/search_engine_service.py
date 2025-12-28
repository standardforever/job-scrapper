import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional
import random

from playwright.async_api import  Page, TimeoutError as PlaywrightTimeoutError
from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)

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





class HumanBehavior:
    """Utility class to mimic human-like interactions."""
    
    @staticmethod
    async def random_delay(min_ms: int = 100, max_ms: int = 500) -> None:
        """Add a random delay to mimic human reaction time."""
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)
    
    @staticmethod
    async def human_type(page: Page, locator, text: str, min_delay: int = 50, max_delay: int = 150) -> None:
        """Type text character by character with random delays like a human."""
        await locator.click()
        await HumanBehavior.random_delay(100, 300)
        
        for char in text:
            await locator.press(char)
            # Random delay between keystrokes
            delay = random.randint(min_delay, max_delay) / 1000
            await asyncio.sleep(delay)
            
            # Occasionally add a longer pause (like thinking)
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.2, 0.5))
    
    @staticmethod
    async def move_mouse_randomly(page: Page, target_x: int, target_y: int) -> None:
        """Move mouse to target with some randomness."""
        offset_x = random.randint(-5, 5)
        offset_y = random.randint(-5, 5)
        
        await page.mouse.move(target_x + offset_x, target_y + offset_y)
    
    @staticmethod
    async def human_click(page: Page, locator) -> None:
        """Click an element with human-like behavior."""
        try:
            box = await locator.bounding_box()
            if box:
                x = box['x'] + random.uniform(box['width'] * 0.2, box['width'] * 0.8)
                y = box['y'] + random.uniform(box['height'] * 0.2, box['height'] * 0.8)
                
                await page.mouse.move(x, y)
                await HumanBehavior.random_delay(50, 150)
                await page.mouse.click(x, y)
            else:
                await locator.click()
        except Exception:
            await locator.click()
    
    @staticmethod
    async def random_scroll(page: Page, direction: str = "down", amount: Optional[int] = None) -> None:
        """Perform a random scroll action."""
        if amount is None:
            amount = random.randint(100, 300)
        
        if direction == "down":
            await page.mouse.wheel(0, amount)
        else:
            await page.mouse.wheel(0, -amount)
        
        await HumanBehavior.random_delay(200, 500)


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

    GOOGLE_COOKIE_SELECTORS = [
        "#L2AGLb",
        "#W0wltc",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('I agree')",
        "button:has-text('Reject all')",
        "button:has-text('Reject All')",
        "button[aria-label*='Accept']",
        "button[aria-label*='Reject']",
        "form[action*='consent'] button",
        "div[role='dialog'] button",
    ]

    def __init__(self, page: Page, config: Optional[SearchConfig] = None):
        self._page = page
        self._config = config or SearchConfig()
        self._human = HumanBehavior()
        logger.debug(
            "WebSearcher initialized",
            extra={
                "max_retries": self._config.max_retries,
                "search_timeout": self._config.search_timeout,
                "results_wait_time": self._config.results_wait_time,
            },
        )

    async def search(
        self,
        query: str,
        engine: SearchEngine = SearchEngine.DUCKDUCKGO,
    ) -> SearchResult:
        logger.info(
            "Starting web search",
            extra={
                "query": query,
                "engine": engine.value if hasattr(engine, 'value') else str(engine),
            },
        )

        search_method = (
            self._search_duckduckgo
            if engine == SearchEngine.DUCKDUCKGO
            else self._search_google
        )

        for attempt in range(self._config.max_retries):
            logger.debug(
                "Search attempt",
                extra={
                    "attempt": attempt + 1,
                    "max_retries": self._config.max_retries,
                    "query": query,
                },
            )
            try:
                urls = await search_method(query)
                logger.info(
                    "Search completed successfully",
                    extra={
                        "query": query,
                        "engine": engine.value if hasattr(engine, 'value') else str(engine),
                        "urls_found": len(urls),
                        "attempt": attempt + 1,
                    },
                )
                return SearchResult(
                    urls=urls,
                    query=query,
                    engine=engine,
                    success=True,
                )
            except Exception as e:
                logger.warning(
                    "Search attempt failed",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self._config.max_retries,
                        "query": query,
                        "error": str(e),
                    },
                )
                if attempt == self._config.max_retries - 1:
                    logger.error(
                        "Search failed after all retries",
                        extra={
                            "query": query,
                            "engine": engine.value if hasattr(engine, 'value') else str(engine),
                            "error": str(e),
                        },
                    )
                    return SearchResult(
                        urls=[],
                        query=query,
                        engine=engine,
                        success=False,
                        error=str(e),
                    )
                await asyncio.sleep(1)

        logger.error(
            "Search failed - max retries exceeded",
            extra={"query": query},
        )
        return SearchResult(
            urls=[],
            query=query,
            engine=engine,
            success=False,
            error="Max retries exceeded",
        )

    async def _search_duckduckgo(self, query: str) -> list[str]:
        logger.debug(
            "Starting DuckDuckGo search",
            extra={"query": query},
        )

        await self._page.goto("https://duckduckgo.com/")
        logger.debug("Navigated to DuckDuckGo")

        search_box = self._page.locator('input[name="q"]')
        await search_box.wait_for(timeout=self._config.search_timeout)
        await search_box.fill(query)
        await search_box.press("Enter")
        logger.debug(
            "Search query submitted",
            extra={"query": query},
        )

        results_container = self._page.locator("ol.react-results--main")
        await results_container.wait_for(timeout=self._config.search_timeout)
        logger.debug("Results container loaded")

        await asyncio.sleep(self._config.results_wait_time)

        result_links = self._page.locator('article[data-testid="result"] a[href]')
        urls = await self._extract_urls_from_locator(result_links)

        deduplicated_urls = self._deduplicate_urls(urls)
        logger.debug(
            "DuckDuckGo search completed",
            extra={
                "query": query,
                "raw_urls_count": len(urls),
                "deduplicated_urls_count": len(deduplicated_urls),
            },
        )

        return deduplicated_urls

    async def _search_google(self, query: str) -> list[str]:
        logger.debug(
            "Starting Google search with human behavior",
            extra={"query": query},
        )

        # Navigate to Google with random viewport size
        viewport_width = random.randint(1200, 1920)
        viewport_height = random.randint(800, 1080)
        await self._page.set_viewport_size({"width": viewport_width, "height": viewport_height})
        logger.debug(
            "Viewport set",
            extra={"width": viewport_width, "height": viewport_height},
        )

        # Navigate to Google
        await self._page.goto("https://www.google.com/", wait_until="domcontentloaded")
        logger.debug("Navigated to Google")

        # Human-like initial wait (looking at the page)
        await HumanBehavior.random_delay(500, 1500)

        # Random mouse movement on the page (like looking around)
        await self._random_mouse_movement()

        # Handle cookie consent popup
        await self._handle_google_cookie_popup_human()

        # Small pause after cookie handling
        await HumanBehavior.random_delay(300, 800)

        # Find search box
        search_box = await self._find_google_search_box()
        if not search_box:
            logger.error("Could not find Google search box")
            await self._save_debug_screenshot("google_no_searchbox")
            raise Exception("Google search box not found")

        # Move mouse towards search box area first (human-like)
        await self._move_towards_element(search_box)
        await HumanBehavior.random_delay(200, 500)

        # Click on search box with human-like behavior
        await HumanBehavior.human_click(self._page, search_box)
        await HumanBehavior.random_delay(300, 600)

        # Type query character by character like a human
        logger.debug(
            "Typing search query",
            extra={"query": query},
        )
        await HumanBehavior.human_type(self._page, search_box, query)

        # Pause after typing (like reviewing what was typed)
        await HumanBehavior.random_delay(500, 1000)

        # Sometimes people correct typos or pause before searching
        if random.random() < 0.1:
            await HumanBehavior.random_delay(300, 700)

        # Press Enter to search
        await search_box.press("Enter")
        logger.debug("Search query submitted")

        # Wait for results with human-like behavior
        await self._wait_for_google_results_human()

        # Human-like pause while "reading" results
        await HumanBehavior.random_delay(1000, 2000)

        # Maybe scroll down a bit to see more results
        if random.random() < 0.5:
            await HumanBehavior.random_scroll(self._page, "down")
            await HumanBehavior.random_delay(500, 1000)
            await HumanBehavior.random_scroll(self._page, "up", random.randint(50, 100))

        # Extract URLs
        urls = await self._extract_google_results()
        logger.debug(
            "Initial Google results extraction",
            extra={"urls_count": len(urls)},
        )

        if not urls:
            logger.debug("No URLs from primary selectors, attempting fallback extraction")
            urls = await self._fallback_extract_all_links()
            logger.debug(
                "Fallback extraction completed",
                extra={"urls_count": len(urls)},
            )

        deduplicated_urls = self._deduplicate_urls(urls)
        logger.info(
            "Google search completed",
            extra={
                "query": query,
                "raw_urls_count": len(urls),
                "deduplicated_urls_count": len(deduplicated_urls),
            },
        )

        return deduplicated_urls

    async def _random_mouse_movement(self) -> None:
        """Simulate random mouse movement like a human looking at the page."""
        logger.debug("Performing random mouse movement")
        try:
            viewport = self._page.viewport_size
            if viewport:
                for _ in range(random.randint(2, 4)):
                    x = random.randint(100, viewport['width'] - 100)
                    y = random.randint(100, viewport['height'] - 100)
                    
                    await self._page.mouse.move(x, y)
                    await HumanBehavior.random_delay(100, 300)
        except Exception as e:
            logger.debug(
                "Random mouse movement failed",
                extra={"error": str(e)},
            )

    async def _move_towards_element(self, locator) -> None:
        """Move mouse towards an element gradually."""
        try:
            box = await locator.bounding_box()
            if box:
                viewport = self._page.viewport_size
                if viewport:
                    current_x = viewport['width'] // 2
                    current_y = viewport['height'] // 2
                    
                    target_x = box['x'] + box['width'] // 2
                    target_y = box['y'] + box['height'] // 2
                    
                    steps = random.randint(2, 4)
                    for i in range(1, steps + 1):
                        intermediate_x = current_x + (target_x - current_x) * i // steps
                        intermediate_y = current_y + (target_y - current_y) * i // steps
                        
                        intermediate_x += random.randint(-10, 10)
                        intermediate_y += random.randint(-10, 10)
                        
                        await self._page.mouse.move(intermediate_x, intermediate_y)
                        await HumanBehavior.random_delay(50, 150)
        except Exception as e:
            logger.debug(
                "Move towards element failed",
                extra={"error": str(e)},
            )

    async def _find_google_search_box(self):
        """Find Google search box using multiple strategies."""
        logger.debug("Searching for Google search box")
        
        search_box_selectors = [
            'textarea[name="q"]',
            'input[name="q"]',
            'textarea[title="Search"]',
            'input[title="Search"]',
            'textarea[aria-label="Search"]',
            'input[aria-label="Search"]',
            '#APjFqb',
        ]
        
        for selector in search_box_selectors:
            try:
                locator = self._page.locator(selector).first
                if await locator.is_visible(timeout=2000):
                    logger.debug(
                        "Search box found",
                        extra={"selector": selector},
                    )
                    return locator
            except Exception as e:
                logger.debug(
                    "Search box selector failed",
                    extra={"selector": selector, "error": str(e)},
                )
                continue
        
        for selector in search_box_selectors:
            try:
                locator = self._page.locator(selector).first
                await locator.wait_for(state="visible", timeout=3000)
                logger.debug(
                    "Search box found via wait_for",
                    extra={"selector": selector},
                )
                return locator
            except PlaywrightTimeoutError:
                continue
        
        logger.warning("No search box found with any selector")
        return None

    async def _handle_google_cookie_popup_human(self) -> None:
        """Handle Google cookie consent popup with human-like behavior."""
        logger.debug("Checking for Google cookie popup")
        
        popup_found = False
        
        # Check for consent iframe
        try:
            consent_frame = self._page.frame_locator("iframe[src*='consent']")
            for selector in ["#L2AGLb", "button:has-text('Accept')", "button:has-text('Reject')"]:
                try:
                    button = consent_frame.locator(selector).first
                    if await button.is_visible(timeout=1000):
                        popup_found = True
                        await HumanBehavior.random_delay(800, 1500)
                        await HumanBehavior.random_delay(200, 400)
                        await button.click()
                        
                        logger.info(
                            "Cookie popup handled via iframe with human behavior",
                            extra={"selector": selector},
                        )
                        await HumanBehavior.random_delay(500, 1000)
                        return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(
                "No consent iframe found",
                extra={"error": str(e)},
            )

        # Try main page selectors
        for selector in self.GOOGLE_COOKIE_SELECTORS:
            try:
                button = self._page.locator(selector).first
                if await button.is_visible(timeout=800):
                    popup_found = True
                    
                    await HumanBehavior.random_delay(600, 1200)
                    await self._move_towards_element(button)
                    await HumanBehavior.random_delay(150, 350)
                    await HumanBehavior.human_click(self._page, button)
                    
                    logger.info(
                        "Google cookie popup handled with human behavior",
                        extra={"selector": selector},
                    )
                    await HumanBehavior.random_delay(500, 1000)
                    return
            except Exception as e:
                logger.debug(
                    "Cookie selector not found",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        # JavaScript fallback
        try:
            has_consent = await self._page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = btn.innerText.toLowerCase();
                        if (text.includes('accept') || text.includes('agree') || text.includes('reject')) {
                            return true;
                        }
                    }
                    return false;
                }
            """)
            
            if has_consent:
                await HumanBehavior.random_delay(600, 1200)
                
                clicked = await self._page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll('button');
                        for (const btn of buttons) {
                            const text = btn.innerText.toLowerCase();
                            if (text.includes('accept') || text.includes('agree') || text.includes('reject')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                if clicked:
                    logger.info("Cookie popup handled via JavaScript with human delay")
                    await HumanBehavior.random_delay(500, 1000)
                    return
        except Exception as e:
            logger.debug(
                "JavaScript cookie handling failed",
                extra={"error": str(e)},
            )

        if not popup_found:
            logger.debug("No Google cookie popup found")

    async def _wait_for_google_results_human(self) -> bool:
        """Wait for Google results with human-like behavior."""
        logger.debug("Waiting for Google results")
        
        try:
            await self._page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            logger.debug("Network idle timeout, continuing")
        
        await HumanBehavior.random_delay(500, 1000)
        
        for selector in self.GOOGLE_CONTAINER_SELECTORS:
            try:
                container = self._page.locator(selector)
                await container.wait_for(state="visible", timeout=5000)
                logger.debug(
                    "Google results container found",
                    extra={"selector": selector},
                )
                
                await HumanBehavior.random_delay(800, 1500)
                return True
            except PlaywrightTimeoutError:
                logger.debug(
                    "Container selector timed out",
                    extra={"selector": selector},
                )
                continue

        logger.warning("No Google results container found")
        if self._config.screenshot_on_error:
            await self._save_debug_screenshot("google_no_results")

        return False

    async def _extract_google_results(self) -> list[str]:
        logger.debug("Extracting Google results")
        for selector in self.GOOGLE_RESULT_SELECTORS:
            try:
                locator = self._page.locator(selector)
                count = await locator.count()
                logger.debug(
                    "Checking result selector",
                    extra={"selector": selector, "count": count},
                )

                if count > 0:
                    urls = await self._extract_urls_from_locator(locator)
                    if urls:
                        logger.debug(
                            "URLs extracted with selector",
                            extra={"selector": selector, "urls_count": len(urls)},
                        )
                        return urls
            except Exception as e:
                logger.debug(
                    "Selector extraction failed",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug("No URLs extracted from any Google result selector")
        return []

    async def _fallback_extract_all_links(self) -> list[str]:
        logger.debug("Starting fallback link extraction")
        
        try:
            urls = await self._page.evaluate("""
                () => {
                    const urls = [];
                    const links = document.querySelectorAll('a[href]');
                    const searchEngineDomains = [
                        'google.com', 'google.co', 'gstatic.com', 'youtube.com',
                        'duckduckgo.com', 'accounts.google', 'policies.google',
                        'support.google', 'webcache.googleusercontent', 'translate.google'
                    ];
                    
                    for (const link of links) {
                        const href = link.href;
                        const text = link.innerText?.trim() || '';
                        
                        if (href && href.startsWith('http') && text.length > 3) {
                            const isSearchEngine = searchEngineDomains.some(domain => 
                                href.toLowerCase().includes(domain)
                            );
                            if (!isSearchEngine) {
                                urls.push(href);
                            }
                        }
                    }
                    return [...new Set(urls)];
                }
            """)
            logger.debug(
                "Fallback extraction via JavaScript completed",
                extra={"urls_count": len(urls) if urls else 0},
            )
            return urls or []
        except Exception as e:
            logger.warning(
                "JavaScript fallback extraction failed",
                extra={"error": str(e)},
            )

        all_links = self._page.locator("a[href]")
        urls = []

        count = await all_links.count()
        logger.debug(
            "Total links found on page",
            extra={"count": count},
        )

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
            except Exception as e:
                logger.debug(
                    "Failed to extract link",
                    extra={"index": i, "error": str(e)},
                )
                continue

        logger.debug(
            "Fallback extraction completed",
            extra={"urls_extracted": len(urls)},
        )
        return urls

    async def _extract_urls_from_locator(self, locator) -> list[str]:
        urls = []
        count = await locator.count()
        logger.debug(
            "Extracting URLs from locator",
            extra={"element_count": count},
        )

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
            except Exception as e:
                logger.debug(
                    "Failed to extract URL from element",
                    extra={"index": i, "error": str(e)},
                )
                continue

        logger.debug(
            "URL extraction from locator completed",
            extra={"urls_extracted": len(urls)},
        )
        return urls

    def _is_search_engine_url(self, url: str) -> bool:
        url_lower = url.lower()
        is_search_engine = any(domain in url_lower for domain in self.SEARCH_ENGINE_DOMAINS)
        return is_search_engine

    def _deduplicate_urls(self, urls: list[str]) -> list[str]:
        deduplicated = list(dict.fromkeys(urls))
        if len(urls) != len(deduplicated):
            logger.debug(
                "URLs deduplicated",
                extra={
                    "original_count": len(urls),
                    "deduplicated_count": len(deduplicated),
                    "duplicates_removed": len(urls) - len(deduplicated),
                },
            )
        return deduplicated

    async def _save_debug_screenshot(self, name: str) -> None:
        if self._config.screenshot_on_error:
            Path(self._config.screenshot_dir).mkdir(parents=True, exist_ok=True)
            path = Path(self._config.screenshot_dir) / f"{name}.png"
            await self._page.screenshot(path=str(path))
            logger.info(
                "Debug screenshot saved",
                extra={"path": str(path), "name": name},
            )
            
            try:
                html_path = Path(self._config.screenshot_dir) / f"{name}.html"
                content = await self._page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(
                    "Debug HTML saved",
                    extra={"path": str(html_path)},
                )
            except Exception as e:
                logger.debug(
                    "Failed to save debug HTML",
                    extra={"error": str(e)},
                )