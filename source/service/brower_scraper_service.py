import asyncio
import json
from enum import Enum
from typing import Any, Optional
from playwright.async_api import  Page
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)


from dataclasses import dataclass, field

@dataclass
class StructuredSection:
    """Represents a section of content with optional key-value pairs."""
    heading: Optional[str] = None
    content: list[str] = field(default_factory=list)
    key_values: dict[str, Any] = field(default_factory=dict)
    subsections: list["StructuredSection"] = field(default_factory=list)


# =============================================================================
# DOM Content Extractor
# =============================================================================

@dataclass
class SectionedContent:
    """Page content sectioned by headings"""
    sections: dict[str, str]
    metadata: dict[str, Any]  # For any intro content before first heading
    raw_structure: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": self.sections,
            "metadata": self.metadata,
            "raw_structure": self.raw_structure,
        }


@dataclass
class JobPageContent:
    """Structured job page content as flat dictionary"""
    data: dict[str, Any]
    raw_structure: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.data
    



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
    # Add these constants to filter navigation
    SKIP_CONTAINER_TAGS = frozenset({"nav", "header", "footer", "aside"})
    COMMON_JOB_LABELS = frozenset({
        "date", "posted", "posted on", "date posted", "publish date",
        "job title", "title", "position", "role",
        "location", "city", "country", "region", "workplace",
        "department", "team", "division", "business unit",
        "employment type", "job type", "type", "contract type", "work type",
        "experience", "experience level", "seniority", "level",
        "salary", "compensation", "pay", "wage", "salary range",
        "company", "employer", "organization", "organisation",
        "job id", "job req. id", "requisition id", "req id", "reference", "job number",
        "closing date", "deadline", "apply by", "expires", "valid until",
        "start date", "availability",
        "industry", "sector", "field",
        "remote", "hybrid", "on-site", "work arrangement",
        "benefits", "perks", "reports to", "manager", "supervisor",
        "travel", "travel required",
    })

    COMMON_SECTION_HEADINGS = frozenset({
        "job summary", "summary", "overview", "about the role", "about this role",
        "description", "job description", "role description", "position description",
        "responsibilities", "duties", "key responsibilities", "what you'll do",
        "principal duties", "principle duties", "duties and responsibilities",
        "requirements", "qualifications", "what we're looking for", "what you'll need",
        "required qualifications", "minimum qualifications", "must have",
        "preferred qualifications", "nice to have", "preferred", "bonus points",
        "skills", "required skills", "technical skills", "competencies",
        "experience", "required experience", "professional requirements",
        "education", "educational requirements", "education requirements",
        "benefits", "what we offer", "perks", "compensation and benefits",
        "about us", "about the company", "company overview", "who we are",
        "how to apply", "application process", "next steps",
        "equal opportunity", "eeo", "diversity",
        "closing date", "application deadline",
        "additional information", "other information", "notes",
    })

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
    BOLD_TAGS = frozenset({"strong", "b"})
    SKIP_TEXT_PATTERNS = frozenset({"http", "https", "www", "ftp"})

    EXTRACTION_SCRIPT = """
        () => {
            const SKIP_TAGS = new Set([
                'script', 'style', 'noscript', 'svg', 'path', 'head', 'link', 'footer', 'nav'
            ]);
            const INTERACTIVE_TAGS = new Set(['a', 'button']);

            function isVisible(element) {
                if (!element || element.nodeType !== Node.ELEMENT_NODE) return false;
                
                const style = window.getComputedStyle(element);
                
                // Check common ways elements are hidden
                if (style.display === 'none') return false;
                if (style.visibility === 'hidden' || style.visibility === 'collapse') return false;
                if (parseFloat(style.opacity) === 0) return false;
                
                // Check if element has no dimensions
                const rect = element.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return false;
                
                // Check for clip-path or clip hiding
                if (style.clipPath === 'inset(100%)') return false;
                if (style.clip === 'rect(0px, 0px, 0px, 0px)') return false;
                
                // Check for off-screen positioning (common screen-reader only technique)
                if (rect.right < 0 || rect.bottom < 0) return false;
                
                return true;
            }

            function extractAll(element) {
                if (!element) return null;

                const tagName = element.tagName?.toLowerCase();
                if (!tagName || SKIP_TAGS.has(tagName)) return null;

                // Skip hidden elements
                if (!isVisible(element)) return null;

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
        logger.debug(
            "DOMContentExtractor initialized",
            extra={
                "handle_cookies": self._config.handle_cookies,
                "handle_popups": self._config.handle_popups,
                "scroll_to_load": self._config.scroll_to_load,
                "wait_seconds": self._config.wait_seconds,
            },
        )
    
    async def _handle_cookie_consent(self) -> bool:
        if not self._config.handle_cookies:
            logger.debug("Cookie handling disabled, skipping")
            return False

        logger.debug("Attempting to handle cookie consent")
        for selector in self.COOKIE_SELECTORS:
            try:
                button = self._page.locator(selector).first
                if await button.is_visible(timeout=500):
                    await button.click(timeout=self._config.cookie_timeout)
                    await asyncio.sleep(0.5)
                    logger.info(
                        "Cookie consent handled successfully",
                        extra={"selector": selector},
                    )
                    return True
            except (PlaywrightTimeoutError, Exception) as e:
                logger.debug(
                    "Cookie selector not found or failed",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug("No cookie consent button found")
        return False

    async def _handle_popups(self) -> int:
        if not self._config.handle_popups:
            logger.debug("Popup handling disabled, skipping")
            return 0

        logger.debug("Attempting to handle popups")
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
                            logger.debug(
                                "Popup closed",
                                extra={"selector": selector, "index": i},
                            )
                            await asyncio.sleep(0.3)
                    except Exception as e:
                        logger.debug(
                            "Failed to close popup",
                            extra={"selector": selector, "index": i, "error": str(e)},
                        )
                        continue
            except Exception as e:
                logger.debug(
                    "Popup selector failed",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug(
            "Popup handling completed",
            extra={"closed_count": closed_count},
        )
        return closed_count

    async def _remove_overlays(self) -> int:
        logger.debug("Attempting to remove overlay elements")
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
                if count > 0:
                    logger.debug(
                        "Removed overlay elements",
                        extra={"selector": selector, "count": count},
                    )
            except Exception as e:
                logger.debug(
                    "Failed to remove overlay",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug(
            "Overlay removal completed",
            extra={"total_removed": removed_count},
        )
        return removed_count
    

    async def _scroll_to_load_content(self) -> None:
        if not self._config.scroll_to_load:
            logger.debug("Scroll to load disabled, skipping")
            return

        logger.debug("Starting scroll to load content")
        try:
            # Get page height
            scroll_height = await self._page.evaluate("document.body.scrollHeight")
            viewport_height = await self._page.evaluate("window.innerHeight")
            logger.debug(
                "Initial page dimensions",
                extra={"scroll_height": scroll_height, "viewport_height": viewport_height},
            )

            # Scroll incrementally
            current_position = 0
            scroll_count = 0
            while current_position < scroll_height:
                current_position += viewport_height
                await self._page.evaluate(f"window.scrollTo(0, {current_position})")
                await asyncio.sleep(self._config.scroll_delay)
                scroll_count += 1

                # Check if page height increased (lazy loading)
                new_height = await self._page.evaluate("document.body.scrollHeight")
                if new_height > scroll_height:
                    logger.debug(
                        "Lazy content loaded, page height increased",
                        extra={"old_height": scroll_height, "new_height": new_height},
                    )
                    scroll_height = new_height

            # Scroll back to top
            await self._page.evaluate("window.scrollTo(0, 0)")
            logger.debug(
                "Scroll to load completed",
                extra={"scroll_count": scroll_count, "final_height": scroll_height},
            )
        except Exception as e:
            logger.warning(
                "Scroll to load failed",
                extra={"error": str(e)},
            )
            pass
    
    async def _wait_for_page_ready(self) -> None:
        logger.debug("Waiting for page to be ready")
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
            logger.debug("DOM content loaded")
        except PlaywrightTimeoutError:
            logger.warning("Timeout waiting for DOM content loaded")
            pass

        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
            logger.debug("Network idle reached")
        except PlaywrightTimeoutError:
            logger.warning("Timeout waiting for network idle")
            pass

    async def extract(
        self,
        wait_seconds: Optional[float] = None,
        handle_cookies: Optional[bool] = None,
        handle_popups: Optional[bool] = None,
    ) -> ExtractedContent:
        logger.info(
            "Starting content extraction",
            extra={
                "wait_seconds": wait_seconds,
                "handle_cookies": handle_cookies,
                "handle_popups": handle_popups,
            },
        )
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
            popups_closed = await self._handle_popups()
            logger.debug(
                "Popups handling result",
                extra={"popups_closed": popups_closed},
            )

        # Remove overlay elements
        overlays_removed = await self._remove_overlays()
        logger.debug(
            "Overlays removal result",
            extra={"overlays_removed": overlays_removed},
        )

        # Scroll to load lazy content if enabled
        await self._scroll_to_load_content()

        # Final wait
        logger.debug(
            "Final wait before extraction",
            extra={"wait_seconds": wait_seconds},
        )
        await asyncio.sleep(wait_seconds)

        # Extract content
        try:
            logger.debug("Executing extraction script")
            raw_content = await self._page.evaluate(self.EXTRACTION_SCRIPT)

            if isinstance(raw_content, str):
                raw_content = json.loads(raw_content)

            structured_text = self._structure_to_text(raw_content or {})

            logger.info(
                "Content extraction completed successfully",
                extra={
                    "structured_text_length": len(structured_text),
                    "has_raw_structure": bool(raw_content),
                },
            )

            return ExtractedContent(
                structured_text=structured_text,
                raw_structure=raw_content or {},
            )
        except Exception as e:
            logger.error(
                "Content extraction failed",
                extra={"error": str(e)},
                exc_info=True,
            )
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
        logger.debug("Processing table node")
        rows: list[dict[str, Any]] = []

        def find_rows(node: dict[str, Any]) -> None:
            if node.get("tag") == "tr":
                rows.append(node)
            for child in node.get("children", []):
                find_rows(child)

        find_rows(table_node)

        if not rows:
            logger.debug("No rows found in table")
            return ""

        logger.debug(
            "Table rows found",
            extra={"row_count": len(rows)},
        )

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

        logger.debug(
            "Table processing completed",
            extra={"result_lines": len(result_lines)},
        )
        return "\n".join(result_lines) + "\n"

























    # def _extract_structured_data(self, node: dict[str, Any]) -> dict[str, Any]:
    #     """
    #     Extract page content into structured dictionary.
    #     Tries multiple strategies for different site structures.
    #     """
    #     result: dict[str, Any] = {}
        
    #     # Strategy 1: Definition lists (dl/dt/dd)
    #     self._extract_definition_lists(node, result)
        
    #     # Strategy 2: Table-based pairs
    #     self._extract_table_pairs(node, result)
        
    #     # Strategy 3: Inline label:value patterns
    #     self._extract_inline_label_values(node, result)
        
    #     # Strategy 4: Heading sections
    #     self._extract_heading_sections(node, result)
        
    #     # Strategy 5: Bold pseudo-headings
    #     self._extract_bold_sections(node, result)
        
    #     # Cleanup
    #     return self._cleanup_structured_result(result)


    # def _extract_definition_lists(self, node: dict[str, Any], result: dict[str, Any]) -> None:
    #     """Extract from <dl><dt>Label</dt><dd>Value</dd></dl> patterns."""
    #     if not node or not isinstance(node, dict):
    #         return
        
    #     tag = node.get("tag", "")
        
    #     if tag == "dl":
    #         children = node.get("children", [])
    #         i = 0
    #         while i < len(children):
    #             child = children[i]
    #             if child.get("tag") == "dt":
    #                 label = self._get_node_text(child)
    #                 if i + 1 < len(children) and children[i + 1].get("tag") == "dd":
    #                     value = self._get_text_or_list(children[i + 1])
    #                     if label and value:
    #                         self._add_to_structured_result(result, label, value)
    #                     i += 2
    #                     continue
    #             i += 1
    #         return
        
    #     for child in node.get("children", []):
    #         self._extract_definition_lists(child, result)


    # def _extract_table_pairs(self, node: dict[str, Any], result: dict[str, Any]) -> None:
    #     """Extract from table rows with label-value pattern."""
    #     if not node or not isinstance(node, dict):
    #         return
        
    #     tag = node.get("tag", "")
        
    #     if tag == "tr":
    #         children = node.get("children", [])
    #         cells = [c for c in children if c.get("tag") in self.TABLE_CELL_TAGS]
            
    #         if len(cells) == 2:
    #             label = self._get_node_text(cells[0])
    #             value = self._get_text_or_list(cells[1])
    #             if label and value and self._looks_like_label(label):
    #                 self._add_to_structured_result(result, label, value)
    #                 return
        
    #     for child in node.get("children", []):
    #         self._extract_table_pairs(child, result)


    # def _extract_inline_label_values(
    #     self, 
    #     node: dict[str, Any], 
    #     result: dict[str, Any],
    #     parent_children: Optional[list] = None,
    #     index: int = 0
    # ) -> None:
    #     """
    #     Extract patterns like:
    #     - <span>Label:</span><span>Value</span>
    #     - <div><strong>Label:</strong> Value</div>
    #     - Text containing "Label: Value"
    #     """
    #     if not node or not isinstance(node, dict):
    #         return
        
    #     tag = node.get("tag", "")
    #     text = node.get("text", "").strip()
    #     children = node.get("children", [])
        
    #     # Pattern: "Label: Value" in same node
    #     if text and ":" in text and not children:
    #         parts = text.split(":", 1)
    #         if len(parts) == 2:
    #             label, value = parts[0].strip(), parts[1].strip()
    #             if label and value and self._looks_like_label(label):
    #                 self._add_to_structured_result(result, label, value)
    #                 return
        
    #     # Pattern: <span>Label:</span><span>Value</span> as siblings
    #     if tag == "span" and text.endswith(":") and parent_children:
    #         label = text.rstrip(":").strip()
    #         if index + 1 < len(parent_children):
    #             next_sibling = parent_children[index + 1]
    #             value = self._get_node_text(next_sibling)
    #             if label and value and self._looks_like_label(label):
    #                 self._add_to_structured_result(result, label, value)
    #                 return
        
    #     # Pattern: Container with label child + value child
    #     if len(children) >= 2:
    #         for i, child in enumerate(children):
    #             child_text = child.get("text", "").strip()
    #             if child_text.endswith(":"):
    #                 label = child_text.rstrip(":").strip()
    #                 if i + 1 < len(children):
    #                     value = self._get_node_text(children[i + 1])
    #                     if label and value and self._looks_like_label(label):
    #                         self._add_to_structured_result(result, label, value)
        
    #     # Pattern: Heading with label:value children
    #     if tag in self.HEADING_TAGS and len(children) >= 2:
    #         first_text = children[0].get("text", "").strip()
    #         if first_text.endswith(":"):
    #             label = first_text.rstrip(":").strip()
    #             value = self._get_node_text(children[1])
    #             if label and value:
    #                 self._add_to_structured_result(result, label, value)
    #                 return
        
    #     for i, child in enumerate(children):
    #         self._extract_inline_label_values(child, result, children, i)


    # def _extract_heading_sections(self, node: dict[str, Any], result: dict[str, Any]) -> None:
    #     """Extract content grouped under headings."""
    #     elements = []
    #     self._flatten_for_sections(node, elements)
        
    #     current_heading: Optional[str] = None
    #     current_content: list[str] = []
    #     content_is_list = False
        
    #     for elem in elements:
    #         elem_type = elem.get("_type")
            
    #         if elem_type == "heading":
    #             if current_heading:
    #                 self._save_section(result, current_heading, current_content, content_is_list)
    #             current_heading = elem.get("text", "")
    #             current_content = []
    #             content_is_list = False
                
    #         elif elem_type == "list" and current_heading:
    #             content_is_list = True
    #             current_content.extend(elem.get("items", []))
                
    #         elif elem_type == "paragraph" and current_heading:
    #             text = elem.get("text", "")
    #             if text:
    #                 current_content.append(text)
        
    #     if current_heading:
    #         self._save_section(result, current_heading, current_content, content_is_list)


    # def _flatten_for_sections(self, node: dict[str, Any], elements: list[dict]) -> None:
    #     """Flatten DOM keeping headings, lists, and paragraphs."""
    #     if not node or not isinstance(node, dict):
    #         return
        
    #     tag = node.get("tag", "")
        
    #     if tag in self.HEADING_TAGS:
    #         text = self._get_node_text(node)
    #         if text and self._looks_like_section_heading(text):
    #             elements.append({"_type": "heading", "text": text})
    #         return
        
    #     if tag in self.LIST_CONTAINER_TAGS:
    #         items = self._extract_list_items_recursive(node)
    #         if items:
    #             elements.append({"_type": "list", "items": items})
    #         return
        
    #     if tag == "p":
    #         text = self._get_node_text(node)
    #         if text:
    #             elements.append({"_type": "paragraph", "text": text})
    #         return
        
    #     for child in node.get("children", []):
    #         self._flatten_for_sections(child, elements)


    # def _extract_bold_sections(self, node: dict[str, Any], result: dict[str, Any]) -> None:
    #     """Extract sections using bold/strong as pseudo-headings."""
    #     elements = []
    #     self._flatten_for_bold_sections(node, elements)
        
    #     current_heading: Optional[str] = None
    #     current_content: list[str] = []
    #     content_is_list = False
        
    #     for elem in elements:
    #         elem_type = elem.get("_type")
            
    #         if elem_type == "bold_heading":
    #             text = elem.get("text", "")
    #             if text and self._looks_like_section_heading(text) and text not in result:
    #                 if current_heading:
    #                     self._save_section(result, current_heading, current_content, content_is_list)
    #                 current_heading = text
    #                 current_content = []
    #                 content_is_list = False
                    
    #         elif elem_type == "list" and current_heading:
    #             content_is_list = True
    #             current_content.extend(elem.get("items", []))
                
    #         elif elem_type == "text" and current_heading:
    #             text = elem.get("text", "")
    #             if text:
    #                 current_content.append(text)
        
    #     if current_heading:
    #         self._save_section(result, current_heading, current_content, content_is_list)


    # def _flatten_for_bold_sections(self, node: dict[str, Any], elements: list[dict]) -> None:
    #     """Flatten looking for bold elements as potential headings."""
    #     if not node or not isinstance(node, dict):
    #         return
        
    #     tag = node.get("tag", "")
    #     children = node.get("children", [])
        
    #     # Check for bold/strong at start of container
    #     if tag in self.BLOCK_TAGS and children:
    #         first_child = children[0]
    #         if first_child.get("tag") in ("strong", "b"):
    #             bold_text = self._get_node_text(first_child)
    #             remaining = " ".join(self._get_node_text(c) for c in children[1:]).strip()
                
    #             if bold_text and (not remaining or len(remaining) < 20):
    #                 elements.append({"_type": "bold_heading", "text": bold_text})
    #                 if remaining:
    #                     elements.append({"_type": "text", "text": remaining})
    #                 return
        
    #     # Standalone bold
    #     if tag in ("strong", "b"):
    #         text = self._get_node_text(node)
    #         if text and len(text) < 100:
    #             elements.append({"_type": "bold_heading", "text": text})
    #         return
        
    #     if tag in self.LIST_CONTAINER_TAGS:
    #         items = self._extract_list_items_recursive(node)
    #         if items:
    #             elements.append({"_type": "list", "items": items})
    #         return
        
    #     if tag == "p":
    #         text = self._get_node_text(node)
    #         if text:
    #             elements.append({"_type": "text", "text": text})
    #         return
        
    #     for child in children:
    #         self._flatten_for_bold_sections(child, elements)


    # # =========================================================================
    # # Helper Methods
    # # =========================================================================

    # def _get_node_text(self, node: dict[str, Any]) -> str:
    #     """Get text from node, preferring innerText."""
    #     if not node or not isinstance(node, dict):
    #         return ""
        
    #     if inner := node.get("innerText", "").strip():
    #         return inner
    #     if text := node.get("text", "").strip():
    #         return text
        
    #     parts = [self._get_node_text(c) for c in node.get("children", [])]
    #     return " ".join(p for p in parts if p).strip()


    # def _get_text_or_list(self, node: dict[str, Any]) -> Any:
    #     """Get text or extract as list if node contains ul/ol."""
    #     if not node or not isinstance(node, dict):
    #         return ""
        
    #     tag = node.get("tag", "")
        
    #     if tag in self.LIST_CONTAINER_TAGS:
    #         return self._extract_list_items_recursive(node)
        
    #     for child in node.get("children", []):
    #         if child.get("tag") in self.LIST_CONTAINER_TAGS:
    #             return self._extract_list_items_recursive(child)
        
    #     return self._get_node_text(node)


    # def _extract_list_items_recursive(self, list_node: dict[str, Any]) -> list[str]:
    #     """Extract all li items from ul/ol."""
    #     items = []
    #     for child in list_node.get("children", []):
    #         if child.get("tag") == "li":
    #             text = self._get_node_text(child)
    #             if text:
    #                 items.append(text)
    #     return items


    # def _looks_like_label(self, text: str) -> bool:
    #     """Check if text looks like a field label."""
    #     if not text:
    #         return False
        
    #     text_lower = text.lower().strip()
        
    #     for label in self.COMMON_JOB_LABELS:
    #         if label in text_lower or text_lower in label:
    #             return True
        
    #     return len(text) < 50


    # def _looks_like_section_heading(self, text: str) -> bool:
    #     """Check if text looks like a section heading."""
    #     if not text:
    #         return False
        
    #     text_lower = text.lower().strip()
        
    #     for heading in self.COMMON_SECTION_HEADINGS:
    #         if heading in text_lower or text_lower in heading:
    #             return True
        
    #     return len(text) < 80 and not text.endswith(".")


    # def _add_to_structured_result(self, result: dict[str, Any], label: str, value: Any) -> None:
    #     """Add label-value pair, avoiding duplicates."""
    #     if not label or not value:
    #         return
        
    #     label = label.rstrip(":").strip()
    #     label = " ".join(label.split())
        
    #     if label in result:
    #         existing = result[label]
    #         if isinstance(existing, str) and isinstance(value, str):
    #             if len(existing) >= len(value):
    #                 return
        
    #     result[label] = value


    # def _save_section(self, result: dict[str, Any], heading: str, content: list[str], is_list: bool) -> None:
    #     """Save heading section to result."""
    #     if not heading or not content:
    #         return
        
    #     heading = heading.rstrip(":").strip()
    #     heading = " ".join(heading.split())
        
    #     if heading in result:
    #         return
        
    #     result[heading] = content if is_list else " ".join(content).strip()


    # def _cleanup_structured_result(self, result: dict[str, Any]) -> dict[str, Any]:
    #     """Clean up the final result."""
    #     return {
    #         k: v for k, v in result.items()
    #         if v and (not isinstance(v, list) or len(v) > 0) and len(k) >= 2
    #     }







    


    def extract_structured_data(self, node: dict[str, Any]) -> dict[str, Any]:
        """
        Extract page content into structured dictionary.
        Returns both key-value pairs and sectioned content.
        """
        result: dict[str, Any] = {}
        extracted_texts: set[str] = set()  # Track what's been extracted
        
        # Phase 1: Extract explicit key-value patterns
        self._extract_definition_lists(node, result, extracted_texts)
        self._extract_table_pairs(node, result, extracted_texts)
        self._extract_inline_label_values(node, result, extracted_texts)
        
        # Phase 2: Extract sectioned content (headings + bold pseudo-headings)
        sections = self._extract_all_sections(node, extracted_texts)
        
        # Phase 3: Merge sections into result
        for section in sections:
            self._merge_section_to_result(section, result, extracted_texts)
        
        # Phase 4: Capture any remaining content not yet structured
        self._extract_remaining_content(node, result, extracted_texts)
        
        return self._cleanup_structured_result(result)

    # =========================================================================
    # Phase 1: Explicit Key-Value Extraction
    # =========================================================================

    def _extract_definition_lists(self, node: dict[str, Any], result: dict[str, Any], extracted_texts: set[str]) -> None:
        """Extract from <dl><dt>Label</dt><dd>Value</dd></dl> patterns."""
        if not node or not isinstance(node, dict):
            return
        
        tag = node.get("tag", "")
        
        if tag == "dl":
            children = node.get("children", [])
            i = 0
            while i < len(children):
                child = children[i]
                if child.get("tag") == "dt":
                    label = self._get_node_text(child)
                    # Look for dd (might not be immediately after)
                    j = i + 1
                    while j < len(children) and children[j].get("tag") not in ("dt", "dd"):
                        j += 1
                    if j < len(children) and children[j].get("tag") == "dd":
                        value = self._get_text_or_list(children[j])
                        if label and value:
                            self._add_to_result(result, label, value)
                            # Track extracted content
                            extracted_texts.add(label.lower().strip())
                            if isinstance(value, str):
                                extracted_texts.add(value.lower().strip())
                            elif isinstance(value, list):
                                for v in value:
                                    extracted_texts.add(v.lower().strip())
                        i = j + 1
                        continue
                i += 1
            return
        
        for child in node.get("children", []):
            self._extract_definition_lists(child, result, extracted_texts)

    def _extract_table_pairs(self, node: dict[str, Any], result: dict[str, Any], extracted_texts: set[str]) -> None:
        """Extract from table rows with label-value pattern."""
        if not node or not isinstance(node, dict):
            return
        
        tag = node.get("tag", "")
        
        if tag == "tr":
            children = node.get("children", [])
            cells = [c for c in children if c.get("tag") in self.TABLE_CELL_TAGS]
            
            if len(cells) == 2:
                label = self._get_node_text(cells[0])
                value = self._get_text_or_list(cells[1])
                if label and value and self._is_likely_label(label):
                    self._add_to_result(result, label, value)
                    # Track extracted content
                    extracted_texts.add(label.lower().strip())
                    if isinstance(value, str):
                        extracted_texts.add(value.lower().strip())
                    elif isinstance(value, list):
                        for v in value:
                            extracted_texts.add(v.lower().strip())
                    return
        
        for child in node.get("children", []):
            self._extract_table_pairs(child, result, extracted_texts)

            

   


    def _is_key_value_text(self, text: str) -> bool:
        """
        Determine if text is a key-value pair vs a regular sentence or URL.
        """
        if not text or ":" not in text:
            return False
        
        text_lower = text.lower().strip()
        
        # Skip URLs
        if text_lower.startswith(("http:", "https:", "ftp:", "//")):
            return False
        
        # Skip time patterns like "12:00pm", "8:30"
        import re
        if re.match(r'^\d{1,2}:\d{2}', text):
            return False
        
        parts = text.split(":", 1)
        label = parts[0].strip()
        value = parts[1].strip() if len(parts) > 1 else ""
        
        # Label should be short (< 40 chars)
        if len(label) > 40:
            return False
        
        # Label shouldn't be just numbers (like "12" from "12:00pm")
        if label.isdigit():
            return False
        
        # Label shouldn't start with URL-like patterns
        if label.lower() in self.SKIP_TEXT_PATTERNS:
            return False
        
        # Label shouldn't contain sentence-ending punctuation
        if any(p in label for p in ".!?"):
            return False
        
        # Label shouldn't have too many words
        label_words = label.split()
        if len(label_words) > 5:
            return False
        
        # If label is a known field, it's likely key-value
        if self._is_likely_label(label):
            return True
        
        # If value is very long, probably a sentence
        if len(value) > 100:
            return False
        
        # If value starts with sentence patterns
        value_lower = value.lower()
        sentence_starters = ("it ", "this ", "that ", "these ", "those ", "there ", 
                            "here ", "he ", "she ", "they ", "we ", "i ", "you ")
        if any(value_lower.startswith(s) for s in sentence_starters):
            return False
        
        # Short total text with reasonable label
        if len(text) < 60 and len(label_words) <= 3:
            return True
        
        return False


    def _is_likely_label(self, text: str) -> bool:
        """Check if text looks like a field label, not a sentence or number."""
        if not text:
            return False
        
        text_clean = text.strip()
        text_lower = text_clean.lower()
        
        # Reject pure numbers (like statistics "865,534")
        if text_clean.replace(",", "").replace(".", "").isdigit():
            return False
        
        # Reject URLs
        if text_lower.startswith(("http", "https", "www", "//", "ftp")):
            return False
        
        # Check against known labels first
        for label in self.COMMON_JOB_LABELS:
            if label in text_lower or text_lower in label:
                return True
        
        # Reject sentence-like patterns
        sentence_starters = (
            "this ", "that ", "these ", "those ", "there ", "here ",
            "it ", "he ", "she ", "they ", "we ", "i ", "you ",
            "the ", "a ", "an ", "my ", "your ", "his ", "her ", "our ", "their ",
            "if ", "when ", "while ", "after ", "before ", "because ", "since ",
            "what ", "how ", "why ", "where ", "who ", "which ",
        )
        if any(text_lower.startswith(s) for s in sentence_starters):
            return False
        
        # Reject if contains common verbs
        sentence_verbs = (" is ", " are ", " was ", " were ", " has ", " have ", 
                        " had ", " will ", " would ", " should ", " could ", " can ")
        if any(v in text_lower for v in sentence_verbs):
            return False
        
        # Short text with few words is more likely to be a label
        words = text_clean.split()
        if len(text_clean) < 40 and len(words) <= 4:
            return True
        
        return False


    def _should_skip_container(self, node: dict[str, Any]) -> bool:
        """Check if this container should be skipped entirely (nav, header, footer, etc.)"""
        if not node or not isinstance(node, dict):
            return False
        
        tag = node.get("tag", "")
        
        # Skip navigation containers
        if tag in self.SKIP_CONTAINER_TAGS:
            return True
        
        return False


    def _extract_inline_label_values(
        self, 
        node: dict[str, Any], 
        result: dict[str, Any],
        extracted_texts: set[str],
        parent_children: Optional[list] = None,
        index: int = 0
    ) -> None:
        """
        Extract key-value patterns from various HTML structures.
        """
        if not node or not isinstance(node, dict):
            return
        
        tag = node.get("tag", "")
        text = node.get("text", "").strip()
        children = node.get("children", [])
        
        # Skip navigation/header/footer containers entirely
        if self._should_skip_container(node):
            return
        
        # Pattern 1: "Label: Value" in same text node
        if text and ":" in text and not children:
            if self._is_key_value_text(text):
                parts = text.split(":", 1)
                label, value = parts[0].strip(), parts[1].strip()
                if label and value and self._is_likely_label(label):
                    self._add_to_result(result, label, value)
                    extracted_texts.add(label.lower().strip())
                    extracted_texts.add(value.lower().strip())
                    extracted_texts.add(text.lower().strip())
                    return
        
        # Pattern 2: <strong>Label:</strong> followed by value in same container
        if tag in self.BLOCK_TAGS or tag == "p":
            kv = self._extract_bold_label_value_in_block(node)
            if kv:
                self._add_to_result(result, kv[0], kv[1])
                extracted_texts.add(kv[0].lower().strip())
                extracted_texts.add(kv[1].lower().strip())
                full_text = self._get_node_text(node)
                if full_text:
                    extracted_texts.add(full_text.lower().strip())
                return
        
        # Pattern 3: <span>Label:</span><span>Value</span> as siblings
        if tag == "span" and text.endswith(":") and parent_children:
            if not self._is_fragmented_text_container(parent_children):
                label = text.rstrip(":").strip()
                
                # Skip if label doesn't look valid
                if not self._is_likely_label(label):
                    pass
                elif index + 1 < len(parent_children):
                    next_sibling = parent_children[index + 1]
                    next_text = self._get_node_text(next_sibling)
                    
                    # Skip if value is too short (fragment) or too long (paragraph)
                    if len(next_text.strip()) < 2 or len(next_text.strip()) > 200:
                        pass
                    elif next_sibling.get("tag") in self.INLINE_TAGS or not next_sibling.get("tag"):
                        self._add_to_result(result, label, next_text)
                        extracted_texts.add(label.lower().strip())
                        extracted_texts.add(next_text.lower().strip())
                        return
        
        # Pattern 4: Alternating <div>Label</div><div>Value</div> siblings
        # This handles job metadata tables common in job sites
        if tag == "div" and parent_children:
            self._extract_alternating_div_pairs(parent_children, result, extracted_texts)
        
        # Recurse into children
        for i, child in enumerate(children):
            self._extract_inline_label_values(child, result, extracted_texts, children, i)


    def _extract_alternating_div_pairs(
        self, 
        children: list[dict], 
        result: dict[str, Any],
        extracted_texts: set[str]
    ) -> None:
        """
        Extract key-value pairs from alternating div siblings:
        <div>Label</div><div>Value</div><div>Label2</div><div>Value2</div>
        """
        i = 0
        while i < len(children) - 1:
            current = children[i]
            next_item = children[i + 1]
            
            # Both must be divs
            if current.get("tag") != "div" or next_item.get("tag") != "div":
                i += 1
                continue
            
            current_text = current.get("text", "").strip()
            next_text = next_item.get("text", "").strip()
            
            # Current should have text that looks like a label
            # Next should have text that looks like a value (not a label)
            if (current_text 
                and next_text 
                and self._is_likely_label(current_text)
                and not self._is_likely_label(next_text)
                and current_text.lower() not in extracted_texts):
                
                # Check current div has minimal children (often just an icon span)
                current_children = current.get("children", [])
                has_only_empty_children = all(
                    not child.get("text", "").strip() 
                    for child in current_children
                )
                
                if len(current_children) <= 1 or has_only_empty_children:
                    self._add_to_result(result, current_text, next_text)
                    extracted_texts.add(current_text.lower().strip())
                    extracted_texts.add(next_text.lower().strip())
                    i += 2  # Skip both divs
                    continue
            
            i += 1







    def _extract_bold_label_value_in_block(self, node: dict[str, Any]) -> Optional[tuple[str, str]]:
        """
        Extract key-value from patterns like:
        <p><strong>Label:</strong> Value text here</p>
        <div><b>Label:</b> Value</div>
        
        Returns (label, value) tuple or None.
        """
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        if not children:
            return None
        
        first_child = children[0]
        if first_child.get("tag") not in self.BOLD_TAGS:
            return None
        
        bold_text = self._get_node_text(first_child).strip()
        
        # Must end with colon to be a label
        if not bold_text.endswith(":"):
            return None
        
        label = bold_text.rstrip(":").strip()
        
        if not self._is_likely_label(label):
            return None
        
        # Gather value from remaining content
        value_parts = []
        
        # Add any text directly in the parent after the bold
        if text:
            value_parts.append(text)
        
        # Add text from remaining children
        for child in children[1:]:
            child_text = self._get_node_text(child)
            if child_text:
                value_parts.append(child_text)
        
        value = " ".join(value_parts).strip()
        
        if value:
            return (label, value)
        
        return None

    # =========================================================================
    # Phase 2: Section Extraction (Headings and Bold Pseudo-Headings)
    # =========================================================================

    def _extract_all_sections(self, node: dict[str, Any], extracted_texts: set[str]) -> list[StructuredSection]:
        """
        Extract content organized by headings and bold pseudo-headings.
        Returns a list of sections, each potentially containing subsections.
        """
        # Flatten the DOM into a sequence of significant elements
        elements = self._flatten_to_elements(node, extracted_texts)
        
        # Build sections from the flattened elements
        sections = self._build_sections_from_elements(elements, extracted_texts)
        
        return sections

    def _flatten_to_elements(self, node: dict[str, Any], extracted_texts: set[str], depth: int = 0) -> list[dict]:
        """
        Flatten DOM into a list of significant elements:
        - headings (h1-h6)
        - bold_block (strong/b that acts as a header)
        - paragraph
        - list
        - line_break
        
        Skips content that was already extracted as key-values.
        """
        elements = []
        
        if not node or not isinstance(node, dict):
            return elements
        
        tag = node.get("tag", "")
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        # Actual headings
        if tag in self.HEADING_TAGS:
            heading_text = self._get_node_text(node)
            if heading_text:
                level = int(tag[1])
                elements.append({
                    "_type": "heading",
                    "level": level,
                    "text": heading_text,
                })
            return elements
        
        # Line breaks indicate content separation
        if tag == "br":
            elements.append({"_type": "line_break"})
            return elements
        
        # Lists
        if tag in self.LIST_CONTAINER_TAGS:
            items = self._extract_list_items(node)
            # Filter out items that were already extracted
            items = [item for item in items if item.lower().strip() not in extracted_texts]
            if items:
                elements.append({
                    "_type": "list",
                    "items": items,
                })
            return elements
        
        # Paragraphs - check for bold pseudo-heading pattern
        if tag == "p":
            # Check if this paragraph's content was already extracted
            full_text = self._get_node_text(node)
            if full_text and full_text.lower().strip() in extracted_texts:
                return elements  # Skip, already extracted
            
            elem = self._analyze_paragraph(node)
            if elem:
                # If it's a key_value_extracted marker, mark it but don't add content
                if elem.get("_type") == "key_value_extracted":
                    elements.append(elem)
                # Skip paragraphs whose content was already extracted
                elif elem.get("text", "").lower().strip() not in extracted_texts:
                    elements.append(elem)
            return elements
        
        # Block-level elements - check for bold at start
        if tag in self.BLOCK_TAGS:
            block_elements = self._analyze_block(node, extracted_texts)
            elements.extend(block_elements)
            return elements
        
        # Standalone bold/strong that could be a header
        if tag in self.BOLD_TAGS:
            bold_text = self._get_node_text(node)
            if bold_text and self._is_standalone_bold_header(node, bold_text):
                # Don't add as header if it's already been extracted as a key label
                if bold_text.lower().strip() not in extracted_texts:
                    elements.append({
                        "_type": "bold_header",
                        "text": bold_text,
                    })
            return elements
        
        # Recurse into children
        for child in children:
            elements.extend(self._flatten_to_elements(child, extracted_texts, depth + 1))
        
        # Handle any direct text content
        if text and tag not in self.HEADING_TAGS:
            if text.lower().strip() not in extracted_texts:
                elements.append({
                    "_type": "text",
                    "text": text,
                })
        
        return elements

    def _analyze_paragraph(self, node: dict[str, Any]) -> Optional[dict]:
        """
        Analyze a paragraph to determine its type:
        - bold_header: <p><strong>Header Text</strong></p>
        - key_value: <p><strong>Label:</strong> value</p> (marked to skip, handled in phase 1)
        - paragraph: regular paragraph text
        """
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        # Check if paragraph starts with bold
        if children and children[0].get("tag") in self.BOLD_TAGS:
            bold_child = children[0]
            bold_text = self._get_node_text(bold_child).strip()
            
            # Get remaining content after the bold
            remaining_parts = []
            if text:
                remaining_parts.append(text)
            for child in children[1:]:
                child_text = self._get_node_text(child)
                if child_text:
                    remaining_parts.append(child_text)
            remaining = " ".join(remaining_parts).strip()
            
            # Case 1: Bold with colon = key-value (handled in phase 1)
            # Mark as extracted so we don't duplicate
            if bold_text.endswith(":"):
                return {
                    "_type": "key_value_extracted",
                    "label": bold_text.rstrip(":").strip(),
                }
            
            # Case 2: Bold only, no remaining text = potential header
            elif not remaining or len(remaining) < 10:
                if self._looks_like_section_heading(bold_text):
                    return {
                        "_type": "bold_header",
                        "text": bold_text,
                    }
                # Short remaining text but bold isn't a heading - treat as paragraph
                elif remaining:
                    return {
                        "_type": "paragraph",
                        "text": f"{bold_text} {remaining}".strip(),
                    }
            
            # Case 3: Bold followed by significant text = paragraph with inline emphasis
            # This is NOT a header, just emphasis within flowing text
            else:
                full_text = f"{bold_text} {remaining}".strip()
                return {
                    "_type": "paragraph",
                    "text": full_text,
                    "has_inline_emphasis": True,  # Mark that bold is inline, not structural
                }
        
        # Regular paragraph
        full_text = self._get_node_text(node)
        if full_text:
            return {
                "_type": "paragraph",
                "text": full_text,
            }
        
        return None

    def _analyze_block(self, node: dict[str, Any], extracted_texts: set[str]) -> list[dict]:
        """
        Analyze a block element (div, section, etc.) for structure.
        Content found in this block stays within this block's scope.
        """
        elements = []
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        # Check if block starts with bold as a header
        if children and children[0].get("tag") in self.BOLD_TAGS:
            bold_child = children[0]
            bold_text = self._get_node_text(bold_child).strip()
            
            # Check if this is a standalone bold header (not inline emphasis)
            has_break_after = False
            remaining_starts_new_block = False
            
            if len(children) > 1:
                second_child = children[1]
                second_tag = second_child.get("tag", "")
                
                # Line break after bold indicates it's a header
                if second_tag == "br":
                    has_break_after = True
                # Block element after bold indicates it's a header
                elif second_tag in self.BLOCK_TAGS or second_tag in self.LIST_CONTAINER_TAGS:
                    remaining_starts_new_block = True
            
            # Bold with colon at block start = key-value line
            if bold_text.endswith(":") and not has_break_after:
                # Handled in phase 1, mark as extracted
                elements.append({
                    "_type": "key_value_extracted",
                    "label": bold_text.rstrip(":").strip(),
                })
                return elements
            
            # Bold followed by break or block = header for this container only
            elif (has_break_after or remaining_starts_new_block or len(children) == 1) and self._looks_like_section_heading(bold_text):
                # Don't add if already extracted
                if bold_text.lower().strip() not in extracted_texts:
                    elements.append({
                        "_type": "bold_header",
                        "text": bold_text,
                        "scoped": True,  # Mark as scoped to this container
                    })
                
                # Process remaining children within this container
                start_idx = 2 if has_break_after else 1
                for child in children[start_idx:]:
                    elements.extend(self._flatten_to_elements(child, extracted_texts))
                
                # Mark end of scoped section
                elements.append({"_type": "scope_end"})
                
                return elements
        
        # No special pattern, recurse normally
        for child in children:
            elements.extend(self._flatten_to_elements(child, extracted_texts))
        
        if text and text.lower().strip() not in extracted_texts:
            elements.append({"_type": "text", "text": text})
        
        return elements

    def _is_standalone_bold_header(self, node: dict[str, Any], text: str) -> bool:
        """
        Determine if a bold element is a standalone header vs inline emphasis.
        """
        # Must look like a section heading
        if not self._looks_like_section_heading(text):
            return False
        
        # Shouldn't end with colon (that's a label)
        if text.endswith(":"):
            return False
        
        # Shouldn't be too long
        if len(text) > 100:
            return False
        
        return True

    def _build_sections_from_elements(self, elements: list[dict], extracted_texts: set[str]) -> list[StructuredSection]:
        """
        Build structured sections from flattened elements.
        Respects container scoping for bold headers.
        """
        sections = []
        current_section: Optional[StructuredSection] = None
        in_scoped_section = False
        
        for elem in elements:
            elem_type = elem.get("_type")
            
            # Track key-values that were already extracted in phase 1
            if elem_type == "key_value_extracted":
                continue
            
            # End of scoped section - close current section if it was scoped
            if elem_type == "scope_end":
                if current_section and in_scoped_section:
                    sections.append(current_section)
                    current_section = None
                    in_scoped_section = False
                continue
            
            # Real headings (h1-h6) always start new sections and capture subsequent content
            if elem_type == "heading":
                # Save current section
                if current_section:
                    sections.append(current_section)
                
                # Start new section (not scoped - captures until next heading)
                current_section = StructuredSection(
                    heading=elem.get("text", "")
                )
                in_scoped_section = False
            
            # Bold headers - may be scoped to their container
            elif elem_type == "bold_header":
                # Save current section
                if current_section:
                    sections.append(current_section)
                
                # Start new section
                current_section = StructuredSection(
                    heading=elem.get("text", "")
                )
                # Check if this is a scoped section (only captures content in same container)
                in_scoped_section = elem.get("scoped", False)
            
            elif elem_type == "list":
                items = elem.get("items", [])
                # Filter out already extracted items
                items = [item for item in items if item.lower().strip() not in extracted_texts]
                if items:
                    if current_section:
                        current_section.content.extend(items)
                    else:
                        # List without header - create anonymous section
                        current_section = StructuredSection()
                        current_section.content.extend(items)
            
            elif elem_type in ("paragraph", "text"):
                text = elem.get("text", "")
                if text and text.lower().strip() not in extracted_texts:
                    # If we're in a scoped section, content goes there
                    # If not, content goes to current section or starts new anonymous section
                    if current_section:
                        current_section.content.append(text)
                    else:
                        current_section = StructuredSection()
                        current_section.content.append(text)
            
            elif elem_type == "line_break":
                # Line breaks are just separators, don't affect structure
                pass
        
        # Don't forget the last section
        if current_section:
            sections.append(current_section)
        
        return sections

    def _merge_section_to_result(self, section: StructuredSection, result: dict[str, Any], extracted_texts: set[str]) -> None:
        """
        Merge a section into the result dictionary.
        """
        if not section.heading:
            # Anonymous section - add content to a general key
            if section.content:
                if "_content" not in result:
                    result["_content"] = []
                for content in section.content:
                    if content.lower().strip() not in extracted_texts:
                        result["_content"].append(content)
                        extracted_texts.add(content.lower().strip())
            return
        
        heading = section.heading.rstrip(":").strip()
        
        # Skip if already exists with same or more content
        if heading in result:
            existing = result[heading]
            new_content = section.content
            if isinstance(existing, list) and isinstance(new_content, list):
                if len(existing) >= len(new_content):
                    return
            elif isinstance(existing, str) and isinstance(new_content, list):
                if len(existing) >= len(" ".join(new_content)):
                    return
        
        # Filter content that was already extracted
        filtered_content = [c for c in section.content if c.lower().strip() not in extracted_texts]
        
        if not filtered_content:
            return
        
        # Add section content
        if len(filtered_content) == 1:
            result[heading] = filtered_content[0]
            extracted_texts.add(filtered_content[0].lower().strip())
        elif filtered_content:
            result[heading] = filtered_content
            for c in filtered_content:
                extracted_texts.add(c.lower().strip())
        
        # Track the heading
        extracted_texts.add(heading.lower().strip())
        
        # Merge key-values
        for k, v in section.key_values.items():
            self._add_to_result(result, k, v)

    # =========================================================================
    # Phase 4: Remaining Content Extraction
    # =========================================================================

    def _extract_remaining_content(self, node: dict[str, Any], result: dict[str, Any], extracted_texts: set[str]) -> None:
        """
        Capture any content not already in result.
        Uses word-level overlap detection to avoid duplicates.
        """
        all_text_blocks = self._collect_all_text_blocks(node)
        
        # Find uncaptured content
        uncaptured = []
        for block in all_text_blocks:
            block_clean = block.strip()
            block_lower = block_clean.lower()
            if not block_clean:
                continue
            
            # Skip if exact match
            if block_lower in extracted_texts:
                continue
            
            # Check for significant word overlap
            block_words = set(block_lower.split())
            is_captured = False
            
            for cap in extracted_texts:
                # Skip very short captured texts for comparison
                if len(cap) < 10:
                    continue
                
                cap_words = set(cap.split())
                
                # Check for substring match
                if block_lower in cap or cap in block_lower:
                    is_captured = True
                    break
                
                # Check for significant word overlap (>70% of words match)
                if block_words and cap_words:
                    common_words = block_words & cap_words
                    overlap_ratio = len(common_words) / min(len(block_words), len(cap_words))
                    if overlap_ratio > 0.7:
                        is_captured = True
                        break
            
            if not is_captured:
                uncaptured.append(block_clean)
                extracted_texts.add(block_lower)
        
        if uncaptured:
            if "_additional_content" not in result:
                result["_additional_content"] = []
            result["_additional_content"].extend(uncaptured)

    def _collect_all_text_blocks(self, node: dict[str, Any]) -> list[str]:
        """
        Collect all text blocks from the DOM.
        """
        blocks = []
        
        if not node or not isinstance(node, dict):
            return blocks
        
        tag = node.get("tag", "")
        
        # Skip certain tags
        if tag in ("script", "style", "nav", "footer", "header"):
            return blocks
        
        # For paragraphs and list items, get full text
        if tag in ("p", "li"):
            text = self._get_node_text(node)
            if text and len(text) > 10:  # Skip very short fragments
                blocks.append(text)
            return blocks
        
        # Recurse
        for child in node.get("children", []):
            blocks.extend(self._collect_all_text_blocks(child))
        
        return blocks

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_node_text(self, node: dict[str, Any]) -> str:
        """Get all text from node, combining children then any direct text."""
        if not node or not isinstance(node, dict):
            return ""
        
        # Prefer innerText if available (it's already in correct order)
        if inner := node.get("innerText", "").strip():
            return inner
        
        parts = []
        
        # First, get text from children (they appear first in DOM order typically)
        for child in node.get("children", []):
            child_text = self._get_node_text(child)
            if child_text:
                parts.append(child_text)
        
        # Then add any direct text (appears after children in DOM structure)
        # Note: This is a simplification; real DOM might interleave text nodes
        if text := node.get("text", "").strip():
            parts.append(text)
        
        return " ".join(parts).strip()

    def _get_text_or_list(self, node: dict[str, Any]) -> Any:
        """Get text or extract as list if node contains ul/ol."""
        if not node or not isinstance(node, dict):
            return ""
        
        tag = node.get("tag", "")
        
        if tag in self.LIST_CONTAINER_TAGS:
            return self._extract_list_items(node)
        
        for child in node.get("children", []):
            if child.get("tag") in self.LIST_CONTAINER_TAGS:
                return self._extract_list_items(child)
        
        return self._get_node_text(node)

    def _extract_list_items(self, list_node: dict[str, Any]) -> list[str]:
        """Extract all li items from ul/ol."""
        items = []
        for child in list_node.get("children", []):
            if child.get("tag") == "li":
                text = self._get_node_text(child)
                if text:
                    items.append(text)
        return items

    def _is_likely_label(self, text: str) -> bool:
        """Check if text looks like a field label, not a sentence."""
        if not text:
            return False
        
        text_clean = text.strip()
        text_lower = text_clean.lower()
        
        # Check against known labels first
        for label in self.COMMON_JOB_LABELS:
            if label in text_lower or text_lower in label:
                return True
        
        # Reject if starts with sentence-like patterns
        sentence_starters = (
            "this ", "that ", "these ", "those ", "there ", "here ",
            "it ", "he ", "she ", "they ", "we ", "i ", "you ",
            "the ", "a ", "an ", "my ", "your ", "his ", "her ", "our ", "their ",
            "if ", "when ", "while ", "after ", "before ", "because ", "since ",
            "what ", "how ", "why ", "where ", "who ", "which ",
        )
        if any(text_lower.startswith(s) for s in sentence_starters):
            return False
        
        # Reject if contains common verbs that suggest it's a sentence
        sentence_verbs = (" is ", " are ", " was ", " were ", " has ", " have ", " had ", " will ", " would ", " should ", " could ", " can ")
        if any(v in text_lower for v in sentence_verbs):
            return False
        
        # Short text with few words is more likely to be a label
        words = text_clean.split()
        if len(text_clean) < 40 and len(words) <= 4:
            return True
        
        return False

    def _looks_like_section_heading(self, text: str) -> bool:
        """Check if text looks like a section heading."""
        if not text:
            return False
        
        text_lower = text.lower().strip()
        
        # Check against known headings
        for heading in self.COMMON_SECTION_HEADINGS:
            if heading in text_lower or text_lower in heading:
                return True
        
        # Heuristics for headings
        if len(text) > 100:
            return False
        
        if text.endswith("."):
            return False
        
        # Title case or all caps suggests heading
        if text.istitle() or text.isupper():
            return True
        
        # Short text without sentence punctuation
        if len(text) < 60 and not any(p in text for p in ".!?"):
            return True
        
        return False

    def _add_to_result(self, result: dict[str, Any], label: str, value: Any) -> None:
        """Add label-value pair, handling duplicates intelligently."""
        if not label or not value:
            return
        
        # Clean label
        label = label.rstrip(":").strip()
        label = " ".join(label.split())
        
        if not label:
            return
        
        # Handle existing value
        if label in result:
            existing = result[label]
            # Keep longer/more detailed value
            if isinstance(existing, str) and isinstance(value, str):
                if len(value) > len(existing):
                    result[label] = value
            elif isinstance(existing, list) and isinstance(value, list):
                if len(value) > len(existing):
                    result[label] = value
            # Keep existing if same type and same/longer
            return
        
        result[label] = value

    def _cleanup_structured_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Clean up the final result."""
        cleaned = {}
        
        for k, v in result.items():
            # Skip empty values
            if not v:
                continue
            
            # Skip very short keys
            if len(k) < 2:
                continue
            
            # Clean up string values
            if isinstance(v, str):
                v = " ".join(v.split()).strip()
                if not v:
                    continue
            
            # Clean up list values
            if isinstance(v, list):
                v = [item.strip() if isinstance(item, str) else item for item in v]
                v = [item for item in v if item]
                if not v:
                    continue
            
            cleaned[k] = v
        
        return cleaned
    
    def _is_fragmented_text_container(self, children: list[dict]) -> bool:
        """
        Detect Microsoft Word-style HTML where text is split across many spans.
        """
        if not children or len(children) <= 5:
            return False
        
        span_count = 0
        short_text_count = 0
        
        for child in children:
            if not isinstance(child, dict):
                continue
            if child.get("tag") == "span":
                span_count += 1
                text = child.get("text", "") or child.get("innerText", "")
                if len(text.strip()) < 20:
                    short_text_count += 1
        
        if span_count >= 3 and short_text_count / max(span_count, 1) > 0.5:
            return True
        
        return False





# import re
# import uuid
# from dataclasses import dataclass, field
# from typing import Any, Optional
# from enum import Enum
# from datetime import datetime


# # =========================================================================
# # Enums
# # =========================================================================

# class ContractType(str, Enum):
#     PERMANENT = "permanent"
#     TEMPORARY = "temporary"
#     FIXED_TERM = "fixed-term"
#     CONTRACT = "contract"
#     FREELANCE = "freelance"
#     INTERNSHIP = "internship"
#     APPRENTICESHIP = "apprenticeship"
#     ZERO_HOURS = "zero-hours"
#     CASUAL = "casual"
#     VOLUNTEER = "volunteer"
#     SECONDMENT = "secondment"


# class JobType(str, Enum):
#     FULL_TIME = "full-time"
#     PART_TIME = "part-time"


# class RemoteOption(str, Enum):
#     REMOTE = "remote"
#     HYBRID = "hybrid"
#     ON_SITE = "on-site"


# class ApplicationMethod(str, Enum):
#     CV = "cv"
#     APPLICATION_FORM = "application-form"
#     EMAIL = "email"
#     ONLINE_PORTAL = "online-portal"
#     EXTERNAL_LINK = "external-link"


# # =========================================================================
# # Data Classes
# # =========================================================================

# @dataclass
# class LocationInfo:
#     raw: str
#     is_uk_based: bool
#     postcode: Optional[str] = None
#     city: Optional[str] = None
#     region: Optional[str] = None
#     country: Optional[str] = None
#     is_remote: bool = False


# @dataclass
# class SalaryInfo:
#     raw: str
#     min_value: Optional[float] = None
#     max_value: Optional[float] = None
#     currency: str = "GBP"
#     period: str = "annual"  # annual, monthly, daily, hourly
#     is_negotiable: bool = False
#     includes_benefits: bool = False


# @dataclass
# class ContactInfo:
#     name: Optional[str] = None
#     email: Optional[str] = None
#     job_title: Optional[str] = None
#     phone: Optional[str] = None
    
#     def to_dict(self) -> dict[str, Any]:
#         return {
#             "name": self.name,
#             "email": self.email,
#             "job_title": self.job_title,
#             "phone": self.phone,
#         }


# @dataclass
# class JobFields:
#     """Extracted job-specific fields"""
#     # IDs
#     hireful_job_id: Optional[str] = None
#     employer_job_id: Optional[str] = None
    
#     # Timestamps
#     date_created: Optional[str] = None
    
#     # Core Job Info
#     job_title: Optional[str] = None
#     job_description: Optional[str] = None
#     word_count: Optional[int] = None
    
#     # Employment Details
#     contract_type: Optional[str] = None
#     job_type: Optional[str] = None
#     working_hours: Optional[str] = None
#     remote_option: Optional[str] = None
    
#     # Location
#     location: Optional[LocationInfo] = None
#     location_postcode: Optional[str] = None
    
#     # Compensation & Benefits
#     salary: Optional[SalaryInfo] = None
#     holiday: Optional[str] = None
#     benefits: Optional[list[str]] = None
    
#     # Important Dates
#     closing_date: Optional[str] = None
#     interview_date: Optional[str] = None
#     start_date: Optional[str] = None
    
#     # Application Info
#     application_method: Optional[str] = None
    
#     # Contact Info
#     contact: Optional[ContactInfo] = None
    
#     def to_dict(self) -> dict[str, Any]:
#         result = {
#             "hireful_job_id": self.hireful_job_id,
#             "employer_job_id": self.employer_job_id,
#             "date_created": self.date_created,
#             "job_title": self.job_title,
#             "job_description": self.job_description,
#             "word_count": self.word_count,
#             "contract_type": self.contract_type,
#             "job_type": self.job_type,
#             "working_hours": self.working_hours,
#             "remote_option": self.remote_option,
#         }
        
#         # Location
#         if self.location:
#             result["location"] = {
#                 "raw": self.location.raw,
#                 "is_uk_based": self.location.is_uk_based,
#                 "is_remote": self.location.is_remote,
#             }
#             if self.location.city:
#                 result["location"]["city"] = self.location.city
#             if self.location.region:
#                 result["location"]["region"] = self.location.region
#             if self.location.country:
#                 result["location"]["country"] = self.location.country
#             if self.location.postcode:
#                 result["location"]["postcode"] = self.location.postcode
#         else:
#             result["location"] = None
            
#         result["location_postcode"] = self.location_postcode
        
#         # Salary
#         if self.salary:
#             result["salary"] = {
#                 "raw": self.salary.raw,
#                 "min": self.salary.min_value,
#                 "max": self.salary.max_value,
#                 "currency": self.salary.currency,
#                 "period": self.salary.period,
#                 "is_negotiable": self.salary.is_negotiable,
#             }
#         else:
#             result["salary"] = None
        
#         result["holiday"] = self.holiday
#         result["benefits"] = self.benefits
#         result["closing_date"] = self.closing_date
#         result["interview_date"] = self.interview_date
#         result["start_date"] = self.start_date
#         result["application_method"] = self.application_method
        
#         # Contact
#         if self.contact:
#             result["contact"] = self.contact.to_dict()
#         else:
#             result["contact"] = None
        
#         return result


# class JobFieldExtractor:
#     """
#     Specialized extractor for job posting fields.
#     Works with raw DOM structure to find specific field values with high accuracy.
#     """
    
#     # =========================================================================
#     # Job ID Patterns
#     # =========================================================================
    
#     JOB_ID_LABELS = frozenset({
#         'job id', 'job ref', 'job reference', 'reference', 'ref',
#         'job number', 'job no', 'job #', 'vacancy id', 'vacancy ref',
#         'vacancy reference', 'position id', 'position ref', 'requisition id',
#         'req id', 'req', 'job req. id', 'job req id', 'reference number',
#         'ref no', 'ref.', 'id', 'posting id', 'advert ref', 'advert reference',
#         'role id', 'role ref', 'role reference', 'opportunity id',
#     })
    
#     JOB_ID_PATTERNS = [
#         # Alphanumeric IDs: ABC-123, ABC123, 123-ABC
#         r'(?:job\s*(?:id|ref|reference|no|number|#)?[:\s]*)?([A-Z]{2,5}[-_]?\d{3,10})',
#         r'(?:ref(?:erence)?[:\s]*)?(\d{3,10}[-_]?[A-Z]{2,5})',
        
#         # Pure numeric: 12345, 1234567
#         r'(?:(?:job|vacancy|position|req(?:uisition)?)\s*(?:id|ref|no|number|#)?[:\s]*)(\d{4,10})',
        
#         # UUID-style
#         r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
        
#         # Custom formats: JOB/2024/001, VAC-2024-001
#         r'((?:JOB|VAC|REQ|POS)[/\-_]\d{4}[/\-_]\d{2,5})',
        
#         # Workday style: JR-000123
#         r'(JR[-_]\d{6,})',
        
#         # SuccessFactors style: 1754
#         r'(?:requisition\s*(?:id)?[:\s]*)(\d{4,6})',
#     ]
    
#     # =========================================================================
#     # UK Location Patterns (expanded)
#     # =========================================================================
    
#     UK_CITIES = frozenset({
#         # England - Major Cities
#         "london", "birmingham", "manchester", "leeds", "liverpool",
#         "newcastle", "sheffield", "bristol", "nottingham", "leicester",
#         "coventry", "bradford", "hull", "stoke", "wolverhampton",
#         "plymouth", "derby", "southampton", "portsmouth", "brighton",
#         "reading", "luton", "bolton", "bournemouth", "middlesbrough",
#         "swindon", "peterborough", "southend", "sunderland", "crawley",
#         "oxford", "cambridge", "ipswich", "norwich", "gloucester",
#         "exeter", "lincoln", "chester", "carlisle", "york",
#         "bath", "canterbury", "winchester", "durham", "worcester",
#         "salisbury", "chichester", "ely", "wells", "truro",
#         "milton keynes", "northampton", "warrington", "slough", "watford",
#         "basingstoke", "guildford", "chelmsford", "maidstone", "colchester",
#         "harrogate", "scarborough", "blackpool", "preston", "blackburn",
#         "burnley", "rochdale", "oldham", "wigan", "stockport",
#         "salford", "bury", "huddersfield", "wakefield", "doncaster",
#         "rotherham", "barnsley", "scunthorpe", "grimsby", "chesterfield",
#         "mansfield", "worksop", "newark", "grantham", "boston",
#         "spalding", "king's lynn", "great yarmouth", "lowestoft", "thetford",
#         "bury st edmunds", "sudbury", "felixstowe", "harwich", "clacton",
#         "braintree", "witham", "maldon", "southend-on-sea", "basildon",
#         "grays", "tilbury", "romford", "dagenham", "ilford", "barking",
#         "stratford", "walthamstow", "tottenham", "edmonton", "enfield",
#         "potters bar", "barnet", "edgware", "stanmore", "harrow",
#         "wembley", "ealing", "acton", "chiswick", "hammersmith",
#         "fulham", "putney", "wandsworth", "battersea", "clapham",
#         "brixton", "streatham", "croydon", "sutton", "epsom",
#         "kingston", "richmond", "twickenham", "hounslow", "feltham",
#         "staines", "egham", "woking", "weybridge", "esher",
#         "leatherhead", "dorking", "reigate", "redhill", "horley",
#         "gatwick", "east grinstead", "haywards heath", "burgess hill",
#         "lewes", "eastbourne", "hastings", "bexhill", "rye",
#         "ashford", "folkestone", "dover", "deal", "ramsgate",
#         "margate", "broadstairs", "whitstable", "herne bay", "faversham",
#         "sittingbourne", "gillingham", "chatham", "rochester", "gravesend",
#         "dartford", "bexley", "bromley", "orpington", "sevenoaks",
#         "tunbridge wells", "tonbridge", "maidstone", "aylesford",
        
#         # Scotland
#         "edinburgh", "glasgow", "aberdeen", "dundee", "inverness",
#         "stirling", "perth", "paisley", "livingston", "kirkcaldy",
#         "ayr", "kilmarnock", "greenock", "dunfermline", "falkirk",
#         "cumbernauld", "east kilbride", "hamilton", "motherwell", "coatbridge",
#         "airdrie", "bathgate", "arbroath", "montrose", "forfar",
#         "brechin", "stonehaven", "elgin", "forres", "nairn",
#         "fort william", "oban", "campbeltown", "dumbarton", "helensburgh",
        
#         # Wales
#         "cardiff", "swansea", "newport", "wrexham", "barry",
#         "neath", "bridgend", "cwmbran", "llanelli", "pontypridd",
#         "caerphilly", "bangor", "colwyn bay", "aberystwyth",
#         "rhyl", "prestatyn", "denbigh", "mold", "flint",
#         "holyhead", "llandudno", "conwy", "carmarthen", "pembroke",
#         "haverfordwest", "milford haven", "tenby", "brecon", "merthyr tydfil",
#         "ebbw vale", "tredegar", "aberdare", "mountain ash", "porth",
        
#         # Northern Ireland
#         "belfast", "derry", "londonderry", "lisburn", "newtownabbey",
#         "bangor", "craigavon", "newry", "ballymena", "carrickfergus",
#         "newtownards", "coleraine", "omagh", "enniskillen", "dungannon",
#         "cookstown", "magherafelt", "limavady", "strabane", "larne",
#         "antrim", "downpatrick", "ballycastle", "portrush", "portstewart",
#     })
    
#     UK_REGIONS = frozenset({
#         # England Regions
#         "east anglia", "east midlands", "west midlands", "north east",
#         "north west", "south east", "south west", "yorkshire",
#         "yorkshire and the humber", "east of england", "greater london",
#         "home counties", "the midlands", "midlands",
        
#         # Counties
#         "bedfordshire", "berkshire", "buckinghamshire", "cambridgeshire",
#         "cheshire", "cornwall", "cumbria", "derbyshire", "devon",
#         "dorset", "durham", "east sussex", "essex", "gloucestershire",
#         "hampshire", "herefordshire", "hertfordshire", "kent",
#         "lancashire", "leicestershire", "lincolnshire", "norfolk",
#         "north yorkshire", "northamptonshire", "northumberland",
#         "nottinghamshire", "oxfordshire", "rutland", "shropshire",
#         "somerset", "south yorkshire", "staffordshire", "suffolk",
#         "surrey", "tyne and wear", "warwickshire", "west midlands",
#         "west sussex", "west yorkshire", "wiltshire", "worcestershire",
#         "isle of wight", "city of london", "greater manchester",
#         "merseyside", "south gloucestershire", "bristol",
        
#         # Scotland Regions
#         "scottish highlands", "highlands", "lowlands", "scottish borders",
#         "central scotland", "fife", "tayside", "grampian", "lothian",
#         "strathclyde", "dumfries and galloway", "argyll and bute",
#         "aberdeenshire", "angus", "clackmannanshire", "east ayrshire",
#         "east dunbartonshire", "east lothian", "east renfrewshire",
#         "falkirk", "glasgow city", "inverclyde", "midlothian",
#         "moray", "north ayrshire", "north lanarkshire", "orkney",
#         "perth and kinross", "renfrewshire", "scottish borders",
#         "shetland", "south ayrshire", "south lanarkshire", "stirling",
#         "west dunbartonshire", "west lothian", "western isles",
        
#         # Wales Regions
#         "north wales", "south wales", "mid wales", "west wales",
#         "glamorgan", "gwent", "powys", "dyfed", "clwyd", "gwynedd",
#         "ceredigion", "pembrokeshire", "carmarthenshire", "monmouthshire",
#         "blaenau gwent", "torfaen", "caerphilly", "rhondda cynon taf",
#         "vale of glamorgan", "bridgend", "neath port talbot", "swansea",
#         "isle of anglesey", "conwy", "denbighshire", "flintshire",
#         "wrexham",
        
#         # Northern Ireland
#         "county antrim", "county armagh", "county down", "county fermanagh",
#         "county londonderry", "county tyrone",
#     })
    
#     UK_COUNTRY_MARKERS = frozenset({
#         "uk", "u.k.", "u.k", "united kingdom", "great britain", "gb", "g.b.",
#         "britain", "england", "scotland", "wales", "northern ireland",
#         "british", "scottish", "welsh", "english", "ni",
#     })
    
#     # UK Postcode regex - comprehensive pattern
#     UK_POSTCODE_PATTERN = re.compile(
#         r'\b('
#         # Standard formats
#         r'[A-Z]{1,2}[0-9][0-9A-Z]?\s*[0-9][A-Z]{2}|'
#         # Outward code only (first part)
#         r'[A-Z]{1,2}[0-9][0-9A-Z]?'
#         r')\b',
#         re.IGNORECASE
#     )
    
#     # Full UK postcode with both parts
#     UK_FULL_POSTCODE_PATTERN = re.compile(
#         r'\b([A-Z]{1,2}[0-9][0-9A-Z]?\s*[0-9][A-Z]{2})\b',
#         re.IGNORECASE
#     )
    
#     # =========================================================================
#     # Contract Type Patterns
#     # =========================================================================
    
#     CONTRACT_PATTERNS = {
#         ContractType.PERMANENT: [
#             r'\bpermanent\b',
#             r'\bperm\b',
#             r'\bindefinite\s+contract\b',
#             r'\bopen[\-\s]ended\b',
#             r'\bstaff\s+position\b',
#             r'\bdirect\s+hire\b',
#             r'\bsubstantive\b',
#             r'\bestablished\s+post\b',
#         ],
#         ContractType.TEMPORARY: [
#             r'\btemporary\b',
#             r'\btemp\b(?!\w)',
#             r'\bshort[\-\s]term\b',
#             r'\bseasonal\b',
#             r'\bmaternity\s+cover\b',
#             r'\bpaternity\s+cover\b',
#             r'\bsickness\s+cover\b',
#             r'\bholiday\s+cover\b',
#             r'\bcover\s+(?:for|position)\b',
#             r'\bacting\s+(?:up|position)\b',
#         ],
#         ContractType.FIXED_TERM: [
#             r'\bfixed[\-\s]term\b',
#             r'\bftc\b',
#             r'\b\d+[\-\s](?:month|year|week)s?\s+contract\b',
#             r'\bcontract\s+(?:for\s+)?\d+\s+(?:month|year|week)s?\b',
#             r'\blimited[\-\s]term\b',
#             r'\bdefinite[\-\s]term\b',
#             r'\b(?:until|ending)\s+\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\b',
#             r'\bproject[\-\s]based\s+contract\b',
#         ],
#         ContractType.CONTRACT: [
#             r'\bcontract(?:or)?\b(?!\s+type)',
#             r'\boutside\s+ir35\b',
#             r'\binside\s+ir35\b',
#             r'\bir35\b',
#             r'\bltd\s+company\b',
#             r'\bumbrella\s+(?:company|contract)\b',
#             r'\bself[\-\s]employed\b',
#             r'\b1099\b',
#             r'\bc2c\b',
#             r'\bcorp[\-\s]to[\-\s]corp\b',
#             r'\bday\s+rate\b',
#         ],
#         ContractType.FREELANCE: [
#             r'\bfreelance\b',
#             r'\bfreelancer\b',
#             r'\bgig\b',
#             r'\bproject[\-\s]based\b(?!\s+contract)',
#             r'\bad[\-\s]hoc\b',
#             r'\bconsultant\b',
#         ],
#         ContractType.INTERNSHIP: [
#             r'\binternship\b',
#             r'\bintern\b(?!al)',
#             r'\bplacement\s+(?:year|student)\b',
#             r'\bgraduate\s+(?:scheme|program(?:me)?)\b',
#             r'\bwork\s+experience\b',
#             r'\bindustrial\s+placement\b',
#             r'\bsandwich\s+(?:year|placement)\b',
#             r'\byear\s+in\s+industry\b',
#         ],
#         ContractType.APPRENTICESHIP: [
#             r'\bapprenticeship\b',
#             r'\bapprentice\b',
#             r'\btrainee(?:ship)?\b',
#             r'\bschool\s+leaver\s+(?:scheme|program(?:me)?)\b',
#         ],
#         ContractType.ZERO_HOURS: [
#             r'\bzero[\-\s]hours?\b',
#             r'\b0[\-\s]hours?\b',
#             r'\bcasual\s+contract\b',
#             r'\bbank\s+staff\b',
#             r'\brelief\s+(?:staff|worker)\b',
#             r'\bflexible\s+hours\s+contract\b',
#             r'\bas\s+and\s+when\s+required\b',
#         ],
#         ContractType.VOLUNTEER: [
#             r'\bvolunteer\b',
#             r'\bvoluntary\b',
#             r'\bunpaid\b',
#             r'\bpro[\-\s]bono\b',
#             r'\bexpenses[\-\s]only\b',
#         ],
#         ContractType.SECONDMENT: [
#             r'\bsecondment\b',
#             r'\bseconded\b',
#             r'\bloan\s+(?:arrangement|position)\b',
#         ],
#     }
    
#     # =========================================================================
#     # Job Type Patterns
#     # =========================================================================
    
#     JOB_TYPE_PATTERNS = {
#         JobType.FULL_TIME: [
#             r'\bfull[\-\s]time\b',
#             r'\bfulltime\b',
#             r'\bft\b(?!\s*contract)',
#             r'\b35\+?\s*(?:hours?|hrs?)\s*(?:per|\/|a)?\s*(?:week|wk)?\b',
#             r'\b37\.?5\s*(?:hours?|hrs?)\b',
#             r'\b40\s*(?:hours?|hrs?)\b',
#             r'\bstandard\s+hours\b',
#         ],
#         JobType.PART_TIME: [
#             r'\bpart[\-\s]time\b',
#             r'\bparttime\b',
#             r'\bpt\b(?!\s*contract)',
#             r'\bjob[\-\s]share\b',
#             r'\breduced\s+hours\b',
#             r'\bcompressed\s+hours\b',
#             r'\bterm[\-\s]time\s+only\b',
#             r'\b(?:2|3|4)\s*days?\s*(?:per|a|\/)\s*week\b',
#             r'\b\d{1,2}\s*(?:hours?|hrs?)\s*(?:per|\/|a)\s*(?:week|wk)\b(?=.*part)',
#         ],
#     }
    
#     # =========================================================================
#     # Remote Option Patterns
#     # =========================================================================
    
#     REMOTE_PATTERNS = {
#         RemoteOption.REMOTE: [
#             r'\bfully\s+remote\b',
#             r'\b100%\s+remote\b',
#             r'\bremote[\-\s]first\b',
#             r'\bremote[\-\s]only\b',
#             r'\bwork\s+from\s+(?:home|anywhere)\b',
#             r'\bwfh\b',
#             r'\btelecommute\b',
#             r'\btelecommuting\b',
#             r'\bdistributed\s+team\b',
#             r'\blocation[\-\s]independent\b',
#             r'\banywhere\s+in\s+(?:the\s+)?(?:uk|world)\b',
#             r'(?<!\bnot\s)(?<!\bno\s)(?<!\bsome\s)\bremote\b(?!\s+option)(?!\s+working\s+available)',
#             r'\bhome[\-\s]based\b',
#         ],
#         RemoteOption.HYBRID: [
#             r'\bhybrid\b',
#             r'\bflexible\s+working\b',
#             r'\b\d+\s*days?\s*(?:in\s+)?(?:the\s+)?office\b',
#             r'\b\d+\s*days?\s*(?:from\s+)?home\b',
#             r'\bmixed\s+(?:working|location)\b',
#             r'\bsplit\s+(?:between|time)\b',
#             r'\bpartially?\s+remote\b',
#             r'\bremote\s+(?:option|working)\s+available\b',
#             r'\boccasional(?:ly)?\s+(?:remote|office|wfh)\b',
#             r'\bsmart[\-\s]working\b',
#             r'\bagile\s+working\b',
#         ],
#         RemoteOption.ON_SITE: [
#             r'\bon[\-\s]site\b',
#             r'\bonsite\b',
#             r'\bin[\-\s]office\b',
#             r'\boffice[\-\s]based\b',
#             r'\boffice[\-\s]only\b',
#             r'\bmust\s+be\s+(?:based\s+)?(?:in|at)\b',
#             r'\bno\s+remote\b',
#             r'\bnot\s+remote\b',
#             r'\b(?:site|location)[\-\s]based\b',
#             r'\bpresence\s+required\b',
#             r'\bface[\-\s]to[\-\s]face\b',
#             r'\b(?:5|five)\s+days?\s+(?:in\s+)?(?:the\s+)?office\b',
#         ],
#     }
    
#     # =========================================================================
#     # Working Hours Patterns
#     # =========================================================================
    
#     HOURS_PATTERNS = [
#         # Specific hours per week
#         r'(\d{1,2}(?:\.\d+)?)\s*(?:to|[-–])\s*(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per|\/|a|pw|p\.w\.)\s*(?:week|wk)?',
#         r'(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per|\/|a|pw|p\.w\.)\s*(?:week|wk)',
#         r'(\d{1,2}(?:\.\d+)?)\s*hpw',
#         r'(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:pw|p\.w\.)',
        
#         # FTE patterns
#         r'(\d+(?:\.\d+)?)\s*fte',
#         r'fte[:\s]*(\d+(?:\.\d+)?)',
        
#         # Days per week
#         r'(\d)\s*(?:to|[-–])\s*(\d)\s*days?\s*(?:per|\/|a)\s*week',
#         r'(\d)\s*days?\s*(?:per|\/|a)\s*week',
        
#         # Specific shift patterns
#         r'(\d{1,2}(?::\d{2})?)\s*(?:am|pm)?\s*(?:to|[-–])\s*(\d{1,2}(?::\d{2})?)\s*(?:am|pm)',
#         r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*(?:to|[-–])\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
        
#         # Rotating/shift patterns
#         r'(rotating\s+shifts?|shift\s+work|night\s+shifts?|day\s+shifts?|early\s+shifts?|late\s+shifts?)',
#         r'(\d+)\s*(?:on|days?)\s*[,/]\s*(\d+)\s*(?:off|days?\s+off)',
        
#         # Annualized hours
#         r'(\d{3,4})\s*(?:hours?|hrs?)\s*(?:per|\/|a)\s*(?:year|annum|pa)',
#     ]
    
#     HOURS_LABELS = frozenset({
#         'hours', 'working hours', 'hours per week', 'weekly hours',
#         'shift', 'shifts', 'schedule', 'working pattern', 'time',
#         'contracted hours', 'work pattern', 'working time', 'fte',
#     })
    
#     # =========================================================================
#     # Salary Patterns
#     # =========================================================================
    
#     CURRENCY_SYMBOLS = {
#         '£': 'GBP', '€': 'EUR', '$': 'USD', '¥': 'JPY',
#         'gbp': 'GBP', 'eur': 'EUR', 'usd': 'USD', 'jpy': 'JPY',
#         'pound': 'GBP', 'pounds': 'GBP', 'euro': 'EUR', 'euros': 'EUR',
#         'dollar': 'USD', 'dollars': 'USD',
#     }
    
#     SALARY_PATTERNS = [
#         # Range: £30,000 - £45,000
#         r'([£€$])\s*([\d,]+(?:\.\d{2})?)\s*(?:k)?\s*(?:to|[-–]|and)\s*([£€$])?\s*([\d,]+(?:\.\d{2})?)\s*(?:k)?\s*(?:per\s+)?(annum|year|annual|pa|p\.a\.|month|hour|day|week)?',
        
#         # Single value: £45,000 per annum
#         r'([£€$])\s*([\d,]+(?:\.\d{2})?)\s*(?:k)?\s*(?:per\s+)?(annum|year|annual|pa|p\.a\.|month|hour|day|week)?',
        
#         # Up to / From patterns
#         r'(?:up\s+to|from|starting\s+at|circa|c\.?|approx(?:imately)?)\s*([£€$])\s*([\d,]+(?:\.\d{2})?)\s*(?:k)?',
        
#         # Range with k: 30k - 45k
#         r'([£€$])?\s*([\d,]+)\s*k\s*(?:to|[-–])\s*([£€$])?\s*([\d,]+)\s*k',
        
#         # NHS/Public sector bands
#         r'(?:band|grade)\s*(\d+[a-z]?)',
#         r'(?:nhs\s+)?(?:agenda\s+for\s+change\s+)?band\s*(\d+)',
        
#         # Pro rata
#         r'([£€$])\s*([\d,]+(?:\.\d{2})?)\s*(?:pro[\-\s]?rata)',
        
#         # Negotiable patterns
#         r'(competitive|negotiable|attractive|excellent|market[\-\s]rate|doi|depending\s+on\s+experience)',
#     ]
    
#     SALARY_LABELS = frozenset({
#         'salary', 'pay', 'compensation', 'remuneration', 'wage',
#         'rate', 'package', 'earnings', 'reward', 'salary range',
#         'pay range', 'day rate', 'hourly rate', 'annual salary',
#     })
    
#     # =========================================================================
#     # Holiday/Leave Patterns
#     # =========================================================================
    
#     HOLIDAY_PATTERNS = [
#         # X days patterns
#         r'(\d{1,2})\s*days?\s*(?:annual\s+)?(?:leave|holiday|vacation)',
#         r'(?:annual\s+)?(?:leave|holiday|vacation)[:\s]*(\d{1,2})\s*days?',
        
#         # X days plus bank holidays
#         r'(\d{1,2})\s*days?\s*(?:\+|plus)\s*(?:bank|public)\s+holidays?',
#         r'(\d{1,2})\s*days?\s*(?:excluding|excl\.?|not\s+including)\s*(?:bank|public)\s+holidays?',
#         r'(\d{1,2})\s*days?\s*(?:including|incl\.?)\s*(?:bank|public)\s+holidays?',
        
#         # Weeks patterns
#         r'(\d{1,2})\s*weeks?\s*(?:annual\s+)?(?:leave|holiday|vacation)',
        
#         # Pro rata
#         r'(\d{1,2})\s*days?\s*(?:pro[\-\s]?rata)',
        
#         # Generous/competitive
#         r'(generous|competitive|excellent)\s+(?:annual\s+)?(?:leave|holiday)',
        
#         # NHS standard
#         r'(27|28|29|33)\s*days?\s*(?:\+|plus)?\s*(?:8\s+)?(?:bank|public)\s+holidays?',
#     ]
    
#     HOLIDAY_LABELS = frozenset({
#         'holiday', 'holidays', 'annual leave', 'leave', 'vacation',
#         'paid leave', 'pto', 'time off', 'holiday entitlement',
#         'leave entitlement', 'annual holiday', 'holiday allowance',
#     })
    
#     # =========================================================================
#     # Benefits Patterns
#     # =========================================================================
    
#     COMMON_BENEFITS = [
#         # Pension
#         r'(?:company|employer|workplace)?\s*pension\s*(?:scheme|contribution)?',
#         r'\d+%\s*(?:employer)?\s*pension\s*(?:contribution)?',
#         r'auto[\-\s]?enrolment\s+pension',
        
#         # Healthcare
#         r'(?:private\s+)?(?:medical|health)\s*(?:insurance|cover|care)',
#         r'(?:private\s+)?dental\s*(?:insurance|cover|care)',
#         r'(?:eye|optical|vision)\s*(?:care|cover|insurance)',
#         r'(?:bupa|axa|vitality|aviva)\s*(?:health|medical)?',
#         r'health\s*(?:cash\s+)?plan',
#         r'employee\s+assistance\s+program(?:me)?',
#         r'eap\b',
#         r'mental\s+health\s+support',
#         r'wellbeing\s+(?:support|program(?:me)?|benefit)',
        
#         # Insurance
#         r'life\s*(?:insurance|assurance|cover)',
#         r'(?:death\s+in\s+service|dis)\s*(?:benefit)?',
#         r'income\s+protection',
#         r'critical\s+illness\s*(?:cover|insurance)',
        
#         # Financial
#         r'(?:annual|performance|discretionary)?\s*bonus',
#         r'commission\s*(?:scheme)?',
#         r'profit[\-\s]?sharing',
#         r'share\s*(?:options?|scheme|save)',
#         r'(?:company|employee)\s+shares?',
#         r'stock\s*(?:options?|purchase|plan)',
#         r'equity\b',
#         r'(?:season\s+)?ticket\s+loan',
#         r'cycle\s+to\s+work\s*(?:scheme)?',
#         r'childcare\s*(?:vouchers?|support)',
#         r'(?:salary\s+)?sacrifice',
        
#         # Leave
#         r'(?:enhanced\s+)?maternity\s*(?:pay|leave)?',
#         r'(?:enhanced\s+)?paternity\s*(?:pay|leave)?',
#         r'(?:enhanced\s+)?parental\s+leave',
#         r'(?:enhanced\s+)?sick\s+pay',
#         r'(?:paid\s+)?sabbatical',
#         r'birthday\s+(?:off|leave|day)',
#         r'duvet\s+days?',
#         r'volunteering\s+days?',
#         r'study\s+leave',
        
#         # Work arrangements
#         r'flexible\s+working',
#         r'remote\s+working',
#         r'hybrid\s+working',
#         r'(?:compressed|flexible)\s+hours',
#         r'work[\-\s]?life\s+balance',
#         r'(?:free|subsidised)\s+parking',
        
#         # Perks
#         r'(?:free|subsidised)\s+(?:gym|fitness)',
#         r'gym\s+membership',
#         r'(?:free|subsidised)\s+(?:lunch|food|meals?|canteen)',
#         r'staff\s+discount',
#         r'employee\s+discount',
#         r'(?:corporate|company)\s+discount',
#         r'retail\s+discount',
#         r'(?:free|subsidised)\s+(?:travel|transport)',
#         r'relocation\s*(?:package|support|assistance)?',
#         r'training\s*(?:budget|allowance)',
#         r'(?:learning|development)\s+(?:budget|opportunities)',
#         r'professional\s+development',
#         r'career\s+(?:development|progression)',
#         r'(?:paid\s+)?(?:professional\s+)?(?:membership|subscriptions?)',
#         r'social\s+(?:events?|activities)',
#         r'team\s+(?:events?|building|socials?)',
#         r'(?:fruit|snacks?|drinks?|coffee)\s+(?:in\s+)?(?:the\s+)?office',
#         r'dog[\-\s]?friendly\s+office',
#     ]
    
#     BENEFITS_LABELS = frozenset({
#         'benefits', 'perks', 'package', 'what we offer', 'we offer',
#         'in return', 'reward', 'rewards', 'employee benefits',
#         'staff benefits', 'what\'s in it for you', 'why join us',
#         'our offer', 'benefits package', 'total reward',
#     })
    
#     # =========================================================================
#     # Date Patterns
#     # =========================================================================
    
#     MONTHS = {
#         'january': '01', 'jan': '01',
#         'february': '02', 'feb': '02',
#         'march': '03', 'mar': '03',
#         'april': '04', 'apr': '04',
#         'may': '05',
#         'june': '06', 'jun': '06',
#         'july': '07', 'jul': '07',
#         'august': '08', 'aug': '08',
#         'september': '09', 'sep': '09', 'sept': '09',
#         'october': '10', 'oct': '10',
#         'november': '11', 'nov': '11',
#         'december': '12', 'dec': '12',
#     }
    
#     DATE_PATTERNS = [
#         # Full dates: 25th December 2025, December 25, 2025
#         r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})',
#         r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})',
        
#         # Short month names
#         r'(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{4})',
#         r'(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})',
        
#         # UK date formats: 25/12/2025, 25-12-2025
#         r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})',
#         r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})(?!\d)',
        
#         # ISO format: 2025-12-25
#         r'(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})',
        
#         # Time-based patterns
#         r'(\d{1,2}:\d{2})\s*(?:am|pm)?\s*(?:on\s+)?(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})',
        
#         # Relative dates
#         r'(asap|immediately|as\s+soon\s+as\s+possible)',
#         r'(within\s+\d+\s+(?:days?|weeks?|months?))',
#         r'(end\s+of\s+(?:january|february|march|april|may|june|july|august|september|october|november|december))',
#         r'((?:early|mid|late)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december))',
#         r'((?:early|mid|late)\s+(?:\d{4}|next\s+year))',
#         r'((?:q[1-4]|quarter\s+[1-4])\s+\d{4})',
#         r'(next\s+(?:week|month|year))',
#         r'(tbc|tbd|to\s+be\s+(?:confirmed|determined|announced))',
#     ]
    
#     CLOSING_DATE_LABELS = frozenset({
#         'closing date', 'deadline', 'apply by', 'application deadline',
#         'applications close', 'closing', 'close date', 'expires',
#         'expiry date', 'last date', 'apply before', 'end date',
#         'applications must be received by', 'submit by', 'final date',
#     })
    
#     START_DATE_LABELS = frozenset({
#         'start date', 'starting date', 'start', 'commencement',
#         'begin', 'beginning', 'join date', 'joining date',
#         'availability', 'available from', 'required from',
#         'start from', 'commencing', 'to start', 'anticipated start',
#     })
    
#     INTERVIEW_DATE_LABELS = frozenset({
#         'interview date', 'interviews', 'interview', 'assessment',
#         'assessment date', 'assessment centre', 'selection date',
#         'interview day', 'interviews will be held', 'assessment day',
#     })
    
#     # =========================================================================
#     # Application Method Patterns
#     # =========================================================================
    
#     APPLICATION_PATTERNS = {
#         ApplicationMethod.CV: [
#             r'\bsend\s+(?:your\s+)?cv\b',
#             r'\bsubmit\s+(?:your\s+)?cv\b',
#             r'\bapply\s+(?:with|via)\s+(?:your\s+)?cv\b',
#             r'\bcv\s+(?:to|required|needed)\b',
#             r'\bresume\s+(?:to|required|needed)\b',
#             r'\bemail\s+(?:your\s+)?cv\b',
#             r'\bcv\s+and\s+cover\s+letter\b',
#             r'\bcv\s+(?:with|and)\s+covering\s+letter\b',
#         ],
#         ApplicationMethod.APPLICATION_FORM: [
#             r'\bapplication\s+form\b',
#             r'\bcomplete\s+(?:the|our|an)\s+application\b',
#             r'\bfill\s+(?:in|out)\s+(?:the|our|an)\s+application\b',
#             r'\bonline\s+application\b',
#             r'\bapply\s+(?:online|through\s+our\s+(?:website|portal))\b',
#             r'\bsubmit\s+(?:an|your)\s+application\b',
#         ],
#         ApplicationMethod.EMAIL: [
#             r'\bapply\s+(?:by|via)\s+email\b',
#             r'\bemail\s+(?:your\s+)?application\b',
#             r'\bsend\s+(?:your\s+)?application\s+to\b',
#             r'\bemail\s+us\s+(?:at|to)\b',
#         ],
#         ApplicationMethod.ONLINE_PORTAL: [
#             r'\bapply\s+(?:via|through|on)\s+(?:our\s+)?(?:website|portal|careers?\s+(?:page|site))\b',
#             r'\bclick\s+(?:the\s+)?apply\s+(?:button|now|here)\b',
#             r'\bapply\s+(?:online|now)\b',
#         ],
#         ApplicationMethod.EXTERNAL_LINK: [
#             r'\bapply\s+(?:on|via|at)\s+(?:indeed|linkedin|glassdoor|reed|totaljobs|monster|cv[\-\s]?library)\b',
#             r'\bexternal\s+application\b',
#             r'\bredirected\s+to\b',
#         ],
#     }
    
#     APPLICATION_LABELS = frozenset({
#         'how to apply', 'application method', 'to apply', 'apply',
#         'application process', 'applying', 'application', 'applications',
#     })
    
#     # =========================================================================
#     # Contact Information Patterns
#     # =========================================================================
    
#     # Email pattern - comprehensive
#     EMAIL_PATTERN = re.compile(
#         r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
#     )
    
#     # Phone patterns - UK focused
#     PHONE_PATTERNS = [
#         # UK landlines
#         r'(\+44\s*\(?\d\)?\s*\d{3,4}\s*\d{3,4}\s*\d{3,4})',
#         r'(0\d{2,4}\s*\d{3,4}\s*\d{3,4})',
#         r'(\(0\d{2,4}\)\s*\d{3,4}\s*\d{3,4})',
        
#         # UK mobiles
#         r'(07\d{3}\s*\d{3}\s*\d{3})',
#         r'(\+44\s*7\d{3}\s*\d{3}\s*\d{3})',
#     ]
    
#     # Name patterns - looking for contact names
#     NAME_PATTERN = re.compile(
#         r'(?:contact|enquiries?|questions?|info(?:rmation)?|details?)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',
#         re.IGNORECASE
#     )
    
#     # Job title patterns for contacts
#     CONTACT_TITLE_PATTERNS = [
#         r'(?:hr|human\s+resources)\s*(?:manager|director|advisor|officer|coordinator|business\s+partner)',
#         r'(?:talent|recruitment)\s*(?:manager|advisor|coordinator|specialist|partner|lead)',
#         r'(?:recruiting|resourcing)\s*(?:manager|lead|coordinator)',
#         r'(?:people|hr)\s*(?:partner|advisor|coordinator)',
#         r'(?:hiring|recruitment)\s*(?:manager|coordinator|lead)',
#         r'head\s+of\s+(?:hr|people|talent|recruitment)',
#     ]
    
#     CONTACT_LABELS = frozenset({
#         'contact', 'enquiries', 'questions', 'for more information',
#         'informal enquiries', 'informal discussion', 'to discuss',
#         'contact us', 'get in touch', 'reach out', 'speak to',
#         'key contact', 'contact person', 'recruiting contact',
#     })
    
#     # =========================================================================
#     # Other Labels
#     # =========================================================================
    
#     LOCATION_LABELS = frozenset({
#         'location', 'locations', 'office location', 'office', 'based',
#         'where', 'workplace', 'work location', 'site', 'region',
#         'city', 'area', 'place of work', 'base', 'based in', 'located',
#     })
    
#     CONTRACT_LABELS = frozenset({
#         'contract', 'contract type', 'employment type', 'type of contract',
#         'position type', 'engagement type', 'tenure', 'employment',
#     })
    
#     JOB_TYPE_LABELS = frozenset({
#         'job type', 'working pattern', 'hours', 'employment',
#         'work type', 'schedule', 'working arrangement', 'pattern',
#     })
    
#     REMOTE_LABELS = frozenset({
#         'remote', 'location type', 'work arrangement', 'workplace type',
#         'working arrangement', 'flexible working', 'work style',
#         'working location', 'work location type',
#     })

#     # =========================================================================
#     # Initialization
#     # =========================================================================

#     def __init__(
#         self, 
#         raw_structure: dict[str, Any], 
#         existing_data: Optional[dict[str, Any]] = None,
#         job_id_counter: Optional[int] = None,
#     ):
#         self._raw = raw_structure
#         self._existing = existing_data or {}
#         self._job_id_counter = job_id_counter
#         self._all_text: list[str] = []
#         self._label_value_pairs: dict[str, str] = {}
#         self._full_description: str = ""
        
#     def extract(self) -> JobFields:
#         """Extract all job-specific fields from the DOM structure."""
#         # First pass: collect all text and label-value pairs
#         self._collect_text_and_pairs(self._raw)
#         self._full_description = ' '.join(self._all_text)
        
#         # Extract each field
#         fields = JobFields()
        
#         # IDs
#         fields.hireful_job_id = self._generate_hireful_id()
#         fields.employer_job_id = self._extract_employer_job_id()
#         fields.date_created = datetime.utcnow().isoformat() + 'Z'
        
#         # Core Job Info
#         fields.job_title = self._extract_job_title()
#         fields.job_description = self._extract_job_description()
#         fields.word_count = self._count_words(fields.job_description or "")
        
#         # Employment Details
#         fields.contract_type = self._extract_contract_type()
#         fields.job_type = self._extract_job_type()
#         fields.working_hours = self._extract_working_hours()
#         fields.remote_option = self._extract_remote_option()
        
#         # Location
#         fields.location = self._extract_location()
#         fields.location_postcode = self._extract_postcode()
        
#         # Compensation & Benefits
#         fields.salary = self._extract_salary()
#         fields.holiday = self._extract_holiday()
#         fields.benefits = self._extract_benefits()
        
#         # Important Dates
#         fields.closing_date = self._extract_date(self.CLOSING_DATE_LABELS, 'closing')
#         fields.interview_date = self._extract_date(self.INTERVIEW_DATE_LABELS, 'interview')
#         fields.start_date = self._extract_date(self.START_DATE_LABELS, 'start')
        
#         # Application Info
#         fields.application_method = self._extract_application_method()
        
#         # Contact Info
#         fields.contact = self._extract_contact_info()
        
#         return fields
    
#     def _collect_text_and_pairs(self, node: dict[str, Any]) -> None:
#         """Collect all text content and label-value pairs from DOM."""
#         if not node or not isinstance(node, dict):
#             return
        
#         tag = node.get('tag', '')
#         text = node.get('text', '').strip()
#         inner_text = node.get('innerText', '').strip()
#         children = node.get('children', [])
        
#         # Collect text
#         if inner_text:
#             self._all_text.append(inner_text)
#         elif text:
#             self._all_text.append(text)
        
#         # Look for label: value patterns
#         if text and ':' in text:
#             parts = text.split(':', 1)
#             if len(parts) == 2 and parts[0].strip() and parts[1].strip():
#                 label = parts[0].strip().lower()
#                 value = parts[1].strip()
#                 self._label_value_pairs[label] = value
        
#         # Check for label-value in consecutive children
#         for i, child in enumerate(children):
#             child_text = child.get('text', '').strip()
#             if child_text.endswith(':'):
#                 label = child_text.rstrip(':').strip().lower()
#                 if i + 1 < len(children):
#                     next_child = children[i + 1]
#                     value = next_child.get('innerText', '') or next_child.get('text', '')
#                     if value:
#                         self._label_value_pairs[label] = value.strip()
        
#         # Recurse
#         for child in children:
#             self._collect_text_and_pairs(child)
    
#     def _get_all_text(self) -> str:
#         """Get all collected text as single string."""
#         return self._full_description
    
#     # =========================================================================
#     # ID Extraction
#     # =========================================================================
    
#     def _generate_hireful_id(self) -> str:
#         """Generate sequential hireful job ID."""
#         return f"HF-{uuid.uuid4().hex[:8].upper()}"
    
#     def _extract_employer_job_id(self) -> Optional[str]:
#         """Extract employer's job ID/reference."""
#         # Check existing data first
#         for key in ['Job Req. ID', 'job_id', 'Job ID', 'ref', 'reference', 'Requisition ID']:
#             if key in self._existing:
#                 return str(self._existing[key])
        
#         # Check label-value pairs
#         for label in self.JOB_ID_LABELS:
#             if label in self._label_value_pairs:
#                 value = self._label_value_pairs[label]
#                 # Validate it looks like an ID
#                 if self._validate_job_id(value):
#                     return value
        
#         # Search in all text using patterns
#         all_text = self._get_all_text()
        
#         for pattern in self.JOB_ID_PATTERNS:
#             match = re.search(pattern, all_text, re.IGNORECASE)
#             if match:
#                 job_id = match.group(1) if match.lastindex else match.group(0)
#                 if self._validate_job_id(job_id):
#                     return job_id
        
#         return None
    
#     def _validate_job_id(self, value: str) -> bool:
#         """Validate that a string looks like a job ID."""
#         if not value:
#             return False
        
#         value = value.strip()
        
#         # Too short or too long
#         if len(value) < 3 or len(value) > 50:
#             return False
        
#         # Should contain at least one digit or be alphanumeric
#         if not re.search(r'\d', value) and not re.match(r'^[A-Z]{3,10}$', value, re.IGNORECASE):
#             return False
        
#         # Exclude common false positives
#         false_positives = {
#             'n/a', 'na', 'none', 'tbc', 'tbd', 'see below', 'various',
#             'multiple', 'asap', 'competitive', 'negotiable',
#         }
#         if value.lower() in false_positives:
#             return False
        
#         return True
    
#     # =========================================================================
#     # Job Title and Description
#     # =========================================================================
    
#     def _extract_job_title(self) -> Optional[str]:
#         """Extract job title from h1 or existing data."""
#         # Check existing data first
#         for key in ['Job Title', 'job_title', 'title', 'position', 'role', 'Position']:
#             if key in self._existing:
#                 title = str(self._existing[key])
#                 return self._clean_job_title(title)
        
#         # Find h1
#         title = self._find_h1_text(self._raw)
#         if title:
#             return self._clean_job_title(title)
        
#         return None
    
#     def _clean_job_title(self, title: str) -> str:
#         """Clean and validate job title."""
#         # Remove common prefixes
#         prefixes_to_remove = [
#             r'^job\s*(?:title)?[:\s]*',
#             r'^position[:\s]*',
#             r'^role[:\s]*',
#             r'^vacancy[:\s]*',
#         ]
        
#         for prefix in prefixes_to_remove:
#             title = re.sub(prefix, '', title, flags=re.IGNORECASE)
        
#         # Remove trailing location in parentheses if it's a UK location
#         title = re.sub(r'\s*\([^)]*(?:uk|london|remote|hybrid)[^)]*\)\s*$', '', title, flags=re.IGNORECASE)
        
#         return title.strip()
    
#     def _find_h1_text(self, node: dict[str, Any]) -> Optional[str]:
#         """Find first h1 text in DOM."""
#         if not node or not isinstance(node, dict):
#             return None
        
#         if node.get('tag') == 'h1':
#             return node.get('innerText', '') or node.get('text', '') or self._get_child_text(node)
        
#         for child in node.get('children', []):
#             result = self._find_h1_text(child)
#             if result:
#                 return result
        
#         return None
    
#     def _get_child_text(self, node: dict[str, Any]) -> str:
#         """Recursively get all text from children."""
#         if not node:
#             return ''
        
#         parts = []
#         if text := node.get('text', '').strip():
#             parts.append(text)
        
#         for child in node.get('children', []):
#             if child_text := self._get_child_text(child):
#                 parts.append(child_text)
        
#         return ' '.join(parts)
    
#     def _extract_job_description(self) -> Optional[str]:
#         """Extract full job description text."""
#         # Check existing data
#         for key in ['Job description', 'job_description', 'description', 'Description']:
#             if key in self._existing:
#                 val = self._existing[key]
#                 if isinstance(val, list):
#                     return ' '.join(str(v) for v in val)
#                 return str(val)
        
#         # Use collected text, filtering out navigation/footer content
#         if self._all_text:
#             # Filter out very short entries and common noise
#             noise_patterns = [
#                 r'^(?:home|about|contact|menu|search|login|sign\s*in|register)$',
#                 r'^(?:apply|apply\s+now|submit|back|next|previous)$',
#                 r'^(?:share|print|save|email)$',
#                 r'^\d+$',  # Just numbers
#                 r'^[•\-\*]$',  # Just bullets
#             ]
            
#             filtered = []
#             for text in self._all_text:
#                 text = text.strip()
#                 if len(text) < 3:
#                     continue
#                 if any(re.match(p, text, re.IGNORECASE) for p in noise_patterns):
#                     continue
#                 filtered.append(text)
            
#             if filtered:
#                 return ' '.join(filtered)
        
#         return None
    
#     def _count_words(self, text: str) -> int:
#         """Count words in text accurately."""
#         if not text:
#             return 0
        
#         # Remove HTML entities
#         text = re.sub(r'&[a-z]+;', ' ', text)
        
#         # Remove URLs
#         text = re.sub(r'https?://\S+', '', text)
        
#         # Remove email addresses
#         text = re.sub(r'\S+@\S+\.\S+', '', text)
        
#         # Replace punctuation with spaces (except apostrophes within words)
#         text = re.sub(r"[^\w\s']", ' ', text)
#         text = re.sub(r"(?<!\w)'|'(?!\w)", ' ', text)
        
#         # Split and count non-empty words
#         words = [w for w in text.split() if w and not w.isspace()]
        
#         return len(words)
    
#     # =========================================================================
#     # Location Extraction
#     # =========================================================================
    
#     def _extract_location(self) -> Optional[LocationInfo]:
#         """Extract location with UK-based detection."""
#         raw_location = None
        
#         # Check existing data
#         for key in ['Location', 'location', 'office', 'city', 'region']:
#             if key in self._existing:
#                 val = self._existing[key]
#                 if isinstance(val, dict):
#                     raw_location = val.get('raw', str(val))
#                 else:
#                     raw_location = val if isinstanece(val, str) else str(val)
#                 break
        
#         # Check label-value pairs
#         if not raw_location:
#             for label in self.LOCATION_LABELS:
#                 if label in self._label_value_pairs:
#                     raw_location = self._label_value_pairs[label]
#                     break
        
#         # Search in all text
#         if not raw_location:
#             raw_location = self._find_location_in_text()
        
#         if not raw_location:
#             return None
        
#         return self._analyze_location(raw_location)
    
#     def _find_location_in_text(self) -> Optional[str]:
#         """Find location mentions in text."""
#         all_text = self._get_all_text().lower()
        
#         # First try to find UK postcode - most reliable
#         match = self.UK_FULL_POSTCODE_PATTERN.search(self._get_all_text())
#         if match:
#             # Get surrounding context
#             start = max(0, match.start() - 50)
#             end = min(len(self._get_all_text()), match.end() + 20)
#             return self._get_all_text()[start:end].strip()
        
#         # Look for UK cities
#         for city in self.UK_CITIES:
#             pattern = rf'\b{re.escape(city)}\b'
#             if re.search(pattern, all_text):
#                 return city.title()
        
#         return None
    
#     def _analyze_location(self, raw_location: str) -> LocationInfo:
#         """Analyze location string to determine if UK-based."""
#         location_lower = raw_location.lower()
        
#         is_uk = False
#         is_remote = False
#         postcode = None
#         city = None
#         region = None
#         country = None
        
#         # Extract postcode
#         postcode_match = self.UK_FULL_POSTCODE_PATTERN.search(raw_location)
#         if postcode_match:
#             postcode = postcode_match.group(1).upper()
#             # Normalize format: ensure single space
#             postcode = re.sub(r'\s+', ' ', postcode)
#             if ' ' not in postcode and len(postcode) > 4:
#                 # Insert space before last 3 characters
#                 postcode = postcode[:-3] + ' ' + postcode[-3:]
#             is_uk = True
        
#         # Check for remote
#         if re.search(r'\bremote\b|\bwork\s+from\s+home\b|\bwfh\b|\bhome[\-\s]based\b', location_lower):
#             is_remote = True
        
#         # Check UK country markers
#         for marker in self.UK_COUNTRY_MARKERS:
#             if re.search(rf'\b{re.escape(marker)}\b', location_lower):
#                 is_uk = True
#                 if marker in ('england', 'scotland', 'wales', 'northern ireland'):
#                     country = marker.title()
#                 else:
#                     country = 'United Kingdom'
#                 break
        
#         # Check UK cities
#         for uk_city in self.UK_CITIES:
#             if re.search(rf'\b{re.escape(uk_city)}\b', location_lower):
#                 is_uk = True
#                 city = uk_city.title()
#                 break
        
#         # Check UK regions
#         for uk_region in self.UK_REGIONS:
#             if re.search(rf'\b{re.escape(uk_region)}\b', location_lower):
#                 is_uk = True
#                 region = uk_region.title()
#                 break
        
#         # If remote and mentions UK anywhere
#         if is_remote and any(
#             re.search(rf'\b{re.escape(m)}\b', location_lower)
#             for m in self.UK_COUNTRY_MARKERS
#         ):
#             is_uk = True
        
#         return LocationInfo(
#             raw=raw_location,
#             is_uk_based=is_uk,
#             postcode=postcode,
#             city=city,
#             region=region,
#             country=country,
#             is_remote=is_remote,
#         )
    
#     def _extract_postcode(self) -> Optional[str]:
#         """Extract UK postcode separately."""
#         # Check if we already have it from location
#         if hasattr(self, '_location') and self._location and self._location.postcode:
#             return self._location.postcode
        
#         # Check existing data
#         for key in ['postcode', 'Postcode', 'post_code', 'zip', 'postal_code']:
#             if key in self._existing:
#                 val = str(self._existing[key]).strip().upper()
#                 if self.UK_FULL_POSTCODE_PATTERN.match(val):
#                     return val
        
#         # Search in all text
#         all_text = self._get_all_text()
#         match = self.UK_FULL_POSTCODE_PATTERN.search(all_text)
#         if match:
#             postcode = match.group(1).upper()
#             # Normalize format
#             postcode = re.sub(r'\s+', ' ', postcode)
#             if ' ' not in postcode and len(postcode) > 4:
#                 postcode = postcode[:-3] + ' ' + postcode[-3:]
#             return postcode
        
#         return None
    
#     # =========================================================================
#     # Contract and Job Type Extraction
#     # =========================================================================
    
#     def _extract_contract_type(self) -> Optional[str]:
#         """Extract contract type."""
#         # Check existing data
#         for key in ['contract_type', 'Contract Type', 'contract', 'employment type', 'Employment Type']:
#             if key in self._existing:
#                 return self._normalize_contract_type(str(self._existing[key]))
        
#         # Check label-value pairs
#         for label in self.CONTRACT_LABELS:
#             if label in self._label_value_pairs:
#                 return self._normalize_contract_type(self._label_value_pairs[label])
        
#         # Search in all text
#         all_text = self._get_all_text().lower()
        
#         # Check patterns in priority order
#         for contract_type, patterns in self.CONTRACT_PATTERNS.items():
#             for pattern in patterns:
#                 if re.search(pattern, all_text, re.IGNORECASE):
#                     return contract_type.value
        
#         return None
    
#     def _normalize_contract_type(self, value: str) -> Optional[str]:
#         """Normalize contract type string."""
#         value_lower = value.lower()
        
#         for contract_type, patterns in self.CONTRACT_PATTERNS.items():
#             for pattern in patterns:
#                 if re.search(pattern, value_lower):
#                     return contract_type.value
        
#         # Return cleaned original if reasonable
#         if len(value) < 30:
#             return value.lower().strip()
#         return None
    
#     def _extract_job_type(self) -> Optional[str]:
#         """Extract job type (full-time/part-time)."""
#         # Check existing data
#         for key in ['job_type', 'Job Type', 'type', 'employment', 'Work Type']:
#             if key in self._existing:
#                 normalized = self._normalize_job_type(str(self._existing[key]))
#                 if normalized:
#                     return normalized
        
#         # Check label-value pairs
#         for label in self.JOB_TYPE_LABELS:
#             if label in self._label_value_pairs:
#                 normalized = self._normalize_job_type(self._label_value_pairs[label])
#                 if normalized:
#                     return normalized
        
#         # Search in all text
#         all_text = self._get_all_text().lower()
        
#         for job_type, patterns in self.JOB_TYPE_PATTERNS.items():
#             for pattern in patterns:
#                 if re.search(pattern, all_text, re.IGNORECASE):
#                     return job_type.value
        
#         return None
    
#     def _normalize_job_type(self, value: str) -> Optional[str]:
#         """Normalize job type string."""
#         value_lower = value.lower()
        
#         for job_type, patterns in self.JOB_TYPE_PATTERNS.items():
#             for pattern in patterns:
#                 if re.search(pattern, value_lower):
#                     return job_type.value
        
#         return None
    
#     # =========================================================================
#     # Working Hours Extraction
#     # =========================================================================
    
#     def _extract_working_hours(self) -> Optional[str]:
#         """Extract working hours information."""
#         # Check existing data
#         for key in ['working_hours', 'hours', 'Hours', 'schedule', 'Working Hours']:
#             if key in self._existing:
#                 return str(self._existing[key])
        
#         # Check label-value pairs
#         for label in self.HOURS_LABELS:
#             if label in self._label_value_pairs:
#                 return self._label_value_pairs[label]
        
#         # Search in all text
#         all_text = self._get_all_text()
        
#         for pattern in self.HOURS_PATTERNS:
#             match = re.search(pattern, all_text, re.IGNORECASE)
#             if match:
#                 return match.group(0).strip()
        
#         return None
    
#     # =========================================================================
#     # Remote Option Extraction
#     # =========================================================================
    
#     def _extract_remote_option(self) -> Optional[str]:
#         """Extract remote working option."""
#         # Check existing data
#         for key in ['remote_option', 'remote', 'Work Type', 'workplace type', 'Remote']:
#             if key in self._existing:
#                 normalized = self._normalize_remote_option(str(self._existing[key]))
#                 if normalized:
#                     return normalized
        
#         # Check label-value pairs
#         for label in self.REMOTE_LABELS:
#             if label in self._label_value_pairs:
#                 normalized = self._normalize_remote_option(self._label_value_pairs[label])
#                 if normalized:
#                     return normalized
        
#         # Search in all text - check most specific first
#         all_text = self._get_all_text().lower()
        
#         # Check on-site first (to avoid "remote" false positives in "no remote")
#         for pattern in self.REMOTE_PATTERNS[RemoteOption.ON_SITE]:
#             if re.search(pattern, all_text, re.IGNORECASE):
#                 return RemoteOption.ON_SITE.value
        
#         # Check hybrid
#         for pattern in self.REMOTE_PATTERNS[RemoteOption.HYBRID]:
#             if re.search(pattern, all_text, re.IGNORECASE):
#                 return RemoteOption.HYBRID.value
        
#         # Check remote
#         for pattern in self.REMOTE_PATTERNS[RemoteOption.REMOTE]:
#             if re.search(pattern, all_text, re.IGNORECASE):
#                 return RemoteOption.REMOTE.value
        
#         return None
    
#     def _normalize_remote_option(self, value: str) -> Optional[str]:
#         """Normalize remote option string."""
#         value_lower = value.lower()
        
#         # Check patterns in priority order
#         for remote_type in [RemoteOption.ON_SITE, RemoteOption.HYBRID, RemoteOption.REMOTE]:
#             for pattern in self.REMOTE_PATTERNS[remote_type]:
#                 if re.search(pattern, value_lower):
#                     return remote_type.value
        
#         return None
    
#     # =========================================================================
#     # Salary Extraction
#     # =========================================================================
    
#     def _extract_salary(self) -> Optional[SalaryInfo]:
#         """Extract salary information."""
#         raw_salary = None
        
#         # Check existing data
#         for key in ['salary', 'Salary', 'pay', 'compensation', 'package']:
#             if key in self._existing:
#                 val = self._existing[key]
#                 if isinstance(val, dict):
#                     raw_salary = val.get('raw', str(val))
#                 else:
#                     raw_salary = val if isinstance(val, str) else str(val)
#                 break
        
#         # Check label-value pairs
#         if not raw_salary:
#             for label in self.SALARY_LABELS:
#                 if label in self._label_value_pairs:
#                     raw_salary = self._label_value_pairs[label]
#                     break
        
#         # Search in all text
#         if not raw_salary:
#             raw_salary = self._find_salary_in_text()
        
#         if not raw_salary:
#             return None
        
#         return self._parse_salary(raw_salary)
    
#     def _find_salary_in_text(self) -> Optional[str]:
#         """Find salary mentions in text."""
#         all_text = self._get_all_text()
        
#         for pattern in self.SALARY_PATTERNS:
#             match = re.search(pattern, all_text, re.IGNORECASE)
#             if match:
#                 return match.group(0)
        
#         return None
    
#     def _parse_salary(self, raw: str) -> SalaryInfo:
#         """Parse salary string into structured info."""
#         salary = SalaryInfo(raw=raw)
        
#         # Check negotiable
#         if re.search(r'negotiable|competitive|doi|depending|attractive|market', raw, re.IGNORECASE):
#             salary.is_negotiable = True
        
#         # Detect currency
#         for symbol, currency in self.CURRENCY_SYMBOLS.items():
#             if symbol in raw.lower():
#                 salary.currency = currency
#                 break
        
#         # Detect period
#         if re.search(r'hour|hourly|hr\b|ph\b|p\.h\.', raw, re.IGNORECASE):
#             salary.period = 'hourly'
#         elif re.search(r'\bday|daily|pd\b|p\.d\.', raw, re.IGNORECASE):
#             salary.period = 'daily'
#         elif re.search(r'week|weekly|pw\b|p\.w\.', raw, re.IGNORECASE):
#             salary.period = 'weekly'
#         elif re.search(r'month|monthly|pm\b|p\.m\.', raw, re.IGNORECASE):
#             salary.period = 'monthly'
#         else:
#             salary.period = 'annual'
        
#         # Check for pro rata
#         if re.search(r'pro[\-\s]?rata', raw, re.IGNORECASE):
#             salary.includes_benefits = False
        
#         # Extract values
#         has_k = bool(re.search(r'\d+k', raw, re.IGNORECASE))
        
#         # Pattern: £30,000 - £45,000 or 30k - 45k
#         range_match = re.search(
#             r'[£€$]?\s*([\d,]+(?:\.\d{2})?)\s*k?\s*(?:to|[-–]|and)\s*[£€$]?\s*([\d,]+(?:\.\d{2})?)\s*k?',
#             raw, re.IGNORECASE
#         )
        
#         if range_match:
#             min_str = range_match.group(1).replace(',', '')
#             max_str = range_match.group(2).replace(',', '')
            
#             salary.min_value = self._parse_salary_value(min_str, has_k)
#             salary.max_value = self._parse_salary_value(max_str, has_k)
#         else:
#             # Single value
#             single_match = re.search(r'[£€$]\s*([\d,]+(?:\.\d{2})?)\s*k?', raw, re.IGNORECASE)
#             if single_match:
#                 value_str = single_match.group(1).replace(',', '')
#                 value = self._parse_salary_value(value_str, has_k)
                
#                 if re.search(r'up\s+to|max|maximum', raw, re.IGNORECASE):
#                     salary.max_value = value
#                 elif re.search(r'from|min|minimum|starting', raw, re.IGNORECASE):
#                     salary.min_value = value
#                 else:
#                     salary.min_value = value
#                     salary.max_value = value
        
#         return salary
    
#     def _parse_salary_value(self, value_str: str, has_k: bool) -> float:
#         """Parse salary value string to float."""
#         try:
#             value = float(value_str)
#             if has_k and value < 1000:
#                 value *= 1000
#             return value
#         except ValueError:
#             return 0.0
    
#     # =========================================================================
#     # Holiday Extraction
#     # =========================================================================
    
#     def _extract_holiday(self) -> Optional[str]:
#         """Extract holiday/annual leave information."""
#         # Check existing data
#         for key in ['holiday', 'Holiday', 'annual leave', 'Annual Leave', 'leave', 'vacation']:
#             if key in self._existing:
#                 return str(self._existing[key])
        
#         # Check label-value pairs
#         for label in self.HOLIDAY_LABELS:
#             if label in self._label_value_pairs:
#                 return self._label_value_pairs[label]
        
#         # Search in all text
#         all_text = self._get_all_text()
        
#         for pattern in self.HOLIDAY_PATTERNS:
#             match = re.search(pattern, all_text, re.IGNORECASE)
#             if match:
#                 return match.group(0).strip()
        
#         return None
    
#     # =========================================================================
#     # Benefits Extraction
#     # =========================================================================
    
#     def _extract_benefits(self) -> Optional[list[str]]:
#         """Extract list of benefits."""
#         benefits = []
        
#         # Check existing data
#         for key in ['benefits', 'Benefits', 'perks', 'Perks', "What's in it for you?"]:
#             if key in self._existing:
#                 val = self._existing[key]
#                 if isinstance(val, list):
#                     benefits.extend(str(v) for v in val)
#                 elif isinstance(val, str):
#                     # Try to split by common delimiters
#                     for item in re.split(r'[•\-\*\n]', val):
#                         item = item.strip()
#                         if item and len(item) > 3:
#                             benefits.append(item)
#                 break
        
#         # Search for common benefit patterns
#         all_text = self._get_all_text()
        
#         for pattern in self.COMMON_BENEFITS:
#             matches = re.finditer(pattern, all_text, re.IGNORECASE)
#             for match in matches:
#                 benefit = match.group(0).strip()
#                 # Avoid duplicates (case-insensitive)
#                 if benefit and not any(b.lower() == benefit.lower() for b in benefits):
#                     benefits.append(benefit)
        
#         # Limit to avoid noise
#         if len(benefits) > 20:
#             benefits = benefits[:20]
        
#         return benefits if benefits else None
    
#     # =========================================================================
#     # Date Extraction
#     # =========================================================================
    
#     def _extract_date(self, labels: frozenset, date_type: str) -> Optional[str]:
#         """Extract specific date type."""
#         # Check existing data
#         key_variants = [
#             f'{date_type}_date', f'{date_type}Date', date_type,
#             f'{date_type.title()} Date', f'{date_type.title()}',
#             f'{date_type} date', f'Closing Date', f'Start Date', f'Interview Date',
#         ]
        
#         for key in key_variants:
#             if key in self._existing:
#                 return self._normalize_date(str(self._existing[key]))
        
#         # Check label-value pairs
#         for label in labels:
#             if label in self._label_value_pairs:
#                 return self._normalize_date(self._label_value_pairs[label])
        
#         # Search in context around labels
#         all_text = self._get_all_text()
        
#         for label in labels:
#             # Look for label followed by date
#             pattern = rf'{re.escape(label)}[:\s]*([^.;\n]+)'
#             match = re.search(pattern, all_text, re.IGNORECASE)
#             if match:
#                 date_str = match.group(1).strip()
#                 normalized = self._normalize_date(date_str)
#                 if normalized:
#                     return normalized
        
#         return None
    
#     def _normalize_date(self, date_str: str) -> Optional[str]:
#         """Normalize date string."""
#         if not date_str:
#             return None
        
#         date_str = date_str.strip()
        
#         # Check for relative/special dates
#         special_patterns = [
#             (r'asap|immediately', 'ASAP'),
#             (r'as\s+soon\s+as\s+possible', 'ASAP'),
#             (r'tbc|tbd|to\s+be\s+(?:confirmed|determined|announced)', 'TBC'),
#         ]
        
#         for pattern, replacement in special_patterns:
#             if re.search(pattern, date_str, re.IGNORECASE):
#                 return replacement
        
#         # Try to find a proper date
#         for pattern in self.DATE_PATTERNS:
#             match = re.search(pattern, date_str, re.IGNORECASE)
#             if match:
#                 return match.group(0).strip()
        
#         # Return cleaned original if reasonable length
#         cleaned = date_str[:50].strip()
#         if cleaned and len(cleaned) > 3:
#             return cleaned
        
#         return None
    
#     # =========================================================================
#     # Application Method Extraction
#     # =========================================================================
    
#     def _extract_application_method(self) -> Optional[str]:
#         """Extract how to apply (CV, application form, etc.)."""
#         # Check existing data
#         for key in ['application_method', 'how to apply', 'How to Apply', 'apply']:
#             if key in self._existing:
#                 return self._normalize_application_method(str(self._existing[key]))
        
#         # Check label-value pairs
#         for label in self.APPLICATION_LABELS:
#             if label in self._label_value_pairs:
#                 return self._normalize_application_method(self._label_value_pairs[label])
        
#         # Search in all text
#         all_text = self._get_all_text().lower()
        
#         # Check patterns
#         for method, patterns in self.APPLICATION_PATTERNS.items():
#             for pattern in patterns:
#                 if re.search(pattern, all_text, re.IGNORECASE):
#                     return method.value
        
#         # Check for common ATS indicators
#         ats_indicators = [
#             r'apply\s+now',
#             r'click\s+(?:here\s+)?to\s+apply',
#             r'submit\s+(?:your\s+)?application',
#         ]
        
#         for pattern in ats_indicators:
#             if re.search(pattern, all_text, re.IGNORECASE):
#                 return ApplicationMethod.ONLINE_PORTAL.value
        
#         return None
    
#     def _normalize_application_method(self, value: str) -> Optional[str]:
#         """Normalize application method string."""
#         value_lower = value.lower()
        
#         for method, patterns in self.APPLICATION_PATTERNS.items():
#             for pattern in patterns:
#                 if re.search(pattern, value_lower):
#                     return method.value
        
#         return None
    
#     # =========================================================================
#     # Contact Info Extraction
#     # =========================================================================
    
#     def _extract_contact_info(self) -> Optional[ContactInfo]:
#         """Extract key contact information."""
#         contact = ContactInfo()
        
#         all_text = self._get_all_text()
        
#         # Extract email
#         email_match = self.EMAIL_PATTERN.search(all_text)
#         if email_match:
#             email = email_match.group(1).lower()
#             # Validate it's not a generic/noreply email
#             invalid_emails = ['noreply', 'no-reply', 'donotreply', 'do-not-reply']
#             if not any(inv in email for inv in invalid_emails):
#                 contact.email = email
        
#         # Extract phone
#         for pattern in self.PHONE_PATTERNS:
#             phone_match = re.search(pattern, all_text)
#             if phone_match:
#                 contact.phone = phone_match.group(1).strip()
#                 break
        
#         # Extract name - look near contact labels
#         for label in self.CONTACT_LABELS:
#             pattern = rf'{re.escape(label)}[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})'
#             name_match = re.search(pattern, all_text, re.IGNORECASE)
#             if name_match:
#                 contact.name = name_match.group(1).strip()
#                 break
        
#         # Try to extract name from email format (firstname.lastname@)
#         if not contact.name and contact.email:
#             email_name_match = re.match(r'([a-z]+)\.([a-z]+)@', contact.email)
#             if email_name_match:
#                 first = email_name_match.group(1).title()
#                 last = email_name_match.group(2).title()
#                 contact.name = f"{first} {last}"
        
#         # Extract job title near contact name
#         if contact.name:
#             name_pattern = re.escape(contact.name)
#             for title_pattern in self.CONTACT_TITLE_PATTERNS:
#                 # Look for title before or after name
#                 pattern = rf'({title_pattern})[,\s]+{name_pattern}|{name_pattern}[,\s]+({title_pattern})'
#                 title_match = re.search(pattern, all_text, re.IGNORECASE)
#                 if title_match:
#                     contact.job_title = (title_match.group(1) or title_match.group(2)).strip().title()
#                     break
        
#         # Only return if we found something
#         if contact.name or contact.email or contact.phone:
#             return contact
        
#         return None


#     # async def extract_to_sections(
#     #     self,
#     #     include_raw: bool = False,
#     #     raw_content: dict = {}
#     # ) -> SectionedContent:
#     #     """
#     #     Extract DOM content and return as sectioned dictionary.
#     #     Each heading becomes a key, content under it becomes the value.
        
#     #     Ideal for job posting pages.
        
#     #     Returns:
#     #         SectionedContent with sections dict mapping heading -> content
#     #     """
#     #     logger.info("Starting sectioned content extraction")
        

#     #     sections, metadata = self._extract_sections(raw_content or {})

#     #     logger.info(
#     #         "Sectioned content extraction completed",
#     #         extra={"section_count": len(sections)},
#     #     )

#     #     return SectionedContent(
#     #         sections=sections,
#     #         metadata=metadata,
#     #         raw_structure=raw_content if include_raw else {},
#     #     )
    


#     # def _extract_sections(self, node: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
#     #     """
#     #     Extract page content sectioned by headings.
        
#     #     Returns:
#     #         Tuple of (sections dict, metadata dict)
#     #     """
#     #     # Flatten the DOM tree into a linear sequence of elements
#     #     elements = []
#     #     self._flatten_dom(node, elements)
        
#     #     sections: dict[str, str] = {}
#     #     metadata: dict[str, Any] = {}
        
#     #     current_heading: Optional[str] = None
#     #     current_content: list[str] = []
#     #     intro_content: list[str] = []  # Content before first heading
        
#     #     for element in elements:
#     #         tag = element.get("tag", "")
            
#     #         if tag in self.HEADING_TAGS:
#     #             # Save previous section
#     #             if current_heading:
#     #                 content = self._clean_content(" ".join(current_content))
#     #                 if content:
#     #                     sections[current_heading] = content
#     #             elif current_content:
#     #                 # Content before first heading goes to metadata
#     #                 intro_content = current_content
                
#     #             # Start new section
#     #             current_heading = self._get_element_text(element)
#     #             current_content = []
#     #         else:
#     #             # Collect content
#     #             text = self._get_element_text(element)
#     #             if text:
#     #                 current_content.append(text)
        
#     #     # Save last section
#     #     if current_heading:
#     #         content = self._clean_content(" ".join(current_content))
#     #         if content:
#     #             sections[current_heading] = content
        
#     #     # Process intro content
#     #     if intro_content:
#     #         metadata["intro"] = self._clean_content(" ".join(intro_content))
        
#     #     return sections, metadata

#     # def _flatten_dom(self, node: dict[str, Any], elements: list[dict[str, Any]]) -> None:
#     #     """
#     #     Flatten DOM tree into linear sequence, preserving order.
#     #     """
#     #     if not node or not isinstance(node, dict):
#     #         return
        
#     #     tag = node.get("tag", "")
        
#     #     # Skip empty structural tags, keep meaningful ones
#     #     if tag:
#     #         elements.append(node)
        
#     #     # Process children
#     #     for child in node.get("children", []):
#     #         self._flatten_dom(child, elements)

#     # def _get_element_text(self, node: dict[str, Any]) -> str:
#     #     """
#     #     Extract text content from an element.
#     #     """
#     #     tag = node.get("tag", "")
#     #     text = node.get("text", "").strip()
#     #     inner_text = node.get("innerText", "").strip()
        
#     #     # For headings, links, buttons - prefer innerText
#     #     if tag in self.HEADING_TAGS or tag in ("a", "button"):
#     #         return inner_text or text
        
#     #     # For list items, add bullet
#     #     if tag == "li":
#     #         content = inner_text or text
#     #         return f"• {content}" if content else ""
        
#     #     # For paragraphs and other text elements
#     #     if tag in ("p", "span", "div", "td", "th"):
#     #         return inner_text or text
        
#     #     return text

#     # def _clean_content(self, content: str) -> str:
#     #     """
#     #     Clean up extracted content.
#     #     """
#     #     import re
        
#     #     # Normalize whitespace
#     #     content = re.sub(r'\s+', ' ', content)
        
#     #     # Remove excessive bullet points
#     #     content = re.sub(r'(• )+', '• ', content)
        
#     #     return content.strip()
