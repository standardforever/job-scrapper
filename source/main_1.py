
# =============================================================================
# Main Integration Example
# =============================================================================

from service.search_engine_service import WebSearcher, SearchEngine
from service.brower_scraper_service import DOMContentExtractor, ExtractionConfig
from service.chromium_service import ChromeCDPManager
from service.job_analyzer import JobPageAnalyzer, AnalysisPromptType
from service.agent_service import JobScraperConfig, URLTracker, TrackedJobScraper, JobEntry
from utils.domain_name_filters import URLFilter, FallbackURLDiscovery
from utils.ats_detector import  ATSDetector
from browser_use import Agent, BrowserSession, ChatOpenAI
from core.config import settings
from service.mongdb_service import MongoDBService
from utils.file_storage import JobFileManager
from typing import List, Dict, Any
from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)




async def main_scrapper(domain: str) -> List[Dict[str, Any]]:
    logger.info(
        "Starting main scraper",
        extra={"domain": domain},
    )

    config = JobScraperConfig(
        openai_api_key=settings.OPENAI_API_KEY,
        llm_model="gpt-5-nano",
    )
    logger.debug(
        "JobScraperConfig initialized",
        extra={"llm_model": config.llm_model},
    )

    extract_config = ExtractionConfig(
        handle_cookies=True,
        handle_popups=True,
        scroll_to_load=True,  # For infinite scroll pages
        wait_seconds=3.0,
    )
    logger.debug(
        "ExtractionConfig initialized",
        extra={
            "handle_cookies": extract_config.handle_cookies,
            "handle_popups": extract_config.handle_popups,
            "scroll_to_load": extract_config.scroll_to_load,
            "wait_seconds": extract_config.wait_seconds,
        },
    )

    
    logger.debug(
        "MongoDBService initialized",
        extra={
            "database_name": settings.DATABASE_NAME,
            "collection_name": "jobs",
        },
    )

    async with ChromeCDPManager() as manager:
        logger.debug("ChromeCDPManager context entered")
        page = manager.page
        
        extractor = DOMContentExtractor(page, extract_config)

        # extractor = DOMContentExtractor(page)
        searcher = WebSearcher(page)
        analyzer = JobPageAnalyzer(api_key=config.openai_api_key, model=config.llm_model)
        llm = ChatOpenAI(model=config.llm_model)
        tracker = URLTracker()
        fallback_discovery = FallbackURLDiscovery(page, extractor)
        logger.debug("All services initialized")

        browser = BrowserSession(cdp_url=manager.cdp_url, keep_alive=True)
        await browser.start()
        logger.debug(
            "BrowserSession started",
            extra={"cdp_url": manager.cdp_url},
        )

        try:
            logger.info(
                "Starting search and filter phase",
                extra={"domain": domain},
            )

            search_query = f"{domain} jobs"
            logger.debug(
                "Executing web search",
                extra={"query": search_query, "engine": "DUCKDUCKGO"},
            )
            search_result = await searcher.search(search_query, SearchEngine.DUCKDUCKGO)

            job_filtered = []
            if search_result.success:
                logger.info(
                    "Search completed successfully",
                    extra={"urls_found": len(search_result.urls)},
                )
                domain_filtered = URLFilter.filter_by_domain(search_result.urls, domain)
                logger.debug(
                    "Domain filtering completed",
                    extra={"domain_filtered_count": len(domain_filtered)},
                )
                web_page_fitered = URLFilter.filter_web_pages_only(domain_filtered)
                logger.debug(
                    "Web page filtering completed",
                    extra={"web_page_filtered_count": len(web_page_fitered)},
                )

                job_filtered = URLFilter.filter_job_urls(web_page_fitered)
                logger.info(
                    "Job URL filtering completed",
                    extra={"job_filtered_count": len(job_filtered)},
                )
            else:
                logger.warning(
                    "Search failed",
                    extra={"error": search_result.error},
                )

            # === FALLBACK: Direct domain exploration ===
            if not job_filtered:
                logger.warning(
                    "No job URLs from search, trying fallback discovery",
                    extra={"domain": domain},
                )
                job_filtered = await fallback_discovery.discover_job_urls_from_domain(
                    domain=domain,
                    try_common_paths=False,
                    extract_from_homepage=True,
                )
                logger.info(
                    "Fallback discovery completed",
                    extra={"urls_discovered": len(job_filtered)},
                )

            if not job_filtered:
                logger.error(
                    "No job URLs found even with fallback",
                    extra={"domain": domain},
                )
                return []
            
            logger.info(
                "Starting job scraping phase",
                extra={"urls_to_process": len(job_filtered)},
            )
            scraper = TrackedJobScraper(
                browser=browser,
                llm=llm,
                extractor=extractor,
                analyzer=analyzer,
                tracker=tracker,
                config=config,
            )

            all_scraped_jobs: list[JobEntry] = []
            jobs_saved = 0
            jobs_updated = 0
    
            for url in job_filtered:
                url = tracker.normalize_full_path(url, domain)

                if tracker.should_skip(url):
                    logger.debug(
                        "Skipping already processed URL",
                        extra={"url": url},
                    )
                    continue

                logger.debug(
                    "Scraping jobs from URL",
                    extra={"url": url},
                )
                result = await scraper.scrape_jobs(url)
    
                logger.info(
                    "Jobs found from URL",
                    extra={"url": url, "jobs_count": len(result.jobs)},
                )

                all_scraped_jobs.extend(result.jobs)

                remaining = tracker.filter_unvisited(job_filtered)
                logger.debug(
                    "Remaining URLs to process",
                    extra={"remaining_count": len(remaining)},
                )

            logger.info(
                "Scraping stats",
                extra={"total_jobs_found": len(all_scraped_jobs)},
            )

            # Scrape job details and save to database
            all_detail_jobs = []
            if all_scraped_jobs:
                logger.info(
                    "Starting job details scraping and database saving",
                    extra={"jobs_to_process": len(all_scraped_jobs)},
                )

                for i, job in enumerate(all_scraped_jobs):
                    if not job.url:
                        logger.debug(
                            "Skipping job without URL",
                            extra={"job_index": i, "job_title": job.title},
                        )
                        continue

                    logger.debug(
                        "Processing job",
                        extra={
                            "job_index": i + 1,
                            "total_jobs": len(all_scraped_jobs),
                            "job_title": job.title,
                            "job_url": job.url,
                        },
                    )

                    try:
                        page = await browser.get_current_page()
                        
                        filter_domain = tracker.extract_domain(url)
                        job.url = tracker.normalize_full_path(job.url, filter_domain)
                        await page.goto(job.url)
                        await asyncio.sleep(config.page_load_wait)
                        tracker.mark_visited(job.url)
                        logger.debug(
                            "Navigated to job page",
                            extra={"job_url": job.url},
                        )
                        
                        text_extracted = await extractor.extract()
                        analysis = await analyzer.analyze(
                            job.url,
                            text_extracted.structured_text,
                            prompt_type=AnalysisPromptType.STRUCTURED,
                        )
                        if analysis.success:
                            job.details = analysis.response
                            logger.debug(
                                "Job details analysis successful",
                                extra={"job_url": job.url},
                            )
                        else:
                            logger.warning(
                                "Job details analysis failed",
                                extra={"job_url": job.url, "error": analysis.error},
                            )
                    except Exception as e:
                        logger.error(
                            "Error scraping job details",
                            extra={"job_url": job.url, "error": str(e)},
                            exc_info=True,
                        )

                    # Detect ATS and create document
                    ats_info = ATSDetector.detect_ats(job.url, domain)
                    logger.debug(
                        "ATS detection completed",
                        extra={
                            "job_url": job.url,
                            "is_ats": ats_info["is_ats"],
                            "ats_provider": ats_info["ats_provider"],
                        },
                    )

                    job_doc = job.details or {}
                    if not job_doc:
                        logger.debug("No job_doc details found",
                                     extra={"url": job.url})
                        continue

                    job_doc['main_domain'] = domain
                    job_doc["raw_text"] = text_extracted.structured_text
                    job_doc['filter_domain'] = url
                    job_doc["url"] = job.url
                    job_doc["is_known_ats"] = ats_info["is_known_ats"]
                    job_doc['is_ats'] = ats_info["is_ats"]
                    job_doc['is_external_application'] = ats_info["is_external_application"]
                    job_doc['ats_provider'] = ats_info["ats_provider"]
                    job_doc['detection_reason'] = ats_info["detection_reason"]
        
                    all_detail_jobs.append(job_doc)
                    logger.debug(
                        "Job document created",
                        extra={"job_url": job.url},
                    )

                logger.info(
                    "Job details scraping completed",
                    extra={
                        "total_detail_jobs": len(all_detail_jobs),
                        "jobs_saved": jobs_saved,
                        "jobs_updated": jobs_updated,
                    },
                )

                return all_detail_jobs
            return []

        except Exception as e:
            logger.error(
                "Error in main scraper",
                extra={"domain": domain, "error": str(e)},
                exc_info=True,
            )
            return []
        finally:
            logger.debug("Main scraper execution finished")
            await browser.stop()
    

            







async def process_single_url(url: str, file_manager: JobFileManager) -> dict:
    """Process a single URL and save results."""
    result = {
        "url": url,
        "status": "pending",
        "jobs_found": 0,
        "error": None
    }
    
    try:
        all_scraped_jobs = await main_scrapper(domain=url)  # Your existing main function
        for job_doc in all_scraped_jobs:
            # if job_doc:
            save_info = file_manager.add_job(job_doc)
            result["status"] = "success"
            result["jobs_found"] = 1
            result["save_info"] = save_info
            # else:
            #     result["status"] = "no_job_found"
            
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"Error processing {url}: {e}")
    
    return result


async def main_batch(urls: list[str], max_records_per_file: int = 50):
    """
    Process multiple URLs and save jobs to rotating JSON files.
    
    Args:
        urls: List of URLs/domains to process
        max_records_per_file: Number of records before creating a new file
    """
    # Initialize file manager
    file_manager = JobFileManager(
        output_dir="job_outputs",
        max_records_per_file=max_records_per_file,
        file_prefix="jobs"
    )
    # # # Initialize MongoDB
    # mongo_service = MongoDBService(
    #     database_name=settings.DATABASE_NAME,
    #     collection_name="jobs",
    # )
    
    print(f"Starting batch processing of {len(urls)} URLs")
    print(f"Output directory: {file_manager.output_dir}")
    print(f"Max records per file: {max_records_per_file}")
    print("-" * 50)
    
    results = {
        "total": len(urls),
        "success": 0,
        "no_job_found": 0,
        "errors": 0,
        "details": []
    }
    
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] Processing: {url}")
        
        result = await process_single_url(url, file_manager)
        results["details"].append(result)
        
        if result["status"] == "success":
            results["success"] += 1
        elif result["status"] == "no_job_found":
            results["no_job_found"] += 1
        else:
            results["errors"] += 1
    
    # Final stats
    print("\n" + "=" * 50)
    print("BATCH PROCESSING COMPLETE")
    print("=" * 50)
    print(f"Total URLs processed: {results['total']}")
    print(f"Successful: {results['success']}")
    print(f"No job found: {results['no_job_found']}")
    print(f"Errors: {results['errors']}")
    print(f"\nStorage stats: {file_manager.get_stats()}")
    
    return results


if __name__ == "__main__":
    import asyncio
    
    # List of URLs/domains to process
    urls_to_process = [
        # "aceandtate.com",
        "mynewterm.com",
        # "aish.org.uk",
        # Add more URLs here...
    ]
    
    # Or load from file
    # with open("urls_to_scrape.txt", "r") as f:
    #     urls_to_process = [line.strip() for line in f if line.strip()]
    
    asyncio.run(main_batch(
        urls=urls_to_process,
        max_records_per_file=50  # Creates new file after 50 records
    ))



# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(main(domain="accordmat.org")) # job found with Ats detection
#     # asyncio.run(main(domain="aish.org.uk")) # No job and use fallback class 
#     # asyncio.run(main(domain="ajr.org.uk")) # used for fallback class and job detected but email base registration
#     asyncio.run(main(domain="ajr.org.uk")) # used for fallback class and job detected but email base registration