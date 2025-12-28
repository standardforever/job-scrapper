"""
FastAPI Routes for Job Scraper API
Handles all scraping-related endpoints
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import Optional
import asyncio
from datetime import datetime

from schemas.api_schemas import (
    BatchScrapeRequest,
    BatchScrapeResponse,
    SingleScrapeRequest,
    SingleScrapeResponse,
    ProgressResponse,
    StopRequest,
    StopResponse,
    ResourceInfo,
    BatchInfo,
    TaskInfo,
    TaskStatus,
    JobsListResponse
)

from service.resource_manager_service import resource_manager
from service.task_manager_service import get_task_manager, TaskManager
from service.batch_executor_service import run_batch_in_background, stop_current_batch, get_executor
from service.mongdb_service import MongoDBService
from utils.main_scrapper import main_scrapper
from core.config import settings
from utils.logging import setup_logger

logger = setup_logger(__name__)

router = APIRouter(prefix="/scrape", tags=["Scraper"])


def get_mongo_service() -> MongoDBService:
    """Dependency to get MongoDB service"""
    return MongoDBService(
        database_name=settings.DATABASE_NAME,
        collection_name="jobs"
    )


def get_resource_info() -> ResourceInfo:
    """Get current resource info"""
    info = resource_manager.get_resource_info_dict()
    return ResourceInfo(**info)


def convert_batch_to_response(batch: dict) -> BatchInfo:
    """Convert MongoDB batch document to BatchInfo response"""
    return BatchInfo(
        batch_id=batch["batch_id"],
        total_urls=batch["total_urls"],
        completed_urls=batch["completed_urls"],
        failed_urls=batch["failed_urls"],
        pending_urls=batch["pending_urls"],
        running_urls=batch["running_urls"],
        status=TaskStatus(batch["status"]),
        workers_active=batch["workers_active"],
        started_at=batch["created_at"],
        estimated_completion=None,
        total_jobs_found=batch.get("total_jobs_found", 0)
    )


def convert_task_to_response(task: dict) -> TaskInfo:
    """Convert MongoDB task document to TaskInfo response"""
    return TaskInfo(
        task_id=task["task_id"],
        url=task["url"],
        status=TaskStatus(task["status"]),
        worker_id=task.get("worker_id"),
        started_at=task.get("started_at"),
        completed_at=task.get("completed_at"),
        jobs_found=task.get("jobs_found", 0),
        error=task.get("error"),
        progress_percent=task.get("progress_percent", 0.0)
    )


@router.post("/batch", response_model=BatchScrapeResponse)
async def start_batch_scrape(
    request: BatchScrapeRequest,
    background_tasks: BackgroundTasks
):
    """
    Start a batch scraping job.
    
    Submits a list of URLs/domains for scraping. The server will:
    1. Check available resources
    2. Allocate appropriate number of workers
    3. Split URLs among workers
    4. Run scraping in background
    
    Returns immediately with batch ID for progress tracking.
    """
    logger.info(f"Batch scrape request: {len(request.urls)} URLs")
    
    # Check if server can accept the batch
    can_accept, reason = resource_manager.can_accept_batch()
    
    if not can_accept:
        return BatchScrapeResponse(
            success=False,
            message=reason,
            resource_info=get_resource_info()
        )
    
    # Allocate workers
    workers = await resource_manager.allocate_workers(request.max_workers)
    
    if workers == 0:
        return BatchScrapeResponse(
            success=False,
            message="Could not allocate workers. Server resources exhausted.",
            resource_info=get_resource_info()
        )
    
    try:
        
        # Get MongoDB service
        mongo_service = get_mongo_service()
        
        # Start batch in background
        batch_id = await run_batch_in_background(
            urls=request.urls,
            num_workers=workers,
            scrape_function=main_scrapper,
            mongo_service=mongo_service,
            max_records_per_file=request.max_records_per_file
        )
        
        # Get batch info
        task_manager = get_task_manager()
        batch = await task_manager.get_batch_info(batch_id)
        
        return BatchScrapeResponse(
            success=True,
            message=f"Batch started with {workers} workers",
            batch_id=batch_id,
            batch_info=convert_batch_to_response(batch) if batch else None,
            resource_info=get_resource_info()
        )
        
    except Exception as e:
        logger.error(f"Error starting batch: {e}")
        await resource_manager.release_workers()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/single", response_model=SingleScrapeResponse)
async def scrape_single_url(request: SingleScrapeRequest):
    """
    Scrape a single URL synchronously.
    
    Use this for testing or quick single-URL scrapes.
    Waits for completion and returns results immediately.
    """
    logger.info(f"Single scrape request: {request.url}")
    
    # Check if server is busy
    if resource_manager.is_server_busy():
        raise HTTPException(
            status_code=503,
            detail="Server is busy processing a batch. Try again later or check /scrape/progress"
        )
    
    start_time = datetime.now()
    
    try:
        # Import and run scraper
       
        
        # Allocate minimal resources
        await resource_manager.allocate_workers(1)
        
        try:
            # Run scraping
            jobs = await main_scrapper(domain=request.url)
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Save to MongoDB
            mongo_service = get_mongo_service()
            for job_doc in jobs:
                if job_doc:
                    mongo_service.add_job(job_doc, update_if_exists=True)
            
            return SingleScrapeResponse(
                success=True,
                message=f"Scraping completed successfully",
                url=request.url,
                jobs_found=len(jobs),
                jobs=jobs,
                duration_seconds=duration
            )
            
        finally:
            await resource_manager.release_workers()
            
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error(f"Error in single scrape: {e}")
        
        return SingleScrapeResponse(
            success=False,
            message="Scraping failed",
            url=request.url,
            error=str(e),
            duration_seconds=duration
        )


@router.get("/progress", response_model=ProgressResponse)
async def get_scrape_progress():
    """
    Get current scraping progress.
    
    Returns information about the current batch including:
    - Overall batch status
    - Per-task progress
    - Resource usage
    """
    task_manager = get_task_manager()
    progress = await task_manager.get_progress()
    
    batch_info = None
    tasks = []
    
    if progress["batch_info"]:
        batch_info = convert_batch_to_response(progress["batch_info"])
    
    if progress["tasks"]:
        tasks = [convert_task_to_response(t) for t in progress["tasks"]]
    
    return ProgressResponse(
        is_running=progress["is_running"],
        batch_info=batch_info,
        tasks=tasks,
        resource_info=get_resource_info()
    )


@router.get("/status")
async def get_scrape_status():
    """
    Quick status check for current scraping operation.
    
    Lightweight endpoint for polling.
    """
    task_manager = get_task_manager()
    batch = await task_manager.get_active_batch_info()
    
    if not batch:
        return {
            "is_running": False,
            "batch_id": None,
            "message": "No active batch"
        }
    
    return {
        "is_running": batch["status"] == "running",
        "batch_id": batch["batch_id"],
        "total_urls": batch["total_urls"],
        "completed_urls": batch["completed_urls"],
        "failed_urls": batch["failed_urls"],
        "pending_urls": batch["pending_urls"],
        "running_urls": batch["running_urls"],
        "progress_percent": round(
            (batch["completed_urls"] + batch["failed_urls"]) / batch["total_urls"] * 100, 2
        ) if batch["total_urls"] > 0 else 0,
        "workers_active": batch["workers_active"],
        "total_jobs_found": batch.get("total_jobs_found", 0)
    }


@router.post("/stop", response_model=StopResponse)
async def stop_scraping(request: StopRequest = None):
    """
    Stop current scraping operation.
    
    Gracefully stops all workers and marks pending tasks as cancelled.
    Running tasks will complete before workers stop.
    
    Use force=true to attempt immediate cancellation.
    """
    logger.info("Stop request received")
    
    task_manager = get_task_manager()
    active_batch = task_manager.get_active_batch_id()
    
    if not active_batch:
        return StopResponse(
            success=False,
            message="No active batch to stop",
            stopped_tasks=0
        )
    
    try:
        stopped = await stop_current_batch()
        
        return StopResponse(
            success=True,
            message=f"Batch stopped. {stopped} pending tasks cancelled.",
            stopped_tasks=stopped
        )
        
    except Exception as e:
        logger.error(f"Error stopping batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/resources", response_model=ResourceInfo)
async def get_resources():
    """
    Get current server resource information.
    
    Shows CPU, memory usage, and recommended worker count.
    """
    return get_resource_info()


@router.get("/history")
async def get_batch_history(limit: int = 10):
    """
    Get history of recent batches.
    
    Args:
        limit: Maximum number of batches to return (default: 10)
    """
    task_manager = get_task_manager()
    batches = await task_manager.get_recent_batches(limit=limit)
    
    return {
        "batches": [
            {
                "batch_id": b["batch_id"],
                "status": b["status"],
                "total_urls": b["total_urls"],
                "completed_urls": b["completed_urls"],
                "failed_urls": b["failed_urls"],
                "total_jobs_found": b.get("total_jobs_found", 0),
                "created_at": b["created_at"],
                "completed_at": b.get("completed_at")
            }
            for b in batches
        ]
    }


@router.get("/batch/{batch_id}")
async def get_batch_details(batch_id: str):
    """
    Get detailed information about a specific batch.
    
    Args:
        batch_id: The batch ID to query
    """
    task_manager = get_task_manager()
    batch = await task_manager.get_batch_info(batch_id)
    
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    tasks = await task_manager.get_batch_tasks(batch_id, limit=100)
    
    return {
        "batch": convert_batch_to_response(batch),
        "tasks": [convert_task_to_response(t) for t in tasks]
    }


@router.get("/jobs", response_model=JobsListResponse)
async def list_scraped_jobs(
    page: int = 1,
    page_size: int = 20,
    location: Optional[str] = None,
    company: Optional[str] = None
):
    """
    List scraped jobs from database.
    
    Args:
        page: Page number (1-indexed)
        page_size: Items per page
        location: Filter by location
        company: Filter by company
    """
    mongo_service = get_mongo_service()
    
    # Build filters
    filters = {}
    if location:
        filters["location"] = {"$regex": location, "$options": "i"}
    if company:
        filters["company"] = {"$regex": company, "$options": "i"}
    
    # Get total count
    total = mongo_service.count_jobs(filters)
    total_pages = (total + page_size - 1) // page_size
    
    # Get jobs
    skip = (page - 1) * page_size
    jobs = mongo_service.find_jobs(
        filters=filters,
        limit=page_size,
        skip=skip,
        sort_by=[("scraped_at", -1)]
    )
    
    return JobsListResponse(
        success=True,
        total_count=total,
        jobs=jobs,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@router.get("/jobs/stats")
async def get_jobs_stats():
    """
    Get statistics about scraped jobs.
    """
    mongo_service = get_mongo_service()
    stats = mongo_service.get_stats()
    
    return {
        "stats": stats,
        "top_locations": mongo_service.get_jobs_by_location(limit=10),
        "top_companies": mongo_service.get_jobs_by_company(limit=10),
        "top_skills": mongo_service.get_top_skills(limit=20)
    }