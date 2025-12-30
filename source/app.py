"""
Simple FastAPI for background job scraping with parallel agents
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum
import asyncio
from uuid import uuid4

from service.mongdb_service import MongoDBService
from core.config import settings
from utils.main_scrapper import main_scrapper

app = FastAPI(title="Job Scraper API")

# Task tracking storage
tasks_db = {}


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScrapeRequest(BaseModel):
    urls: List[str] = Field(..., min_items=1, description="List of domains to scrape")
    num_agents: int = Field(default=2, ge=1, le=6, description="Number of parallel agents")


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str


class TaskDetail(BaseModel):
    task_id: str
    status: TaskStatus
    urls: List[str]
    num_agents: int
    total_urls: int
    completed_urls: List[str]
    failed_urls: List[str]
    jobs_scraped: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


async def process_urls_with_agent(
    urls_chunk: List[str],
    agent_id: int,
    task_id: str,
    mongo_service: MongoDBService
):
    """Process a chunk of URLs with a single agent"""
    for url in urls_chunk:
        try:
            print(f"[Agent {agent_id}] Processing: {url}")
            
            # Run scraper
            jobs = await main_scrapper(domain=url)
            
            # Save to MongoDB
            if jobs:
                for job_doc in jobs:
                    mongo_service.add_job(job_doc, update_if_exists=True)
                
                tasks_db[task_id]["jobs_scraped"] += len(jobs)
                print(f"[Agent {agent_id}] Saved {len(jobs)} jobs from {url}")
            
            # Mark as completed
            tasks_db[task_id]["completed_urls"].append(url)
            
        except Exception as e:
            print(f"[Agent {agent_id}] Error processing {url}: {str(e)}")
            tasks_db[task_id]["failed_urls"].append(url)


async def run_scraping_task(task_id: str, urls: List[str], num_agents: int):
    """Run the scraping task in background"""
    try:
        tasks_db[task_id]["status"] = TaskStatus.RUNNING
        
        # Initialize MongoDB
        mongo_service = MongoDBService(
            database_name=settings.DATABASE_NAME,
            collection_name="jobs"
        )
        
        # Divide URLs across agents
        chunk_size = len(urls) // num_agents
        remainder = len(urls) % num_agents
        
        url_chunks = []
        start = 0
        for i in range(num_agents):
            extra = 1 if i < remainder else 0
            end = start + chunk_size + extra
            if start < len(urls):
                url_chunks.append(urls[start:end])
            start = end
        
        # Run agents in parallel
        agent_tasks = [
            process_urls_with_agent(chunk, i + 1, task_id, mongo_service)
            for i, chunk in enumerate(url_chunks) if chunk
        ]
        
        await asyncio.gather(*agent_tasks)
        
        # Mark as completed
        tasks_db[task_id]["status"] = TaskStatus.COMPLETED
        tasks_db[task_id]["completed_at"] = datetime.now()
        
        mongo_service.close()
        
    except Exception as e:
        tasks_db[task_id]["status"] = TaskStatus.FAILED
        tasks_db[task_id]["error"] = str(e)
        tasks_db[task_id]["completed_at"] = datetime.now()


@app.post("/scrape", response_model=TaskResponse)
async def start_scraping(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks
):
    """
    Start a background scraping task
    
    - **urls**: List of domains to scrape (e.g., ["aceandtate.com", "ajr.org.uk"])
    - **num_agents**: Number of parallel agents (1-6)
    """
    task_id = str(uuid4())
    
    # Initialize task tracking
    tasks_db[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.PENDING,
        "urls": request.urls,
        "num_agents": request.num_agents,
        "total_urls": len(request.urls),
        "completed_urls": [],
        "failed_urls": [],
        "jobs_scraped": 0,
        "created_at": datetime.now(),
        "completed_at": None,
        "error": None
    }
    
    # Add to background tasks
    background_tasks.add_task(
        run_scraping_task,
        task_id,
        request.urls,
        request.num_agents
    )
    
    return TaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        message=f"Scraping task started with {request.num_agents} agents"
    )


@app.get("/tasks/{task_id}", response_model=TaskDetail)
async def get_task_status(task_id: str):
    """
    Get status of a scraping task
    
    - **task_id**: Task ID returned from /scrape endpoint
    """
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskDetail(**tasks_db[task_id])


@app.get("/tasks")
async def list_tasks():
    """List all tasks"""
    return {
        "total_tasks": len(tasks_db),
        "tasks": list(tasks_db.values())
    }


@app.get("/stats")
async def get_stats():
    """Get database statistics"""
    mongo_service = MongoDBService(
        database_name=settings.DATABASE_NAME,
        collection_name="jobs"
    )
    
    stats = mongo_service.get_stats()
    mongo_service.close()
    
    return stats


@app.get("/")
async def root():
    """API information"""
    return {
        "name": "Job Scraper API",
        "version": "1.0",
        "endpoints": {
            "POST /scrape": "Start scraping task",
            "GET /tasks/{task_id}": "Get task status",
            "GET /tasks": "List all tasks",
            "GET /stats": "Get database stats"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)