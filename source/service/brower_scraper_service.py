import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from playwright.async_api import  Page
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError





# =============================================================================
# DOM Content Extractor
# =============================================================================


class TagCategory(Enum):
    BLOCK = "block"
    HEADING = "heading"
    LIST_CONTAINER = "list_container"
    INLINE = "inline"
    TABLE = "table"


@dataclass
class ExtractedContent:
    structured_text: str
    raw_structure: dict[str, Any]


@dataclass
class ExtractionConfig:
    wait_seconds: float = 2.0
    handle_cookies: bool = True
    handle_popups: bool = True
    cookie_timeout: int = 3000
    popup_timeout: int = 2000
    scroll_to_load: bool = False
    scroll_delay: float = 0.5


class DOMContentExtractor:

    # Cookie consent button selectors (ordered by specificity)
    COOKIE_SELECTORS = [
        # Common accept buttons
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('Allow all')",
        "button:has-text('Allow All')",
        "button:has-text('I agree')",
        "button:has-text('I Accept')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Okay')",
        "button:has-text('Continue')",
        "button:has-text('Agree')",
        "button:has-text('Consent')",
        # Reject/necessary only (fallback)
        "button:has-text('Reject all')",
        "button:has-text('Reject All')",
        "button:has-text('Decline')",
        "button:has-text('Only necessary')",
        "button:has-text('Essential only')",
        # ID/class based selectors
        "[id*='accept-cookies']",
        "[id*='cookie-accept']",
        "[id*='gdpr-accept']",
        "[id*='consent-accept']",
        "[class*='cookie-accept']",
        "[class*='accept-cookie']",
        "[data-testid*='cookie-accept']",
        "[data-testid*='accept-cookies']",
        # Common cookie banner libraries
        "#onetrust-accept-btn-handler",
        ".onetrust-accept-btn-handler",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#cookieconsent-button-accept",
        ".cc-accept",
        ".cc-allow",
        ".cc-dismiss",
        "#accept-cookies",
        "#cookie-consent-accept",
        ".cookie-consent-accept",
        "[aria-label='Accept cookies']",
        "[aria-label='Accept all cookies']",
    ]

    # Popup/modal close selectors
    POPUP_CLOSE_SELECTORS = [
        # Close buttons
        "button:has-text('Close')",
        "button:has-text('×')",
        "button:has-text('X')",
        "button:has-text('No thanks')",
        "button:has-text('No, thanks')",
        "button:has-text('Not now')",
        "button:has-text('Maybe later')",
        "button:has-text('Skip')",
        "button:has-text('Dismiss')",
        # Icon buttons
        "[aria-label='Close']",
        "[aria-label='close']",
        "[aria-label='Dismiss']",
        "[title='Close']",
        "[title='close']",
        # Class/ID based
        ".modal-close",
        ".popup-close",
        ".close-button",
        ".close-btn",
        ".dismiss-button",
        "[class*='close-modal']",
        "[class*='modal-close']",
        "[class*='popup-close']",
        "[class*='newsletter-close']",
        "[data-dismiss='modal']",
        "[data-close]",
        # SVG close icons
        "button svg[class*='close']",
        "button[class*='close'] svg",
    ]

    # Elements to remove before extraction (overlays, banners, etc.)
    OVERLAY_SELECTORS = [
        "[class*='cookie-banner']",
        "[class*='cookie-notice']",
        "[class*='cookie-consent']",
        "[class*='gdpr-banner']",
        "[class*='newsletter-popup']",
        "[class*='newsletter-modal']",
        "[class*='email-popup']",
        "[class*='subscribe-popup']",
        "[class*='overlay-modal']",
        "[id*='cookie-banner']",
        "[id*='cookie-notice']",
        "[id*='newsletter-popup']",
        "#onetrust-consent-sdk",
        "#CybotCookiebotDialog",
        ".modal-backdrop",
        ".overlay-backdrop",
    ]
    BLOCK_TAGS = frozenset({
        "div", "section", "article", "main", "aside",
        "figure", "figcaption", "address", "details", "summary",
    })
    HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    LIST_CONTAINER_TAGS = frozenset({"ul", "ol"})
    INLINE_TAGS = frozenset({
        "span", "strong", "b", "em", "i", "u", "small", "mark", "code",
    })
    TABLE_SECTION_TAGS = frozenset({"thead", "tbody", "tfoot"})
    TABLE_CELL_TAGS = frozenset({"td", "th"})

    EXTRACTION_SCRIPT = """
        () => {
            const SKIP_TAGS = new Set([
                'script', 'style', 'noscript', 'svg', 'path',
                'head', 'link', 'footer', 'header', 'nav'
            ]);
            const INTERACTIVE_TAGS = new Set(['a', 'button']);

            function extractAll(element) {
                if (!element) return null;

                const tagName = element.tagName?.toLowerCase();
                if (!tagName || SKIP_TAGS.has(tagName)) return null;

                const node = { tag: tagName };

                const href = element.getAttribute('href');
                const src = element.getAttribute('src');
                const action = element.getAttribute('action');

                if (href && !href.startsWith('javascript:')) node.href = href;
                if (src && !src.startsWith('data:')) node.src = src;
                if (action) node.action = action;

                let text = '';
                for (const child of element.childNodes) {
                    if (child.nodeType === Node.TEXT_NODE) {
                        const t = child.textContent.trim();
                        if (t) text += (text ? ' ' : '') + t;
                    }
                }
                if (text) node.text = text;

                if (INTERACTIVE_TAGS.has(tagName)) {
                    const innerText = element.innerText?.trim();
                    if (innerText) node.innerText = innerText;
                }

                const children = [];
                for (const child of element.children) {
                    const result = extractAll(child);
                    if (result) children.push(result);
                }
                if (children.length > 0) node.children = children;

                return node;
            }

            return extractAll(document.body);
        }
    """

    def __init__(self, page: Page, config: Optional[ExtractionConfig] = None):
        self._page = page
        self._config = config or ExtractionConfig()
    
    async def _handle_cookie_consent(self) -> bool:
        if not self._config.handle_cookies:
            return False

        for selector in self.COOKIE_SELECTORS:
            try:
                button = self._page.locator(selector).first
                if await button.is_visible(timeout=500):
                    await button.click(timeout=self._config.cookie_timeout)
                    await asyncio.sleep(0.5)
                    return True
            except (PlaywrightTimeoutError, Exception):
                continue

        return False

    async def _handle_popups(self) -> int:
        if not self._config.handle_popups:
            return 0

        closed_count = 0

        for selector in self.POPUP_CLOSE_SELECTORS:
            try:
                buttons = self._page.locator(selector)
                count = await buttons.count()

                for i in range(min(count, 3)):  # Limit to 3 per selector
                    try:
                        button = buttons.nth(i)
                        if await button.is_visible(timeout=300):
                            await button.click(timeout=self._config.popup_timeout)
                            closed_count += 1
                            await asyncio.sleep(0.3)
                    except Exception:
                        continue
            except Exception:
                continue

        return closed_count

    async def _remove_overlays(self) -> int:
        removed_count = 0

        for selector in self.OVERLAY_SELECTORS:
            try:
                count = await self._page.evaluate(
                    f"""
                    () => {{
                        const elements = document.querySelectorAll('{selector}');
                        let count = 0;
                        elements.forEach(el => {{
                            el.remove();
                            count++;
                        }});
                        return count;
                    }}
                    """
                )
                removed_count += count
            except Exception:
                continue

        return removed_count
    

    async def _scroll_to_load_content(self) -> None:
        if not self._config.scroll_to_load:
            return

        try:
            # Get page height
            scroll_height = await self._page.evaluate("document.body.scrollHeight")
            viewport_height = await self._page.evaluate("window.innerHeight")

            # Scroll incrementally
            current_position = 0
            while current_position < scroll_height:
                current_position += viewport_height
                await self._page.evaluate(f"window.scrollTo(0, {current_position})")
                await asyncio.sleep(self._config.scroll_delay)

                # Check if page height increased (lazy loading)
                new_height = await self._page.evaluate("document.body.scrollHeight")
                if new_height > scroll_height:
                    scroll_height = new_height

            # Scroll back to top
            await self._page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
    
    async def _wait_for_page_ready(self) -> None:
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightTimeoutError:
            pass

        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass

    # async def extract(self, wait_seconds: float = 2.0) -> ExtractedContent:
    #     await asyncio.sleep(wait_seconds)

    #     raw_content = await self._page.evaluate(self.EXTRACTION_SCRIPT)

    #     if isinstance(raw_content, str):
    #         raw_content = json.loads(raw_content)

    #     structured_text = self._structure_to_text(raw_content)

    #     return ExtractedContent(
    #         structured_text=structured_text,
    #         raw_structure=raw_content or {},
    #     )

    async def extract(
        self,
        wait_seconds: Optional[float] = None,
        handle_cookies: Optional[bool] = None,
        handle_popups: Optional[bool] = None,
    ) -> ExtractedContent:
        wait_seconds = wait_seconds or self._config.wait_seconds
        should_handle_cookies = handle_cookies if handle_cookies is not None else self._config.handle_cookies
        should_handle_popups = handle_popups if handle_popups is not None else self._config.handle_popups

        # Wait for page to be ready
        await self._wait_for_page_ready()

        # Handle cookie consent
        if should_handle_cookies:
            cookie_handled = await self._handle_cookie_consent()
            if cookie_handled:
                await asyncio.sleep(0.5)

        # Handle popups
        if should_handle_popups:
            await self._handle_popups()

        # Remove overlay elements
        await self._remove_overlays()

        # Scroll to load lazy content if enabled
        await self._scroll_to_load_content()

        # Final wait
        await asyncio.sleep(wait_seconds)

        # Extract content
        try:
            raw_content = await self._page.evaluate(self.EXTRACTION_SCRIPT)

            if isinstance(raw_content, str):
                raw_content = json.loads(raw_content)

            structured_text = self._structure_to_text(raw_content or {})

            return ExtractedContent(
                structured_text=structured_text,
                raw_structure=raw_content or {},
            )
        except Exception as e:
            return ExtractedContent(
                structured_text="",
                raw_structure={"error": str(e)},
            )

    def _structure_to_text(self, node: dict[str, Any], depth: int = 0) -> str:
        if not node or not isinstance(node, dict):
            return ""

        tag = node.get("tag", "")
        text = node.get("text", "").strip()
        inner_text = node.get("innerText", "").strip()
        href = node.get("href", "")
        src = node.get("src", "")
        action = node.get("action", "")
        children = node.get("children", [])

        def process_children() -> str:
            child_texts = [
                self._structure_to_text(child, depth + 1)
                for child in children
            ]
            return " ".join(t for t in child_texts if t.strip())

        if tag in self.HEADING_TAGS:
            level = int(tag[1])
            content = inner_text or text or process_children()
            if content.strip():
                return f"\n\n{'#' * level} {content.strip()}\n"
            return ""

        if tag == "a":
            link_text = (inner_text or text or process_children() or "link").strip()
            return f"[{link_text}]({href})" if href else link_text

        if tag == "button":
            btn_text = (inner_text or text or process_children() or "button").strip()
            return f"[BUTTON: {btn_text}]"

        if tag == "img":
            alt = text or "image"
            return f"[IMAGE: {alt}]({src})" if src else f"[IMAGE: {alt}]"

        if tag == "form":
            form_header = f"[FORM action={action}]" if action else "[FORM]"
            form_content = process_children()
            if form_content.strip():
                return f"\n{form_header}\n{form_content.strip()}\n[/FORM]\n"
            return ""

        if tag == "input":
            return "[INPUT]"

        if tag == "textarea":
            return "[TEXTAREA]"

        if tag == "select":
            child_content = process_children()
            return f"[SELECT: {child_content}]" if child_content else "[SELECT]"

        if tag == "option":
            return text or inner_text or ""

        if tag in self.LIST_CONTAINER_TAGS:
            list_items = [
                self._structure_to_text(child, depth + 1)
                for child in children
            ]
            filtered_items = [item for item in list_items if item.strip()]
            return "\n" + "\n".join(filtered_items) + "\n" if filtered_items else ""

        if tag == "li":
            content = self._combine_text_and_children(text, process_children())
            return f"  • {content}" if content else ""

        if tag == "p":
            content = self._combine_text_and_children(text, process_children())
            return f"\n{content}\n" if content else ""

        if tag == "br":
            return "\n"

        if tag == "hr":
            return "\n---\n"

        if tag == "table":
            table_content = self._process_table(node)
            return f"\n[TABLE]\n{table_content}[/TABLE]\n" if table_content else ""

        if tag in self.TABLE_SECTION_TAGS:
            return process_children()

        if tag == "tr":
            cells = [
                self._structure_to_text(child, depth + 1).strip()
                for child in children
            ]
            filtered_cells = [c for c in cells if c is not None]
            return "| " + " | ".join(filtered_cells) + " |" if filtered_cells else ""

        if tag in self.TABLE_CELL_TAGS:
            return self._combine_text_and_children(text, process_children())

        if tag == "pre":
            content = text or process_children()
            return f"\n```\n{content.strip()}\n```\n" if content.strip() else ""

        if tag == "code":
            content = text or inner_text or process_children()
            return f"`{content.strip()}`" if content.strip() else ""

        if tag == "blockquote":
            content = text or process_children()
            if content.strip():
                quoted = "\n".join(f"> {line}" for line in content.strip().split("\n"))
                return f"\n{quoted}\n"
            return ""

        if tag in self.INLINE_TAGS:
            return text or process_children()

        if tag in self.BLOCK_TAGS or tag == "body":
            content = self._combine_text_and_children(text, process_children())
            if content:
                return f"\n{content}\n" if tag in self.BLOCK_TAGS else content
            return ""

        return self._combine_text_and_children(text, process_children())

    def _combine_text_and_children(self, text: str, child_content: str) -> str:
        parts = []
        if text:
            parts.append(text)
        if child_content.strip():
            parts.append(child_content.strip())
        return " ".join(parts).strip()

    def _process_table(self, table_node: dict[str, Any]) -> str:
        rows: list[dict[str, Any]] = []

        def find_rows(node: dict[str, Any]) -> None:
            if node.get("tag") == "tr":
                rows.append(node)
            for child in node.get("children", []):
                find_rows(child)

        find_rows(table_node)

        if not rows:
            return ""

        result_lines = []
        for i, row in enumerate(rows):
            cells = []
            for child in row.get("children", []):
                if child.get("tag") in self.TABLE_CELL_TAGS:
                    cell_text = child.get("text", "") or child.get("innerText", "")
                    if not cell_text and child.get("children"):
                        nested_parts = [
                            self._structure_to_text(nested, 0).strip()
                            for nested in child.get("children", [])
                        ]
                        cell_text = " ".join(p for p in nested_parts if p)
                    cells.append(cell_text.strip() if cell_text else "")

            if cells:
                result_lines.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    result_lines.append("|" + "|".join(["---"] * len(cells)) + "|")

        return "\n".join(result_lines) + "\n"






