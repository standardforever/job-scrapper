# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from middlewares.logger_middleware import LoggingMiddleware
# from middlewares.trace_id_middleware import TraceIDMiddleware
# # from api.v1.routes.scrapper_router import router as scrapper_router




# app = FastAPI(
#     title="RAG Agent API",
#     version="1.0.0"
# )


# # CORS middleware
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# #Added logging middleware
# app.add_middleware(LoggingMiddleware)

# #Trace ID middleware
# app.add_middleware(TraceIDMiddleware)

# # app.include_router(scrapper_router)


# @app.get("/health")
# async def health_check():
#     return {"status": "healthy", "service": "api"}

# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run("main:app", reload=True)





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




async def main_scrapper(domain: str):
    config = JobScraperConfig(
        openai_api_key=settings.OPENAI_API_KEY,
        llm_model="gpt-4o-mini",
    )

    extract_config = ExtractionConfig(
            handle_cookies=True,
            handle_popups=True,
            scroll_to_load=True,  # For infinite scroll pages
            wait_seconds=3.0,
        )

    # Initialize MongoDB
    mongo_service = MongoDBService(
        database_name=settings.DATABASE_NAME,
        collection_name="jobs",
    )

    async with ChromeCDPManager() as manager:
        page = manager.page
        
        extractor = DOMContentExtractor(page, extract_config)

        # extractor = DOMContentExtractor(page)
        searcher = WebSearcher(page)
        analyzer = JobPageAnalyzer(api_key=config.openai_api_key, model=config.llm_model)
        llm = ChatOpenAI(model=config.llm_model)
        tracker = URLTracker()
        fallback_discovery = FallbackURLDiscovery(page, extractor)

        browser = BrowserSession(cdp_url=manager.cdp_url, keep_alive=True)
        await browser.start()

        try:
            print("=== Search & Filter ===")

            search_result = await searcher.search(f"{domain} jobs", SearchEngine.DUCKDUCKGO)

            job_filtered = []
            if search_result.success:
                domain_filtered = URLFilter.filter_by_domain(search_result.urls, domain)
                print(f"Domain filtered: {len(domain_filtered)} URLs")
                web_page_fitered = URLFilter.filter_web_pages_only(domain_filtered)

                job_filtered = URLFilter.filter_job_urls(web_page_fitered)
                print(f"Job filtered: {len(job_filtered)} URLs")

            # === FALLBACK: Direct domain exploration ===
            if not job_filtered:
                print("\n⚠️ No job URLs from search. Trying fallback discovery...")
                job_filtered = await fallback_discovery.discover_job_urls_from_domain(
                    domain=domain,
                    try_common_paths=False,
                    extract_from_homepage=True,
                )
                print(f"Fallback discovered: {len(job_filtered)} URLs")

            if not job_filtered:
                print("❌ No job URLs found even with fallback")
                return []
            
            print("\n=== Job Scraping ===")
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
                if tracker.should_skip(url):
                    print(f"\n⏭️ Skipping (already processed): {url}")
                    continue

                result = await scraper.scrape_jobs(url)
    
                print(f"\n🎯 Found {len(result.jobs)} jobs from {url}")

                # all_scraped_jobs.extend(result.jobs)
                all_scraped_jobs.extend(result.jobs)

                remaining = tracker.filter_unvisited(job_filtered)
                print(f"   Remaining URLs to process: {len(remaining)}")

            print(f"\n{'=' * 50}")
            print(f"📊 Scraping Stats:")
            print(f"   Total jobs found: {len(all_scraped_jobs)}")

            # Scrape job details and save to database
            all_detail_jobs = []
            if all_scraped_jobs:
                print("\n=== Scraping Job Details & Saving to Database ===")

                for i, job in enumerate(all_scraped_jobs):
                    if not job.url:
                        continue

                    print(f"\n  [{i + 1}/{len(all_scraped_jobs)}] Processing: {job.title or job.url}")

                    # # Check if job already exists in database
                    # existing_job = mongo_service.get_job_by_url(job.url)
                    # if existing_job and existing_job.get("raw_details"):
                    #     print(f"    ⏭️ Job already exists with details, skipping scrape")
                    #     jobs_updated += 1
                    #     continue

                    # Scrape details if not already visited
                    # if not tracker.is_visited(job.url):
                    try:
                        page = await browser.get_current_page()
                        await page.goto(job.url)
                        await asyncio.sleep(config.page_load_wait)
                        tracker.mark_visited(job.url)

                        analysis = await analyzer.analyze(
                            job.url,
                            (await extractor.extract()).structured_text,
                            prompt_type=AnalysisPromptType.STRUCTURED,
                        )
                        if analysis.success:
                            job.details = analysis.response
                    except Exception as e:
                        print(f"    ❌ Error scraping details: {e}")

                    # Detect ATS and create document
                    ats_info = ATSDetector.detect_ats(job.url, domain)
                    job_doc = job.details or {}

 
                    job_doc["url"] = job.url
                    job_doc["is_known_ats"] = ats_info["is_known_ats"]
                    job_doc["url"] = job.url
                    job_doc['is_ats'] = ats_info["is_ats"]
                    job_doc['is_external_application'] = ats_info["is_external_application"]
                    job_doc['ats_provider'] = ats_info["ats_provider"]
                    job_doc['detection_reason'] = ats_info["detection_reason"]
        
                    print(job_doc)
                    all_detail_jobs.append(job_doc)
                return all_detail_jobs
                # Save to database (update if exists)
                job_id = mongo_service.add_job(
                    job_data=job_doc,
                    update_if_exists=True,
                )

                if job_id:
                    # if existing_job:
                    #     jobs_updated += 1
                    print(f"    ✅ Updated in database: {job_id}")
                    # else:
                    #     jobs_saved += 1
                    print(f"    ✅ Saved to database: {job_id}")

                    # Log ATS detection
                    if job_doc.get("is_ats"):
                        print(f"    🔗 ATS Detected: {job_doc.get("ats_provider")}")
                else:
                    print(f"    ⚠️ Failed to save job")

            # Final stats
            print(f"\n{'=' * 50}")
            print(f"📊 Final Stats:")
            print(f"   Total jobs scraped: {len(all_scraped_jobs)}")
            print(f"   Jobs saved (new): {jobs_saved}")
            print(f"   Jobs updated: {jobs_updated}")
            print(f"   Database stats: {mongo_service.get_stats()}")
            print(f"   Tracker stats: {tracker.get_stats()}")

            return all_scraped_jobs

        finally:
            await browser.stop()
            mongo_service.close()







async def process_single_url(url: str, file_manager: JobFileManager) -> dict:
    """Process a single URL and save results."""
    result = {
        "url": url,
        "status": "pending",
        "jobs_found": 0,
        "error": None
    }
    
    try:
        # Your existing scraping logic here
        # job_doc = await scrape_job_detail(url)
        
        # Example placeholder - replace with your actual scraping
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
        "accordmat.org",
        "ajr.org.uk",
        "aish.org.uk",
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