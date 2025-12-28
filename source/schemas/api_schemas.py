"""
Pydantic models for API request/response schemas
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class BatchScrapeRequest(BaseModel):
    """Request model for batch scraping"""
    urls: List[str] = Field(..., min_length=1, description="List of URLs or domains to scrape")
    max_workers: Optional[int] = Field(None, ge=1, le=10, description="Max workers (auto-detected if not set)")
    max_records_per_file: int = Field(50, ge=1, le=500, description="Max records per output file")
    priority: int = Field(1, ge=1, le=5, description="Priority level (1=lowest, 5=highest)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "urls": ["example.com", "another-site.org"],
                "max_workers": 2,
                "max_records_per_file": 50,
                "priority": 3
            }
        }


class SingleScrapeRequest(BaseModel):
    """Request model for single URL scraping"""
    url: str = Field(..., description="Single URL or domain to scrape")
    
    class Config:
        json_schema_extra = {
            "example": {
                "url": "example.com"
            }
        }


class TaskInfo(BaseModel):
    """Information about a single task"""
    task_id: str
    url: str
    status: TaskStatus
    worker_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    jobs_found: int = 0
    error: Optional[str] = None
    progress_percent: float = 0.0


class BatchInfo(BaseModel):
    """Information about a batch of tasks"""
    batch_id: str
    total_urls: int
    completed_urls: int
    failed_urls: int
    pending_urls: int
    running_urls: int
    status: TaskStatus
    workers_active: int
    started_at: datetime
    estimated_completion: Optional[datetime] = None
    total_jobs_found: int = 0


class ResourceInfo(BaseModel):
    """Server resource information"""
    cpu_percent: float
    memory_percent: float
    memory_available_gb: float
    memory_total_gb: float
    recommended_workers: int
    max_workers: int
    current_workers: int
    is_busy: bool


class BatchScrapeResponse(BaseModel):
    """Response for batch scrape request"""
    success: bool
    message: str
    batch_id: Optional[str] = None
    batch_info: Optional[BatchInfo] = None
    resource_info: Optional[ResourceInfo] = None


class SingleScrapeResponse(BaseModel):
    """Response for single URL scrape"""
    success: bool
    message: str
    url: str
    task_id: Optional[str] = None
    jobs_found: int = 0
    jobs: List[Dict[str, Any]] = []
    error: Optional[str] = None
    duration_seconds: float = 0.0


class ProgressResponse(BaseModel):
    """Response for progress check"""
    is_running: bool
    batch_info: Optional[BatchInfo] = None
    tasks: List[TaskInfo] = []
    resource_info: Optional[ResourceInfo] = None


class StopRequest(BaseModel):
    """Request to stop scraping"""
    batch_id: Optional[str] = Field(None, description="Specific batch ID to stop (stops all if not provided)")
    force: bool = Field(False, description="Force stop immediately")


class StopResponse(BaseModel):
    """Response for stop request"""
    success: bool
    message: str
    stopped_tasks: int = 0


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    mongodb_connected: bool
    resource_info: ResourceInfo
    uptime_seconds: float


class JobsListResponse(BaseModel):
    """Response for listing scraped jobs"""
    success: bool
    total_count: int
    jobs: List[Dict[str, Any]]
    page: int
    page_size: int
    total_pages: int