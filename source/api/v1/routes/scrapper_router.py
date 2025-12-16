from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional, Dict, Any, List, Tuple
import json
from enum import Enum

from models.heartbeat_models import HeartbeatModel
from utils.heartbeat import get_heartbeat
from utils.logging import setup_logger
from middlewares.trace_id_middleware import get_trace_id

from service.rag_service import QdrantRAG
from service.brower_scraper_service import BrowserScrapper
from service.job_analyzer import JobPageAnalyzer
from service.mongdb_service import MongoDBService
from service.search_engine_service import GoogleSearchSelenium

from utils.domain_name_filters import filter_domain_urls, filter_job_urls
from core.config import settings

from pydantic import BaseModel, Field

logger = setup_logger(__name__)


# ============================================================================
# Pydantic Models
# ============================================================================

class SearchEngineType(str, Enum):
    """Supported search engines"""
    GOOGLE = "google"
    BING = "bing"
    DUCKDUCKGO = "duckduckgo"


class ScrapperRequest(BaseModel):
    """Request model for scraper endpoint"""
    search_engine: SearchEngineType = Field(..., description="Search engine to use")
    domain: str = Field(..., description="Domain to search for jobs")
    use_qdrant: bool = Field(default=False, description="Whether to store in Qdrant")
    max_jobs: int = Field(default=50, description="Maximum number of jobs to scrape")


class URLMetadata(BaseModel):
    """Metadata about URLs found"""
    count: int
    urls: List[str]


class JobResult(BaseModel):
    """Result for a single job"""
    url: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ScrapperResponse(BaseModel):
    """Response model for scraper endpoint"""
    domain: str
    search_engine: str
    total_urls_found: int
    domain_urls_found: int
    job_urls_found: int
    jobs_processed: int
    jobs_successful: int
    jobs_failed: int
    results: List[JobResult] = []


# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter(
    prefix="/scrapper",
    tags=["Scrapper"]
)





# =============================================================================
# Usage Example
# =============================================================================


async def main():
    async with ChromeCDPManager() as manager:
        page = manager.page

        # Search example
        searcher = WebSearcher(page)

        print("=== DuckDuckGo Search ===")
        result = await searcher.search("Python web scraping", SearchEngine.DUCKDUCKGO)
        print(f"Success: {result.success}")
        print(f"Found {len(result.urls)} URLs:")
        for url in result.urls[:5]:
            print(f"  - {url}")

        print("\n=== Google Search ===")
        result = await searcher.search("Playwright automation", SearchEngine.GOOGLE)
        print(f"Success: {result.success}")
        print(f"Found {len(result.urls)} URLs:")
        for url in result.urls[:5]:
            print(f"  - {url}")

        # Content extraction example
        if result.urls:
            print("\n=== Content Extraction ===")
            await page.goto(result.urls[0])
            await page.wait_for_load_state("domcontentloaded")

            extractor = DOMContentExtractor(page)
            content = await extractor.extract(wait_seconds=2.0)

            print(f"Extracted {len(content.structured_text)} characters of text")
            print("Preview:")
            print(content.structured_text[:500])


if __name__ == "__main__":
    asyncio.run(main())

@router.post("/", response_model=ScrapperResponse)
async def scrape_page(
    request: ScrapperRequest,
    trace_id: str = Depends(get_trace_id)
):
    """
    Scrape jobs from a domain
    
    Args:
        request: ScrapperRequest with search_engine and domain
        trace_id: Request trace ID
        
    Returns:
        ScrapperResponse with scraping results
    """
    logger.info(
        f"[{trace_id}] Starting scrape for domain: {request.domain} "
        f"using {request.search_engine}"
    )
    
    # Initialize scraper service
    scraper = ScraperService(use_qdrant=request.use_qdrant)
    
    # Execute scraping
    response = scraper.scrape_domain_jobs(
        domain=request.domain,
        search_engine=request.search_engine.value,
        max_jobs=request.max_jobs
    )
    
    logger.info(f"[{trace_id}] Scraping completed for {request.domain}")
    print(response)
    # return "ok"
    return response