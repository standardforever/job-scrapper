import re
from typing import  Optional
from urllib.parse import urlparse
from playwright.async_api import  Page
from service.brower_scraper_service import DOMContentExtractor

from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)



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
        logger.debug(
            "Filtering web pages only",
            extra={"input_count": len(urls)},
        )
        filtered = []
        skipped_count = 0
        for url in urls:
            url_lower = url.lower().split("?")[0]
            if not any(url_lower.endswith(ext) for ext in cls.SKIP_EXTENSIONS):
                filtered.append(url)
            else:
                skipped_count += 1
                logger.debug(
                    "URL skipped due to extension",
                    extra={"url": url},
                )
        
        logger.debug(
            "Web pages filtering completed",
            extra={
                "input_count": len(urls),
                "output_count": len(filtered),
                "skipped_count": skipped_count,
            },
        )
        return filtered

    @staticmethod
    def filter_by_domain(urls: list[str], domain: str) -> list[str]:
        logger.debug(
            "Filtering URLs by domain",
            extra={"input_count": len(urls), "domain": domain},
        )
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
                            logger.debug(
                                "URL matched domain",
                                extra={"url": url, "domain": domain},
                            )
                    else:
                        filtered.append(url)
                        logger.debug(
                            "URL matched subdomain",
                            extra={"url": url, "url_domain": url_domain, "domain": domain},
                        )
            except Exception as e:
                logger.debug(
                    "Failed to parse URL for domain filtering",
                    extra={"url": url, "error": str(e)},
                )
                continue

        logger.debug(
            "Domain filtering completed",
            extra={
                "input_count": len(urls),
                "output_count": len(filtered),
                "domain": domain,
            },
        )
        return filtered

    @classmethod
    def filter_job_urls(
        cls,
        urls: list[str],
        include_keywords: Optional[set[str]] = None,
    ) -> list[str]:
        keywords = include_keywords or cls.DEFAULT_JOB_KEYWORDS
        logger.debug(
            "Filtering job URLs",
            extra={
                "input_count": len(urls),
                "keywords_count": len(keywords),
            },
        )
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
                    logger.debug(
                        "URL matched job keywords",
                        extra={"url": url, "score": score},
                    )
            except Exception as e:
                logger.debug(
                    "Failed to score URL",
                    extra={"url": url, "error": str(e)},
                )
                continue

        scored.sort(key=lambda x: x[1], reverse=True)
        result = [url for url, _ in scored]
        
        logger.debug(
            "Job URL filtering completed",
            extra={
                "input_count": len(urls),
                "output_count": len(result),
                "top_score": scored[0][1] if scored else 0,
            },
        )
        return result


class FallbackURLDiscovery:
    def __init__(self, page: Page, extractor: DOMContentExtractor):
        self._page = page
        self._extractor = extractor
        logger.debug("FallbackURLDiscovery initialized")

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
        logger.info(
            "Starting job URL discovery from domain",
            extra={
                "domain": domain,
                "try_common_paths": try_common_paths,
                "extract_from_homepage": extract_from_homepage,
            },
        )
        discovered_urls: set[str] = set()
        base_url = f"https://{domain.replace('https://', '').replace('http://', '').strip('/')}"
        logger.debug(
            "Base URL constructed",
            extra={"base_url": base_url},
        )

        # Step 1: Try homepage and extract all links
        if extract_from_homepage:
            logger.debug("Extracting URLs from homepage")
            homepage_urls = await self._extract_urls_from_page(base_url)
            discovered_urls.update(homepage_urls)
            logger.debug(
                "Homepage URLs extracted",
                extra={"urls_found": len(homepage_urls)},
            )

        # Step 2: Try common job paths
        if try_common_paths:
            logger.debug(
                "Trying common job paths",
                extra={"paths_count": len(URLFilter.COMMON_JOB_PATHS)},
            )
            for path in URLFilter.COMMON_JOB_PATHS:
                try:
                    test_url = f"{base_url}{path}"
                    logger.debug(
                        "Testing common job path",
                        extra={"test_url": test_url, "path": path},
                    )
                    
                    response = await self._page.goto(test_url, wait_until="domcontentloaded", timeout=10000)
                    
                    # Check if page exists (not 404)
                    if response and response.status < 400:
                        # Extract URLs from this page
                        page_urls = await self._extract_urls_from_current_page()
                        discovered_urls.update(page_urls)
                        
                        # Also add the successful path itself
                        discovered_urls.add(test_url)
                        
                        logger.info(
                            "Found valid job page",
                            extra={
                                "test_url": test_url,
                                "status": response.status,
                                "urls_found": len(page_urls),
                            },
                        )
                        
                        # If we found a valid careers page, we might not need to try all paths
                        if len(page_urls) > 5:
                            logger.debug(
                                "Sufficient URLs found, stopping path search",
                                extra={"urls_count": len(page_urls)},
                            )
                            break
                            
                except Exception as e:
                    logger.debug(
                        "Failed to test job path",
                        extra={"test_url": test_url, "error": str(e)},
                    )
                    continue

        # Filter discovered URLs
        all_urls = list(discovered_urls)
        logger.debug(
            "Starting URL filtering",
            extra={"total_discovered": len(all_urls)},
        )
        
        domain_filtered = URLFilter.filter_by_domain(all_urls, domain)
        web_filtered = URLFilter.filter_web_pages_only(domain_filtered)
        job_filtered = URLFilter.filter_job_urls(web_filtered)

        logger.info(
            "Job URL discovery completed",
            extra={
                "domain": domain,
                "total_discovered": len(all_urls),
                "domain_filtered": len(domain_filtered),
                "web_filtered": len(web_filtered),
                "job_filtered": len(job_filtered),
            },
        )

        return job_filtered

    async def _extract_urls_from_page(self, url: str) -> list[str]:
        """Navigate to URL and extract all links."""
        logger.debug(
            "Extracting URLs from page",
            extra={"url": url},
        )
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
            urls = await self._extract_urls_from_current_page()
            logger.debug(
                "URLs extracted from page",
                extra={"url": url, "urls_count": len(urls)},
            )
            return urls
        except Exception as e:
            logger.warning(
                "Failed to load page for URL extraction",
                extra={"url": url, "error": str(e)},
            )
            return []

    async def _extract_urls_from_current_page(self) -> list[str]:
        """Extract all URLs from current page."""
        logger.debug("Extracting URLs from current page")
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
            result = urls or []
            logger.debug(
                "URLs extracted from current page",
                extra={"urls_count": len(result)},
            )
            return result
        except Exception as e:
            logger.warning(
                "Failed to extract URLs from current page",
                extra={"error": str(e)},
            )
            return []