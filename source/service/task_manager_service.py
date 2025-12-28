"""
Task Manager for MongoDB-based Task and Batch Tracking
Handles batch creation, task status updates, and progress monitoring
"""

import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
import uuid

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure
from bson import ObjectId

from core.config import settings
from utils.logging import setup_logger

logger = setup_logger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_COMPLETED = "partially_completed"


class TaskManager:
    """
    Manages scraping tasks and batches using MongoDB.
    
    Responsibilities:
    - Create and track batches
    - Create and update tasks
    - Monitor progress
    - Handle cancellation
    """
    
    def __init__(
        self,
        mongo_uri: str = None,
        database_name: str = None
    ):
        self.mongo_uri = mongo_uri or settings.MONGO_URI
        self.database_name = database_name or settings.DATABASE_NAME
        
        try:
            self.client = MongoClient(str(self.mongo_uri))
            self.client.admin.command('ping')
            logger.info(f"TaskManager connected to MongoDB")
        except ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        self.db = self.client[self.database_name]
        self.batches = self.db["scrape_batches"]
        self.tasks = self.db["scrape_tasks"]
        
        self._create_indexes()
        
        # In-memory tracking for active batch
        self._active_batch_id: Optional[str] = None
        self._cancel_requested = False
        self._lock = asyncio.Lock()
    
    def _create_indexes(self):
        """Create indexes for efficient queries"""
        try:
            self.batches.create_index([("created_at", DESCENDING)])
            self.batches.create_index([("status", ASCENDING)])
            
            self.tasks.create_index([("batch_id", ASCENDING)])
            self.tasks.create_index([("status", ASCENDING)])
            self.tasks.create_index([("url", ASCENDING)])
            self.tasks.create_index([("batch_id", ASCENDING), ("status", ASCENDING)])
            
            logger.info("Task manager indexes created")
        except Exception as e:
            logger.warning(f"Error creating indexes: {e}")
    
    def generate_batch_id(self) -> str:
        """Generate unique batch ID"""
        return f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    
    def generate_task_id(self) -> str:
        """Generate unique task ID"""
        return f"task_{uuid.uuid4().hex[:12]}"
    
    async def create_batch(
        self,
        urls: List[str],
        workers: int,
        max_records_per_file: int = 50,
        priority: int = 1
    ) -> Dict[str, Any]:
        """
        Create a new batch with tasks.
        
        Args:
            urls: List of URLs to scrape
            workers: Number of workers allocated
            max_records_per_file: Max records per output file
            priority: Batch priority
            
        Returns:
            Batch document
        """
        async with self._lock:
            if self._active_batch_id:
                raise RuntimeError(f"Batch {self._active_batch_id} is already running")
            
            batch_id = self.generate_batch_id()
            self._active_batch_id = batch_id
            self._cancel_requested = False
        
        batch_doc = {
            "batch_id": batch_id,
            "status": BatchStatus.PENDING.value,
            "total_urls": len(urls),
            "completed_urls": 0,
            "failed_urls": 0,
            "pending_urls": len(urls),
            "running_urls": 0,
            "workers_allocated": workers,
            "workers_active": 0,
            "max_records_per_file": max_records_per_file,
            "priority": priority,
            "total_jobs_found": 0,
            "created_at": datetime.now(),
            "started_at": None,
            "completed_at": None,
            "error": None
        }
        
        self.batches.insert_one(batch_doc)
        
        # Create individual tasks
        task_docs = []
        for i, url in enumerate(urls):
            task_doc = {
                "task_id": self.generate_task_id(),
                "batch_id": batch_id,
                "url": url,
                "status": TaskStatus.PENDING.value,
                "worker_id": None,
                "order": i,
                "jobs_found": 0,
                "progress_percent": 0.0,
                "created_at": datetime.now(),
                "started_at": None,
                "completed_at": None,
                "error": None,
                "result": None
            }
            task_docs.append(task_doc)
        
        if task_docs:
            self.tasks.insert_many(task_docs)
        
        logger.info(f"Created batch {batch_id} with {len(urls)} tasks")
        
        return batch_doc
    
    async def start_batch(self, batch_id: str) -> bool:
        """Mark batch as started"""
        result = self.batches.update_one(
            {"batch_id": batch_id},
            {
                "$set": {
                    "status": BatchStatus.RUNNING.value,
                    "started_at": datetime.now()
                }
            }
        )
        return result.modified_count > 0
    
    async def get_next_pending_tasks(
        self,
        batch_id: str,
        count: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Get next pending tasks and mark them as running.
        
        Args:
            batch_id: Batch ID
            count: Number of tasks to get
            
        Returns:
            List of task documents
        """
        tasks = []
        
        for _ in range(count):
            # Find and update atomically
            task = self.tasks.find_one_and_update(
                {
                    "batch_id": batch_id,
                    "status": TaskStatus.PENDING.value
                },
                {
                    "$set": {
                        "status": TaskStatus.RUNNING.value,
                        "started_at": datetime.now()
                    }
                },
                sort=[("order", ASCENDING)],
                return_document=True
            )
            
            if task:
                task["_id"] = str(task["_id"])
                tasks.append(task)
            else:
                break
        
        # Update batch running count
        if tasks:
            self.batches.update_one(
                {"batch_id": batch_id},
                {
                    "$inc": {
                        "pending_urls": -len(tasks),
                        "running_urls": len(tasks)
                    }
                }
            )
        
        return tasks
    
    async def complete_task(
        self,
        task_id: str,
        jobs_found: int = 0,
        result: Any = None,
        error: str = None
    ):
        """
        Mark task as completed or failed.
        
        Args:
            task_id: Task ID
            jobs_found: Number of jobs found
            result: Task result data
            error: Error message if failed
        """
        status = TaskStatus.FAILED.value if error else TaskStatus.COMPLETED.value
        
        task = self.tasks.find_one_and_update(
            {"task_id": task_id},
            {
                "$set": {
                    "status": status,
                    "completed_at": datetime.now(),
                    "jobs_found": jobs_found,
                    "progress_percent": 100.0,
                    "result": result,
                    "error": error
                }
            },
            return_document=True
        )
        
        if task:
            batch_id = task["batch_id"]
            update_fields = {"running_urls": -1}
            
            if error:
                update_fields["failed_urls"] = 1
            else:
                update_fields["completed_urls"] = 1
                update_fields["total_jobs_found"] = jobs_found
            
            self.batches.update_one(
                {"batch_id": batch_id},
                {"$inc": update_fields}
            )
    
    async def update_task_progress(
        self,
        task_id: str,
        progress_percent: float,
        jobs_found: int = None
    ):
        """Update task progress"""
        update = {"progress_percent": progress_percent}
        if jobs_found is not None:
            update["jobs_found"] = jobs_found
        
        self.tasks.update_one(
            {"task_id": task_id},
            {"$set": update}
        )
    
    async def complete_batch(self, batch_id: str, error: str = None):
        """Mark batch as completed"""
        batch = self.batches.find_one({"batch_id": batch_id})
        
        if batch:
            if error:
                status = BatchStatus.FAILED.value
            elif batch["failed_urls"] > 0 and batch["completed_urls"] > 0:
                status = BatchStatus.PARTIALLY_COMPLETED.value
            elif batch["failed_urls"] == batch["total_urls"]:
                status = BatchStatus.FAILED.value
            else:
                status = BatchStatus.COMPLETED.value
            
            self.batches.update_one(
                {"batch_id": batch_id},
                {
                    "$set": {
                        "status": status,
                        "completed_at": datetime.now(),
                        "workers_active": 0,
                        "error": error
                    }
                }
            )
        
        async with self._lock:
            if self._active_batch_id == batch_id:
                self._active_batch_id = None
                self._cancel_requested = False
        
        logger.info(f"Batch {batch_id} completed with status: {status}")
    
    async def request_cancellation(self, batch_id: str = None) -> int:
        """
        Request cancellation of batch.
        
        Args:
            batch_id: Specific batch to cancel (cancels active if None)
            
        Returns:
            Number of tasks cancelled
        """
        target_batch = batch_id or self._active_batch_id
        
        if not target_batch:
            return 0
        
        async with self._lock:
            self._cancel_requested = True
        
        # Cancel pending tasks
        result = self.tasks.update_many(
            {
                "batch_id": target_batch,
                "status": TaskStatus.PENDING.value
            },
            {
                "$set": {
                    "status": TaskStatus.CANCELLED.value,
                    "completed_at": datetime.now()
                }
            }
        )
        
        cancelled_count = result.modified_count
        
        # Update batch
        batch = self.batches.find_one({"batch_id": target_batch})
        if batch:
            self.batches.update_one(
                {"batch_id": target_batch},
                {
                    "$set": {
                        "status": BatchStatus.CANCELLED.value,
                        "pending_urls": 0
                    },
                    "$inc": {
                        "completed_urls": cancelled_count
                    }
                }
            )
        
        logger.info(f"Cancelled {cancelled_count} pending tasks in batch {target_batch}")
        
        return cancelled_count
    
    def is_cancellation_requested(self) -> bool:
        """Check if cancellation was requested"""
        return self._cancel_requested
    
    def get_active_batch_id(self) -> Optional[str]:
        """Get currently active batch ID"""
        return self._active_batch_id
    
    async def get_batch_info(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Get batch information"""
        batch = self.batches.find_one({"batch_id": batch_id})
        if batch:
            batch["_id"] = str(batch["_id"])
        return batch
    
    async def get_active_batch_info(self) -> Optional[Dict[str, Any]]:
        """Get active batch information"""
        if self._active_batch_id:
            return await self.get_batch_info(self._active_batch_id)
        return None
    
    async def get_batch_tasks(
        self,
        batch_id: str,
        status: str = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get tasks for a batch"""
        query = {"batch_id": batch_id}
        if status:
            query["status"] = status
        
        tasks = list(
            self.tasks.find(query)
            .sort("order", ASCENDING)
            .limit(limit)
        )
        
        for task in tasks:
            task["_id"] = str(task["_id"])
        
        return tasks
    
    async def get_progress(self) -> Dict[str, Any]:
        """Get current progress information"""
        batch = await self.get_active_batch_info()
        
        if not batch:
            return {
                "is_running": False,
                "batch_info": None,
                "tasks": []
            }
        
        tasks = await self.get_batch_tasks(batch["batch_id"], limit=50)
        
        return {
            "is_running": batch["status"] == BatchStatus.RUNNING.value,
            "batch_info": batch,
            "tasks": tasks
        }
    
    async def update_workers_active(self, batch_id: str, count: int):
        """Update active worker count"""
        self.batches.update_one(
            {"batch_id": batch_id},
            {"$set": {"workers_active": count}}
        )
    
    async def get_recent_batches(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent batches"""
        batches = list(
            self.batches.find()
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        
        for batch in batches:
            batch["_id"] = str(batch["_id"])
        
        return batches
    
    async def cleanup_stale_batches(self, max_age_hours: int = 24):
        """Clean up old incomplete batches"""
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        # Find stale running batches
        stale_batches = self.batches.find({
            "status": {"$in": [BatchStatus.RUNNING.value, BatchStatus.PENDING.value]},
            "created_at": {"$lt": cutoff}
        })
        
        for batch in stale_batches:
            await self.complete_batch(
                batch["batch_id"],
                error="Batch timed out and was cleaned up"
            )
    
    def close(self):
        """Close MongoDB connection"""
        self.client.close()
        logger.info("TaskManager connection closed")


# Global task manager instance (initialized in app startup)
task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Get task manager instance"""
    global task_manager
    if task_manager is None:
        task_manager = TaskManager()
    return task_manager


def init_task_manager():
    """Initialize task manager"""
    global task_manager
    task_manager = TaskManager()
    return task_manager