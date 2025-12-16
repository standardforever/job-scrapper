import asyncio
from dataclasses import dataclass
from typing import Any, Optional
from service.brower_scraper_service import DOMContentExtractor
from models.agent_output_models import PaginationCheck
from browser_use import Agent, BrowserSession, ChatOpenAI
from service.job_analyzer import JobPageAnalyzer, AnalysisPromptType
from utils.text_processor import TextProcessor

# =============================================================================
# Pagination Handlers
# =============================================================================


@dataclass
class PaginationConfig:
    max_clicks: int = 10
    wait_after_click: float = 5.0
    content_wait: float = 7.0


class PaginationHandler:
    def __init__(
        self,
        browser: BrowserSession,
        llm: ChatOpenAI,
        extractor: DOMContentExtractor,
        config: Optional[PaginationConfig] = None,
    ):
        self._browser = browser
        self._llm = llm
        self._extractor = extractor
        self._config = config or PaginationConfig()

    async def _extract_content(self) -> str:
        content = await self._extractor.extract(wait_seconds=self._config.content_wait)
        return content.structured_text

    async def handle_pagination(self, base_url: str) -> list[str]:
        print("\n🔄 Started Pagination Handler\n")
        all_contents: list[str] = []

        content = await self._extract_content()
        all_contents.append(content)
        print(f"📄 Scraped initial page: {len(content)} chars")

        click_count = 0

        while click_count < self._config.max_clicks:
            agent = Agent(
                browser=self._browser,
                llm=self._llm,
                task=(
                    "Check if pagination navigation is visible on the page. "
                    "If a 'Next' page button or link is present, click it once only to move forward by one page. "
                    "Do not click 'Previous' or any earlier page numbers. "
                    "Do not repeat the action. "
                    "Do not extract or analyse job data. "
                    "Stop immediately after the click and return TASK COMPLETED."
                ),
                output_model_schema=PaginationCheck,
                max_steps=3,
            )

            result = await agent.run(max_steps=5)
            click_count += 1
            print(f"🖱️ Click #{click_count} completed")

            structured = result.structured_output.model_dump() if result.structured_output else {}
            print(f"📊 Output: {structured}")

            if not structured.get("has_pagination"):
                break

            content = await self._extract_content()
            all_contents.append(content)
            print(f"📄 Scraped page #{click_count + 1}: {len(content)} chars")

        print(f"\n✅ Total pages scraped: {len(all_contents)}\n")
        return all_contents

    async def handle_load_more(
        self,
        base_url: str,
        button_text: Optional[str] = None,
    ) -> list[str]:
        print("\n🔄 Started Load More Handler\n")

        content = await self._extract_content()
        combined_text = content
        print(f"📄 Scraped initial page: {len(combined_text)} chars")

        page = await self._browser.get_current_page()
        prompt = (
            f"Find the clickable element whose visible text most closely matches "
            f"'{button_text or 'Load More'}' and is used to load or show more job listings on this page."
        )

        click_count = 0

        while click_count < self._config.max_clicks:
            click_count += 1

            button = await page.get_element_by_prompt(prompt, llm=self._llm)
            if not button:
                break

            await button.click("left")
            await asyncio.sleep(self._config.wait_after_click)

            new_content = await self._extract_content()
            combined_text = TextProcessor.append_non_overlapping(combined_text, new_content)
            print(f"📄 Scraped after click #{click_count}: {len(new_content)} chars")

        print(f"\n✅ Total content length: {len(combined_text)}\n")
        return TextProcessor.split_into_chunks(combined_text)


# =============================================================================
# Job Scraper
# =============================================================================


@dataclass
class JobScraperConfig:
    max_navigation: int = 2
    page_load_wait: float = 5.0
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"


@dataclass
class JobEntry:
    title: str
    url: str
    details: Optional[dict[str, Any]] = None


class JobScraper:
    def __init__(
        self,
        browser: BrowserSession,
        llm: ChatOpenAI,
        extractor: "DOMContentExtractor",
        analyzer: JobPageAnalyzer,
        config: Optional[JobScraperConfig] = None,
    ):
        self._browser = browser
        self._llm = llm
        self._extractor = extractor
        self._analyzer = analyzer
        self._config = config or JobScraperConfig()
        self._pagination_handler = PaginationHandler(browser, llm, extractor)

    async def _get_page(self):
        return await self._browser.get_current_page()

    async def _navigate(self, url: str) -> None:
        page = await self._get_page()
        await page.goto(url)
        await asyncio.sleep(self._config.page_load_wait)

    async def _extract_and_analyze(self, url: str) -> dict[str, Any]:
        content = await self._extractor.extract()
        result = await self._analyzer.analyze(url, content.structured_text)
        return result.response if result.success else {}

    async def scrape_jobs(self, url: str) -> list[JobEntry]:
        print(f"🚀 Starting job scrape for: {url}")

        await self._navigate(url)
        nav_count = 0
        all_jobs: list[JobEntry] = []

        while True:
            content = await self._extractor.extract()
            analysis = await self._analyzer.analyze(url, content.structured_text, json_resonse=True)

            if not analysis.success:
                print(f"❌ Analysis failed: {analysis.error}")
                break

            result = analysis.response
            print(f"📊 Analysis result: {result}")

            page_category = result.get("page_category", "")

            if page_category == "not_job_related":
                print(f"⏭️ Page not job related: {url}")
                return all_jobs

            if result.get("next_action") == "navigate":
                if nav_count >= self._config.max_navigation:
                    print(f"⏭️ Max navigation reached ({self._config.max_navigation})")
                    return all_jobs

                nav_target = result.get("next_action_target", {})
                nav_url = nav_target.get("url", "")
                nav_url = TextProcessor.normalize_url(nav_url, result.get("domain_name", ""))

                if nav_url and nav_url != url:
                    nav_count += 1
                    url = nav_url
                    await self._navigate(url)
                    print(f"🔄 Navigated to: {url} ({nav_count}/{self._config.max_navigation})")
                    continue

                link_text = nav_target.get("link_text", "")
                if link_text:
                    nav_count += 1
                    page = await self._get_page()
                    prompt = (
                        f"Find the clickable element whose visible text most closely matches "
                        f"'{link_text}' and is used to navigate to the job listings page."
                    )
                    button = await page.get_element_by_prompt(prompt, llm=self._llm)
                    if button:
                        await button.click("left")
                        await asyncio.sleep(self._config.page_load_wait)
                        print(f"🖱️ Clicked navigation element: {link_text}")
                        continue

                return all_jobs

            if page_category == "jobs_listed":
                jobs_on_page = result.get("jobs_listed_on_page", [])
                for job in jobs_on_page:
                    all_jobs.append(JobEntry(
                        title=job.get("title", ""),
                        url=job.get("job_url", ""),
                    ))
                print(f"✅ Found {len(jobs_on_page)} jobs on page")

                pagination = result.get("pagination", {})

                if not pagination.get("is_paginated_page") and pagination.get("has_more_pages"):
                    contents = await self._pagination_handler.handle_load_more(url)
                    for chunk in contents:
                        chunk_analysis = await self._analyzer.analyze(url, chunk)
                        if chunk_analysis.success:
                            for job in chunk_analysis.response.get("jobs_listed_on_page", []):
                                all_jobs.append(JobEntry(
                                    title=job.get("title", ""),
                                    url=job.get("job_url", ""),
                                ))
                    return all_jobs

                if pagination.get("is_paginated_page"):
                    contents = await self._pagination_handler.handle_pagination(url)
                    for page_content in contents[1:]:
                        page_analysis = await self._analyzer.analyze(url, page_content)
                        if page_analysis.success:
                            for job in page_analysis.response.get("jobs_listed_on_page", []):
                                all_jobs.append(JobEntry(
                                    title=job.get("title", ""),
                                    url=job.get("job_url", ""),
                                ))
                    return all_jobs

                return all_jobs

            break

        return all_jobs

    async def scrape_job_details(self, jobs: list[JobEntry]) -> list[JobEntry]:
        print(f"\n📝 Scraping details for {len(jobs)} jobs\n")

        for i, job in enumerate(jobs):
            if not job.url:
                continue

            print(f"  [{i + 1}/{len(jobs)}] {job.title}")

            try:
                await self._navigate(job.url)
                analysis = await self._analyzer.analyze(
                    job.url,
                    (await self._extractor.extract()).structured_text,
                    prompt_type=AnalysisPromptType.STRUCTURED,
                )
                if analysis.success:
                    job.details = analysis.response
            except Exception as e:
                print(f"    ❌ Error: {e}")

        return jobs











from urllib.parse import urlparse, urlunparse

class URLTracker:
    def __init__(self):
        self._visited: set[str] = set()
        self._scraped_jobs: set[str] = set()

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url.lower().rstrip("/"))
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc.replace("www.", ""),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        ))
        return normalized

    def mark_visited(self, url: str) -> None:
        self._visited.add(self.normalize_url(url))

    def mark_job_scraped(self, url: str) -> None:
        self._scraped_jobs.add(self.normalize_url(url))

    def is_visited(self, url: str) -> bool:
        return self.normalize_url(url) in self._visited

    def is_job_scraped(self, url: str) -> bool:
        return self.normalize_url(url) in self._scraped_jobs

    def should_skip(self, url: str) -> bool:
        normalized = self.normalize_url(url)
        return normalized in self._visited or normalized in self._scraped_jobs

    def filter_unvisited(self, urls: list[str]) -> list[str]:
        return [url for url in urls if not self.should_skip(url)]

    def get_stats(self) -> dict:
        return {
            "visited_pages": len(self._visited),
            "scraped_jobs": len(self._scraped_jobs),
        }
    


@dataclass
class ScrapeResult:
    jobs: list["JobEntry"]
    visited_urls: list[str]
    job_detail_urls: list[str]


class TrackedJobScraper:
    def __init__(
        self,
        browser: BrowserSession,
        llm: ChatOpenAI,
        extractor: "DOMContentExtractor",
        analyzer: "JobPageAnalyzer",
        tracker: URLTracker,
        config: Optional["JobScraperConfig"] = None,
    ):
        self._browser = browser
        self._llm = llm
        self._extractor = extractor
        self._analyzer = analyzer
        self._tracker = tracker
        self._config = config or JobScraperConfig()
        self._current_visited: list[str] = []

    async def _get_page(self):
        return await self._browser.get_current_page()

    async def _navigate(self, url: str) -> None:
        page = await self._get_page()
        await page.goto(url)
        await asyncio.sleep(self._config.page_load_wait)
        self._tracker.mark_visited(url)
        self._current_visited.append(url)

    async def scrape_jobs(self, url: str) -> ScrapeResult:
        self._current_visited = []

        if self._tracker.should_skip(url):
            print(f"⏭️ Skipping already visited URL: {url}")
            return ScrapeResult(jobs=[], visited_urls=[], job_detail_urls=[])

        print(f"🚀 Starting job scrape for: {url}")
        await self._navigate(url)

        nav_count = 0
        all_jobs: list[JobEntry] = []

        while True:
            content = await self._extractor.extract()
            analysis = await self._analyzer.analyze(url, content.structured_text)
            # print(analysis)
            if not analysis.success:
                print(f"❌ Analysis failed: {analysis.error}")
                break

            result = analysis.response
            page_category = result.get("page_category", "")
            # print(analysis.response)
            if page_category == "not_job_related":
                print(f"⏭️ Page not job related: {url}")
                break


            if page_category == "single_job_posting":
                print("Working on single job posting")
                jobs_on_page = result.get("jobs_listed_on_page", [])
                job_detail_urls = []

                for job in jobs_on_page:
                    job_url = job.get("job_url") or url
                    all_jobs.append(JobEntry(
                        title=job.get("title", ""),
                        url=job_url,
                    ))
                    if job_url:
                        job_detail_urls.append(job_url)
                        self._tracker.mark_job_scraped(job_url)
                return ScrapeResult(
                    jobs=all_jobs,
                    visited_urls=self._current_visited,
                    job_detail_urls=[j.url for j in all_jobs if j.url],
                )

            if result.get("next_action") == "navigate":
                if nav_count >= self._config.max_navigation:
                    print(f"⏭️ Max navigation reached")
                    break

                nav_target = result.get("next_action_target", {})
                nav_url = nav_target.get("url", "")
                nav_url = TextProcessor.normalize_url(nav_url, result.get("domain_name", ""))

                if nav_url and nav_url != url:
                    if self._tracker.should_skip(nav_url):
                        print(f"⏭️ Navigation target already visited: {nav_url}")
                        break

                    nav_count += 1
                    url = nav_url
                    await self._navigate(url)
                    print(f"🔄 Navigated to: {url}")
                    continue

                link_text = nav_target.get("link_text", "")
                if link_text:
                    nav_count += 1
                    page = await self._get_page()
                    prompt = (
                        f"Find the clickable element whose visible text most closely matches "
                        f"'{link_text}' and is used to navigate to the job listings page."
                    )
                    button = await page.get_element_by_prompt(prompt, llm=self._llm)
                    if button:
                        await button.click("left")
                        await asyncio.sleep(self._config.page_load_wait)

                        current_url = page.url
                        self._tracker.mark_visited(current_url)
                        self._current_visited.append(current_url)
                        print(f"🖱️ Clicked and navigated to: {current_url}")
                        continue

                break

            if page_category == "jobs_listed":
                jobs_on_page = result.get("jobs_listed_on_page", [])
                job_detail_urls = []

                for job in jobs_on_page:
                    job_url = job.get("job_url", "")
                    all_jobs.append(JobEntry(
                        title=job.get("title", ""),
                        url=job_url,
                    ))
                    if job_url:
                        job_detail_urls.append(job_url)
                        self._tracker.mark_job_scraped(job_url)

                print(f"✅ Found {len(jobs_on_page)} jobs on page")

                pagination = result.get("pagination", {})

                if not pagination.get("is_paginated_page") and pagination.get("has_more_pages"):
                    handler = PaginationHandler(self._browser, self._llm, self._extractor)
                    contents = await handler.handle_load_more(url)

                    for chunk in contents:
                        chunk_analysis = await self._analyzer.analyze(url, chunk)
                        if chunk_analysis.success:
                            for job in chunk_analysis.response.get("jobs_listed_on_page", []):
                                job_url = job.get("job_url", "")
                                all_jobs.append(JobEntry(
                                    title=job.get("title", ""),
                                    url=job_url,
                                ))
                                if job_url:
                                    self._tracker.mark_job_scraped(job_url)

                    return ScrapeResult(
                        jobs=all_jobs,
                        visited_urls=self._current_visited,
                        job_detail_urls=[j.url for j in all_jobs if j.url],
                    )

                if pagination.get("is_paginated_page"):
                    handler = PaginationHandler(self._browser, self._llm, self._extractor)
                    contents = await handler.handle_pagination(url)

                    for page_content in contents[1:]:
                        page_analysis = await self._analyzer.analyze(url, page_content)
                        if page_analysis.success:
                            for job in page_analysis.response.get("jobs_listed_on_page", []):
                                job_url = job.get("job_url", "")
                                all_jobs.append(JobEntry(
                                    title=job.get("title", ""),
                                    url=job_url,
                                ))
                                if job_url:
                                    self._tracker.mark_job_scraped(job_url)

                    return ScrapeResult(
                        jobs=all_jobs,
                        visited_urls=self._current_visited,
                        job_detail_urls=[j.url for j in all_jobs if j.url],
                    )

                return ScrapeResult(
                    jobs=all_jobs,
                    visited_urls=self._current_visited,
                    job_detail_urls=[j.url for j in all_jobs if j.url],
                )

            break

        return ScrapeResult(
            jobs=all_jobs,
            visited_urls=self._current_visited,
            job_detail_urls=[j.url for j in all_jobs if j.url],
        )

    async def scrape_job_details(
        self,
        jobs: list["JobEntry"],
        skip_already_scraped: bool = True,
    ) -> list["JobEntry"]:
        print(f"\n📝 Scraping details for {len(jobs)} jobs\n")

        for i, job in enumerate(jobs):
            if not job.url:
                continue

            if skip_already_scraped and self._tracker.is_visited(job.url):
                print(f"  [{i + 1}/{len(jobs)}] ⏭️ Skipping (already visited): {job.title}")
                continue

            print(f"  [{i + 1}/{len(jobs)}] {job.title}")

            try:
                await self._navigate(job.url)
                analysis = await self._analyzer.analyze(
                    job.url,
                    (await self._extractor.extract()).structured_text,
                    prompt_type=AnalysisPromptType.STRUCTURED,
                )
                result = analysis.response
                if analysis.success and result.get('page_category', '') == "not_job_related":
                    continue
                if analysis.success:
                    job.details = analysis.response
            except Exception as e:
                print(f"    ❌ Error: {e}")

        return jobs



