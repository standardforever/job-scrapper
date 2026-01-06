"""
Job Scraper API - FastAPI application for background job scraping with parallel agents
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse
import asyncio
import pandas as pd
import io

from service.mongdb_service import MongoDBService
from core.config import settings
from utils.main_scrapper import main_scrapper
from utils.file_storage import JobFileManager, TaskStorage
from utils.convert_json_to_csv import read_all_jobs_from_files, generate_csv_from_jobs

# ============================================================================
# APP INITIALIZATION
# ============================================================================

app = FastAPI(
    title="Job Scraper API",
    description="Automated job scraping with parallel agents, CSV export, and task management",
    version="2.0"
)

# Task storage and tracking
tasks_db = TaskStorage()
running_agent_tasks: Dict[str, List[asyncio.Task]] = {}

# ============================================================================
# MODELS
# ============================================================================

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapeRequest(BaseModel):
    urls: List[str] = Field(..., min_items=1, description="List of domains to scrape")
    num_agents: int = Field(default=2, ge=1, le=5, description="Number of parallel agents")


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

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def extract_domain(url: str) -> str:
    """
    Extract clean domain from URL.
    
    Examples:
        https://www.example.com/jobs -> example.com
        example.com -> example.com
    """
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split('/')[0]
    domain = domain.split(':')[0]  # Remove port
    domain = domain.lower().strip()
    
    return domain


def get_active_tasks() -> list:
    """Get all tasks that are currently running or pending."""
    active_statuses = [TaskStatus.RUNNING, TaskStatus.PENDING]
    return [
        task for task in tasks_db.all().values()
        if task.get("status") in active_statuses
    ]


def check_can_start_new_task():
    """
    Check if a new task can be started.
    Raises HTTPException if there are active tasks.
    """
    active_tasks = get_active_tasks()
    
    if active_tasks:
        task_info = [
            f"Task {t['task_id'][:8]}... ({t['status']}, {len(t['completed_urls'])}/{t['total_urls']} completed)"
            for t in active_tasks
        ]
        raise HTTPException(
            status_code=409,  # 409 Conflict
            detail={
                "error": "Another scraping task is already running",
                "message": "Please wait for the current task to complete or cancel it before starting a new one",
                "active_tasks": task_info,
                "active_task_ids": [t["task_id"] for t in active_tasks]
            }
        )


def validate_spreadsheet_file(file: UploadFile = File(...)) -> UploadFile:
    """
    Validate that uploaded file is a CSV or XLSX.
    Raises HTTPException if validation fails.
    """
    allowed_extensions = ['.csv', '.xlsx', '.xls']
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed formats: CSV (.csv) or Excel (.xlsx, .xls)"
        )
    
    return file


async def read_spreadsheet(file: UploadFile, domain_column: str) -> list:
    """
    Read domains from CSV or XLSX file.
    
    Args:
        file: Uploaded file (CSV or XLSX)
        domain_column: Name of column containing domains
        
    Returns:
        List of domain strings
    """
    contents = await file.read()
    file_ext = Path(file.filename).suffix.lower()
    
    try:
        # Read based on file type
        if file_ext == '.csv':
            try:
                df = pd.read_csv(io.BytesIO(contents))
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(contents), encoding='latin-1')
        elif file_ext in ['.xlsx', '.xls']:
            df = pd.read_excel(io.BytesIO(contents), engine='openpyxl' if file_ext == '.xlsx' else None)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_ext}")
        
        # Check if domain column exists
        if domain_column not in df.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Column '{domain_column}' not found. Available columns: {', '.join(df.columns)}"
            )
        
        # Extract and clean domains
        domains = df[domain_column].dropna().astype(str).str.strip().tolist()
        domains = [d for d in domains if d and d.lower() != 'nan']
        
        if not domains:
            raise HTTPException(
                status_code=400,
                detail=f"No valid domains found in column '{domain_column}'"
            )
        
        return domains
        
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="File is empty")
    except pd.errors.ParserError as e:
        raise HTTPException(status_code=400, detail=f"Invalid file format: {str(e)}")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

# ============================================================================
# BACKGROUND TASK FUNCTIONS
# ============================================================================

async def process_urls_with_agent(
    urls_chunk: List[str],
    agent_id: int,
    task_id: str,
    file_manager: JobFileManager
):
    """Process a chunk of URLs with a single agent"""
    for url in urls_chunk:
        # Check if task was cancelled before starting new URL
        task_data = tasks_db.get(task_id)
        if task_data and task_data.get("cancelled", False):
            print(f"[Agent {agent_id}] Task {task_id} was cancelled, stopping immediately...")
            return
        
        result = {
            "url": url,
            "status": "pending",
            "jobs_found": 0,
            "error": None
        }
        
        # Clean domain
        domain = extract_domain(url)
        
        try:
            print(f"[Agent {agent_id}] Processing: {domain}")
            
            # Run scraper (this will be cancelled if task is cancelled)
            jobs_response = await main_scrapper(domain=domain, agent_id=agent_id, llm_model="gpt-5-nano")
            all_scraped_jobs = jobs_response.get("job_found", [])
            if not all_scraped_jobs:
                file_manager.add_job(jobs_response)
                print("NO job found on this page")
                return
            
            # Check cancellation again before saving
            task_data = tasks_db.get(task_id)
            if task_data and task_data.get("cancelled", False):
                print(f"[Agent {agent_id}] Task {task_id} cancelled during scraping, stopping...")
                return
            
            # Save jobs
            for job_doc in all_scraped_jobs:
                job_doc.details["task_id"] = task_id
                
                save_info = file_manager.add_job(job_doc.details)
                result["status"] = "success"
                result["save_info"] = save_info
            
            # Mark URL as completed
            task_data = tasks_db.get(task_id)
            if task_data and not task_data.get("cancelled", False):
                task_data["completed_urls"].append(domain)
                tasks_db.set(task_id, task_data)
                
        except asyncio.CancelledError:
            # Task was cancelled, clean exit
            print(f"[Agent {agent_id}] Task {task_id} cancelled while processing {domain}")
            raise  # Re-raise to propagate cancellation
            
        except Exception as e:
            # Check if cancelled during error handling
            task_data = tasks_db.get(task_id)
            if task_data and task_data.get("cancelled", False):
                print(f"[Agent {agent_id}] Task cancelled, not recording error for {domain}")
                return
            
            print(f"[Agent {agent_id}] Error processing {domain}: {str(e)}")
            task_data = tasks_db.get(task_id)
            if task_data:
                task_data["failed_urls"].append(domain)
                tasks_db.set(task_id, task_data)


async def run_scraping_task(task_id: str, urls: List[str], num_agents: int, max_records_per_file: int = 1000):
    """Run the scraping task in background"""
    try:
        file_manager = JobFileManager(
            output_dir="job_outputs",
            max_records_per_file=max_records_per_file,
            file_prefix=f"jobs_{task_id}"
        )
        tasks_db.update(task_id, {"status": TaskStatus.RUNNING})
  
        # Check if cancelled before starting
        task_data = tasks_db.get(task_id)
        if task_data and task_data.get("cancelled", False):
            tasks_db.update(task_id, {
                "status": TaskStatus.CANCELLED,
                "error": "Task was cancelled before starting",
                "completed_at": datetime.now()
            })
            return
        
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
        
        # Create agent tasks and store them for potential cancellation
        agent_tasks = [
            asyncio.create_task(
                process_urls_with_agent(chunk, i + 1, task_id, file_manager),
                name=f"agent_{i+1}_task_{task_id}"
            )
            for i, chunk in enumerate(url_chunks) if chunk
        ]
        
        # Store tasks globally so we can cancel them
        running_agent_tasks[task_id] = agent_tasks
        
        try:
            # Run agents in parallel
            await asyncio.gather(*agent_tasks)
        except asyncio.CancelledError:
            print(f"Task {task_id} was cancelled, cleaning up...")
            # Cancel all agent tasks
            for task in agent_tasks:
                if not task.done():
                    task.cancel()
            # Wait for all to finish cancelling
            await asyncio.gather(*agent_tasks, return_exceptions=True)
            raise
        finally:
            # Clean up task references
            if task_id in running_agent_tasks:
                del running_agent_tasks[task_id]
        
        # Check if task was cancelled during execution
        task_data = tasks_db.get(task_id)
        if task_data and task_data.get("cancelled", False):
            tasks_db.update(task_id, {
                "status": TaskStatus.CANCELLED,
                "error": "Task was cancelled by user",
                "completed_at": datetime.now()
            })
        else:
            # Mark as completed
            tasks_db.update(task_id, {
                "status": TaskStatus.COMPLETED,
                "completed_at": datetime.now()
            })
        
    except asyncio.CancelledError:
        # Task was cancelled
        tasks_db.update(task_id, {
            "status": TaskStatus.CANCELLED,
            "error": "Task was cancelled by user",
            "completed_at": datetime.now()
        })
        
    except Exception as e:
        # Check if it was cancelled
        task_data = tasks_db.get(task_id)
        if task_data and task_data.get("cancelled", False):
            tasks_db.update(task_id, {
                "status": TaskStatus.CANCELLED,
                "error": f"Task cancelled (was processing: {str(e)})",
                "completed_at": datetime.now()
            })
        else:
            tasks_db.update(task_id, {
                "status": TaskStatus.FAILED,
                "error": str(e),
                "completed_at": datetime.now()
            })

# ============================================================================
# SCRAPING ENDPOINTS
# ============================================================================

@app.post("/scrape", response_model=TaskResponse, tags=["Scraping"])
async def start_scraping(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks
):
    """
    Start a background scraping task with a list of URLs.
    
    - **urls**: List of domains to scrape (e.g., ["example.com", "google.com"])
    - **num_agents**: Number of parallel agents (1-5)
    
    **Note:** Only one task can run at a time. Cancel active tasks before starting new ones.
    """
    # Check if any task is already running
    check_can_start_new_task()
    
    task_id = str(uuid4())
    
    # Initialize task tracking
    tasks_db.set(task_id, {
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
        "error": None,
        "cancelled": False,
        "source": "api"
    })
    
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
        message=f"Scraping task started with {request.num_agents} agents for {len(request.urls)} domains"
    )


@app.post("/scrape/from-file", response_model=TaskResponse, tags=["Scraping"])
async def start_scraping_from_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = Depends(validate_spreadsheet_file),
    num_agents: int = Form(default=2, ge=1, le=5, description="Number of parallel agents"),
    domain_column: str = Form(default="domain", description="Name of the column containing domains"),
    task_id: str = Form(default=None, description="Task ID you want to rerun"),
):
    """
    Start a background scraping task from a CSV or Excel file.
    
    - **file**: CSV (.csv) or Excel (.xlsx, .xls) file with domain column
    - **num_agents**: Number of parallel agents (1-5)
    - **domain_column**: Name of the column containing domains (default: "domain")
    
    **Example file format:**
```
    domain          | notes        | priority
    example.com     | Main site    | high
    google.com      | Search       | low
```
    
    **Note:** Only one task can run at a time.
    """
    # Check if any task is already running
    check_can_start_new_task()
    
    try:
        # Read domains from file
        domains = await read_spreadsheet(file, domain_column)
        
        # Create task
        task_id = task_id or  str(uuid4())
        
        # Initialize task tracking
        tasks_db.set(task_id, {
            "task_id": task_id,
            "status": TaskStatus.PENDING,
            "urls": domains,
            "num_agents": num_agents,
            "total_urls": len(domains),
            "completed_urls": [],
            "failed_urls": [],
            "jobs_scraped": 0,
            "created_at": datetime.now(),
            "completed_at": None,
            "error": None,
            "cancelled": False,
            "source": "file_upload",
            "source_filename": file.filename,
            "source_type": Path(file.filename).suffix.lower()
        })
        
        # Add to background tasks
        background_tasks.add_task(
            run_scraping_task,
            task_id,
            domains,
            num_agents
        )
        
        return TaskResponse(
            task_id=task_id,
            status=TaskStatus.PENDING,
            message=f"Scraping task started with {num_agents} agents for {len(domains)} domains from {file.filename}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@app.post("/scrape/validate-file", tags=["Scraping"])
async def validate_file(
    file: UploadFile = Depends(validate_spreadsheet_file),
    domain_column: str = Form(default="domain", description="Expected domain column name")
):
    """
    Validate CSV or Excel file before scraping.
    
    Returns information about the file structure and domain count without starting a scrape.
    """
    try:
        contents = await file.read()
        file_ext = Path(file.filename).suffix.lower()
        
        # Read based on file type
        if file_ext == '.csv':
            try:
                df = pd.read_csv(io.BytesIO(contents))
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(contents), encoding='latin-1')
        elif file_ext in ['.xlsx', '.xls']:
            df = pd.read_excel(io.BytesIO(contents), engine='openpyxl' if file_ext == '.xlsx' else None)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_ext}")
        
        # Get domain column info
        has_domain_column = domain_column in df.columns
        
        if has_domain_column:
            domains = df[domain_column].dropna().astype(str).str.strip().tolist()
            domains = [d for d in domains if d and d.lower() != 'nan']
            valid_domains = len(domains)
        else:
            valid_domains = 0
            domains = []
        
        return {
            "filename": file.filename,
            "file_type": file_ext,
            "total_rows": len(df),
            "columns": df.columns.tolist(),
            "has_domain_column": has_domain_column,
            "domain_column_name": domain_column,
            "valid_domains": valid_domains,
            "sample_domains": domains[:10] if domains else [],
            "estimated_time_minutes": (valid_domains / 10) if valid_domains else 0,
            "recommended_agents": min(5, max(2, valid_domains // 10))
        }
        
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="File is empty")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Error validating file: {str(e)}")

# ============================================================================
# TASK MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/tasks", tags=["Task Management"])
async def list_tasks():
    """
    List all scraping tasks (past and present).
    
    Returns all tasks with their current status, progress, and metadata.
    """
    all_tasks = tasks_db.all()
    return {
        "total_tasks": len(all_tasks),
        "tasks": list(all_tasks.values())
    }


@app.get("/tasks/active", tags=["Task Management"])
async def get_active_tasks_endpoint():
    """
    Get all currently running or pending tasks.
    
    Use this to check if a task is already running before starting a new one.
    """
    active_tasks = get_active_tasks()
    
    return {
        "active_count": len(active_tasks),
        "can_start_new_task": len(active_tasks) == 0,
        "tasks": active_tasks
    }


@app.get("/tasks/{task_id}", response_model=TaskDetail, tags=["Task Management"])
async def get_task_status(task_id: str):
    """
    Get detailed status of a specific scraping task.
    
    - **task_id**: Task ID returned from scrape endpoints
    
    Returns complete task information including progress, errors, and timing.
    """
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_data = tasks_db.get(task_id)
    
    if task_data is None:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskDetail(**task_data)


@app.post("/tasks/{task_id}/cancel", tags=["Task Management"])
async def cancel_task(task_id: str):
    """
    Cancel a running or pending scraping task IMMEDIATELY.
    
    - **task_id**: Task ID to cancel
    
    The task will stop immediately, even if currently processing a URL.
    All running agents will be terminated.
    """
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_data = tasks_db.get(task_id)
    
    if task_data is None:
        raise HTTPException(status_code=404, detail="Task not found")
    
    current_status = task_data.get("status")
    
    # Can only cancel pending or running tasks
    if current_status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel task with status '{current_status}'. Only PENDING or RUNNING tasks can be cancelled."
        )
    
    # Mark task as cancelled
    tasks_db.update(task_id, {"cancelled": True, "status": TaskStatus.CANCELLED})
    
    # If task is running, cancel all agent tasks immediately
    cancelled_agents = 0
    if task_id in running_agent_tasks:
        agent_tasks = running_agent_tasks[task_id]
        for agent_task in agent_tasks:
            if not agent_task.done():
                agent_task.cancel()
                cancelled_agents += 1
        print(f"Cancelled {cancelled_agents} running agent(s) for task {task_id}")
    
    return {
        "task_id": task_id,
        "message": "Task cancelled immediately. All running agents have been stopped.",
        "status": current_status,
        "completed": len(task_data.get("completed_urls", [])),
        "total": task_data.get("total_urls", 0),
        "agents_cancelled": cancelled_agents
    }


@app.post("/tasks/cancel-all", tags=["Task Management"])
async def cancel_all_tasks():
    """
    Cancel all running and pending tasks IMMEDIATELY.
    
    **Emergency endpoint** - Use to stop all active scraping operations.
    All running agents will be terminated.
    """
    active_tasks = get_active_tasks()
    
    if not active_tasks:
        return {
            "message": "No active tasks to cancel",
            "cancelled_count": 0
        }
    
    cancelled_ids = []
    total_agents_cancelled = 0
    
    for task in active_tasks:
        task_id = task["task_id"]
        # Set cancelled flag
        tasks_db.update(task_id, {"cancelled": True, "status": TaskStatus.CANCELLED})
        
        # Cancel running agent tasks immediately
        if task_id in running_agent_tasks:
            agent_tasks = running_agent_tasks[task_id]
            for agent_task in agent_tasks:
                if not agent_task.done():
                    agent_task.cancel()
                    total_agents_cancelled += 1
        
        cancelled_ids.append(task_id)
    
    return {
        "message": f"All active tasks cancelled immediately",
        "cancelled_count": len(cancelled_ids),
        "cancelled_task_ids": cancelled_ids,
        "total_agents_cancelled": total_agents_cancelled
    }

# ============================================================================
# EXPORT ENDPOINTS
# ============================================================================

@app.get("/export/csv", tags=["Export"])
async def export_jobs_to_csv(task_id: str | None = None):
    """
    Export all scraped jobs to CSV format and download.
    
    Returns a CSV file containing all job records from all scraping sessions.
    Includes flattened data with all job fields (location, salary, requirements, etc.).
    """
    try:
        # Read all jobs from files
        all_jobs = read_all_jobs_from_files(output_dir="job_outputs", task_id=task_id)
        
        # Generate CSV
        csv_content = generate_csv_from_jobs(all_jobs)
        
        # Create streaming response
        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=jobs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error exporting CSV: {str(e)}")


@app.get("/export/stats", tags=["Export"])
async def get_export_stats():
    """
    Get statistics about available jobs for export.
    
    Returns:
    - Total job count
    - Jobs with salary information
    - Jobs with location information
    - Company distribution
    - Top companies by job count
    """
    try:
        all_jobs = read_all_jobs_from_files(output_dir="job_outputs")
        
        # Calculate statistics
        total_jobs = len(all_jobs)
        jobs_with_salary = sum(1 for job in all_jobs if job.get("salary", {}).get("min"))
        jobs_with_location = sum(1 for job in all_jobs if job.get("location", {}).get("city"))
        
        # Group by company
        companies = {}
        for job in all_jobs:
            company = job.get("company_name", "Unknown")
            companies[company] = companies.get(company, 0) + 1
        
        return {
            "total_jobs": total_jobs,
            "jobs_with_salary": jobs_with_salary,
            "jobs_with_location": jobs_with_location,
            "unique_companies": len(companies),
            "top_companies": sorted(companies.items(), key=lambda x: x[1], reverse=True)[:10]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting stats: {str(e)}")

# ============================================================================
# INFO ENDPOINTS
# ============================================================================

@app.get("/", tags=["Info"])
async def root():
    """
    API information and available endpoints.
    
    Displays all available endpoints with descriptions.
    """
    return {
        "name": "Job Scraper API",
        "version": "2.0",
        "description": "Automated job scraping with parallel agents, CSV export, and task management",
        "endpoints": {
            "scraping": {
                "POST /scrape": "Start scraping with URL list",
                "POST /scrape/from-file": "Start scraping from CSV/Excel file",
                "POST /scrape/validate-file": "Validate file before scraping"
            },
            "task_management": {
                "GET /tasks": "List all tasks",
                "GET /tasks/active": "Get active tasks",
                "GET /tasks/{task_id}": "Get specific task status",
                "POST /tasks/{task_id}/cancel": "Cancel specific task",
                "POST /tasks/cancel-all": "Cancel all active tasks"
            },
            "export": {
                "GET /export/csv": "Download all jobs as CSV",
                "GET /export/stats": "Get export statistics"
            }
        },
        "features": [
            "Parallel scraping with configurable agents (1-5)",
            "CSV and Excel file upload support",
            "Real-time task tracking and cancellation",
            "Automatic file rotation for large datasets",
            "Complete job data export to CSV",
            "Single-task enforcement for resource management"
        ]
    }


@app.get("/health", tags=["Info"])
async def health_check():
    """
    Health check endpoint.
    
    Returns API status and basic system information.
    """
    active_tasks = get_active_tasks()
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_tasks": len(active_tasks),
        "total_tasks": len(tasks_db.all()),
        "can_accept_new_tasks": len(active_tasks) == 0
    }

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info"
    )