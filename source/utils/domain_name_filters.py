import re
from typing import  Optional
from urllib.parse import urlparse
from playwright.async_api import  Page
from service.brower_scraper_service import DOMContentExtractor



# =============================================================================
# URL Filtering Utilities
# =============================================================================


class URLFilter:
    DEFAULT_JOB_KEYWORDS = frozenset({
        "job", "jobs", "career", "careers",
        "vacancy", "vacancies", "opportunity", "opportunities",
        "hiring", "recruit", "recruitment",
        "position", "positions", "opening", "openings",
        "join", "apply", "application", "talent",
        "team", "work", "working", "people", "peoples",
    })

    SKIP_EXTENSIONS = frozenset({
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".ppt", ".pptx", ".zip", ".rar", ".7z",
        ".png", ".jpg", ".jpeg", ".gif", ".svg",
    })

    COMMON_JOB_PATHS = [
        "/careers",
        "/jobs",
        "/careers/",
        "/jobs/",
        "/work-with-us",
        "/join-us",
        "/join-our-team",
        "/opportunities",
        "/vacancies",
        "/openings",
        "/hiring",
        "/employment",
        "/career",
        "/job",
        "/work",
        "/about/careers",
        "/about/jobs",
        "/company/careers",
        "/en/careers",
        "/en/jobs",
    ]

    @classmethod
    def filter_web_pages_only(cls, urls: list[str]) -> list[str]:
        filtered = []
        for url in urls:
            url_lower = url.lower().split("?")[0]
            if not any(url_lower.endswith(ext) for ext in cls.SKIP_EXTENSIONS):
                filtered.append(url)
        return filtered

    @staticmethod
    def filter_by_domain(urls: list[str], domain: str) -> list[str]:
        domain = domain.replace("www.", "").lower()
        filtered = []

        for url in urls:
            try:
                parsed = urlparse(url)
                url_domain = parsed.netloc.replace("www.", "").lower()

                if url_domain == domain or url_domain.endswith(f".{domain}"):
                    if url_domain == domain:
                        if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
                            filtered.append(url)
                    else:
                        filtered.append(url)
            except Exception:
                continue

        return filtered

    @classmethod
    def filter_job_urls(
        cls,
        urls: list[str],
        include_keywords: Optional[set[str]] = None,
    ) -> list[str]:
        keywords = include_keywords or cls.DEFAULT_JOB_KEYWORDS
        scored = []

        for url in urls:
            try:
                url_lower = url.lower()
                score = sum(
                    1 for kw in keywords
                    if re.search(rf"\b{re.escape(kw)}\b", url_lower)
                )
                if score > 0:
                    scored.append((url, score))
            except Exception:
                continue

        scored.sort(key=lambda x: x[1], reverse=True)
        return [url for url, _ in scored]




class FallbackURLDiscovery:
    def __init__(self, page: Page, extractor: DOMContentExtractor):
        self._page = page
        self._extractor = extractor

    async def discover_job_urls_from_domain(
        self,
        domain: str,
        try_common_paths: bool = False,
        extract_from_homepage: bool = True,
    ) -> list[str]:
        """
        Fallback: Navigate to domain and discover job URLs.
        
        Args:
            domain: The domain to explore (e.g., "openai.com")
            try_common_paths: Try common job page paths
            extract_from_homepage: Extract URLs from homepage first
            
        Returns:
            List of discovered job-related URLs
        """
        discovered_urls: set[str] = set()
        base_url = f"https://{domain.replace('https://', '').replace('http://', '').strip('/')}"

        # Step 1: Try homepage and extract all links
        if extract_from_homepage:
            homepage_urls = await self._extract_urls_from_page(base_url)
            discovered_urls.update(homepage_urls)

        # Step 2: Try common job paths
        if try_common_paths:
            for path in URLFilter.COMMON_JOB_PATHS:
                try:
                    test_url = f"{base_url}{path}"
                    
                    response = await self._page.goto(test_url, wait_until="domcontentloaded", timeout=10000)
                    
                    # Check if page exists (not 404)
                    if response and response.status < 400:
                        # Extract URLs from this page
                        page_urls = await self._extract_urls_from_current_page()
                        discovered_urls.update(page_urls)
                        
                        # Also add the successful path itself
                        discovered_urls.add(test_url)
                        
                        print(f"  ✅ Found job page: {test_url}")
                        
                        # If we found a valid careers page, we might not need to try all paths
                        if len(page_urls) > 5:
                            break
                            
                except Exception as e:
                    continue

        # Filter discovered URLs
        all_urls = list(discovered_urls)
        domain_filtered = URLFilter.filter_by_domain(all_urls, domain)
        web_filtered = URLFilter.filter_web_pages_only(domain_filtered)
        job_filtered = URLFilter.filter_job_urls(web_filtered)

        return job_filtered

    async def _extract_urls_from_page(self, url: str) -> list[str]:
        """Navigate to URL and extract all links."""
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return await self._extract_urls_from_current_page()
        except Exception as e:
            print(f"  ⚠️ Failed to load {url}: {e}")
            return []

    async def _extract_urls_from_current_page(self) -> list[str]:
        """Extract all URLs from current page."""
        try:
            urls = await self._page.evaluate(
                """
                () => {
                    const urls = [];
                    const links = document.querySelectorAll('a[href]');
                    links.forEach(link => {
                        const href = link.href;
                        if (href && href.startsWith('http')) {
                            urls.push(href);
                        }
                    });
                    return [...new Set(urls)];
                }
                """
            )
            return urls or []
        except Exception:
            return []
