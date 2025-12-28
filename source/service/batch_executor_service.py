"""
Batch Executor for Running Scraping Tasks with Multiple Workers
Handles parallel execution and worker management
"""

import asyncio
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
import traceback

from service.task_manager_service import TaskManager, TaskStatus, get_task_manager
from service.resource_manager_service import resource_manager
from utils.logging import setup_logger

logger = setup_logger(__name__)


class BatchExecutor:
    """
    Executes batch scraping tasks using multiple workers.
    
    Features:
    - Parallel task execution
    - Progress tracking
    - Graceful cancellation
    - Error handling per task
    """
    
    def __init__(
        self,
        task_manager: TaskManager,
        scrape_function: Callable,
        max_records_per_file: int = 50
    ):
        """
        Initialize batch executor.
        
        Args:
            task_manager: TaskManager instance
            scrape_function: Async function to scrape a single URL
            max_records_per_file: Max records per output file
        """
        self.task_manager = task_manager
        self.scrape_function = scrape_function
        self.max_records_per_file = max_records_per_file
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()
    
    async def worker(
        self,
        worker_id: int,
        batch_id: str,
        results_queue: asyncio.Queue
    ):
        """
        Worker coroutine that processes tasks.
        
        Args:
            worker_id: Worker identifier
            batch_id: Batch being processed
            results_queue: Queue to put results
        """
        logger.info(f"Worker {worker_id} started for batch {batch_id}")
        
        while self._running:
            # Check for cancellation
            if self.task_manager.is_cancellation_requested():
                logger.info(f"Worker {worker_id} stopping due to cancellation")
                break
            
            # Get next task
            tasks = await self.task_manager.get_next_pending_tasks(batch_id, count=1)
            
            if not tasks:
                logger.debug(f"Worker {worker_id} found no more tasks")
                break
            
            task = tasks[0]
            task_id = task["task_id"]
            url = task["url"]
            
            logger.info(f"Worker {worker_id} processing: {url}")
            
            try:
                # Update task with worker ID
                self.task_manager.tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {"worker_id": worker_id}}
                )
                
                # Run scraping function
                start_time = datetime.now()
                result = await self.scrape_function(url)
                duration = (datetime.now() - start_time).total_seconds()
                
                # Process result
                jobs_found = 0
                if isinstance(result, list):
                    jobs_found = len(result)
                elif isinstance(result, dict) and "jobs" in result:
                    jobs_found = len(result["jobs"])
                
                # Complete task
                await self.task_manager.complete_task(
                    task_id=task_id,
                    jobs_found=jobs_found,
                    result={
                        "duration_seconds": duration,
                        "jobs_count": jobs_found
                    }
                )
                
                # Queue result for storage
                await results_queue.put({
                    "task_id": task_id,
                    "url": url,
                    "jobs": result if isinstance(result, list) else [],
                    "jobs_found": jobs_found,
                    "success": True
                })
                
                logger.info(f"Worker {worker_id} completed {url}: {jobs_found} jobs found")
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Worker {worker_id} error on {url}: {error_msg}")
                logger.debug(traceback.format_exc())
                
                await self.task_manager.complete_task(
                    task_id=task_id,
                    jobs_found=0,
                    error=error_msg
                )
                
                await results_queue.put({
                    "task_id": task_id,
                    "url": url,
                    "jobs": [],
                    "jobs_found": 0,
                    "success": False,
                    "error": error_msg
                })
            
            # Small delay between tasks
            await asyncio.sleep(0.5)
        
        logger.info(f"Worker {worker_id} finished")
    
    async def result_processor(
        self,
        batch_id: str,
        results_queue: asyncio.Queue,
        mongo_service
    ):
        """
        Process results from workers and save to database.
        
        Args:
            batch_id: Batch ID
            results_queue: Queue with results
            mongo_service: MongoDB service for saving jobs
        """
        logger.info(f"Result processor started for batch {batch_id}")
        
        total_saved = 0
        
        while self._running or not results_queue.empty():
            try:
                # Wait for result with timeout
                try:
                    result = await asyncio.wait_for(
                        results_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if result["success"] and result["jobs"]:
                    # Save jobs to MongoDB
                    for job_doc in result["jobs"]:
                        if job_doc:
                            try:
                                mongo_service.add_job(job_doc, update_if_exists=True)
                                total_saved += 1
                            except Exception as e:
                                logger.error(f"Error saving job: {e}")
                
                results_queue.task_done()
                
            except Exception as e:
                logger.error(f"Result processor error: {e}")
        
        logger.info(f"Result processor finished. Total jobs saved: {total_saved}")
    
    async def execute_batch(
        self,
        batch_id: str,
        num_workers: int,
        mongo_service
    ) -> Dict[str, Any]:
        """
        Execute a batch with multiple workers.
        
        Args:
            batch_id: Batch to execute
            num_workers: Number of workers to use
            mongo_service: MongoDB service for saving jobs
            
        Returns:
            Execution results
        """
        async with self._lock:
            if self._running:
                raise RuntimeError("Executor is already running")
            self._running = True
        
        results_queue = asyncio.Queue()
        execution_result = {
            "batch_id": batch_id,
            "workers_used": num_workers,
            "started_at": datetime.now(),
            "completed_at": None,
            "success": False,
            "error": None
        }
        
        try:
            # Start batch
            await self.task_manager.start_batch(batch_id)
            await self.task_manager.update_workers_active(batch_id, num_workers)
            
            logger.info(f"Starting batch {batch_id} with {num_workers} workers")
            
            # Create workers
            self._workers = [
                asyncio.create_task(
                    self.worker(i, batch_id, results_queue)
                )
                for i in range(num_workers)
            ]
            
            # Create result processor
            processor_task = asyncio.create_task(
                self.result_processor(batch_id, results_queue, mongo_service)
            )
            
            # Wait for all workers to complete
            await asyncio.gather(*self._workers, return_exceptions=True)
            
            # Signal processor to finish and wait
            self._running = False
            await results_queue.join()
            processor_task.cancel()
            
            try:
                await processor_task
            except asyncio.CancelledError:
                pass
            
            # Complete batch
            await self.task_manager.complete_batch(batch_id)
            
            execution_result["completed_at"] = datetime.now()
            execution_result["success"] = True
            
            logger.info(f"Batch {batch_id} execution completed successfully")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Batch execution error: {error_msg}")
            logger.debug(traceback.format_exc())
            
            execution_result["error"] = error_msg
            await self.task_manager.complete_batch(batch_id, error=error_msg)
            
        finally:
            async with self._lock:
                self._running = False
                self._workers = []
            
            # Release workers from resource manager
            await resource_manager.release_workers()
        
        return execution_result
    
    async def stop(self):
        """Stop all workers"""
        logger.info("Stopping batch executor")
        
        async with self._lock:
            self._running = False
        
        # Cancel all worker tasks
        for worker in self._workers:
            if not worker.done():
                worker.cancel()
        
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        
        self._workers = []
        logger.info("Batch executor stopped")
    
    @property
    def is_running(self) -> bool:
        """Check if executor is running"""
        return self._running


# Global executor instance
_executor: Optional[BatchExecutor] = None


async def run_batch_in_background(
    urls: List[str],
    num_workers: int,
    scrape_function: Callable,
    mongo_service,
    max_records_per_file: int = 50
) -> str:
    """
    Run a batch in the background.
    
    Args:
        urls: URLs to scrape
        num_workers: Number of workers
        scrape_function: Scraping function
        mongo_service: MongoDB service
        max_records_per_file: Max records per file
        
    Returns:
        Batch ID
    """
    global _executor
    
    task_manager = get_task_manager()
    
    # Create batch
    batch = await task_manager.create_batch(
        urls=urls,
        workers=num_workers,
        max_records_per_file=max_records_per_file
    )
    
    batch_id = batch["batch_id"]
    
    # Create executor
    _executor = BatchExecutor(
        task_manager=task_manager,
        scrape_function=scrape_function,
        max_records_per_file=max_records_per_file
    )
    
    # Run in background
    asyncio.create_task(
        _executor.execute_batch(batch_id, num_workers, mongo_service)
    )
    
    return batch_id


async def stop_current_batch() -> int:
    """Stop current batch execution"""
    global _executor
    
    task_manager = get_task_manager()
    cancelled = await task_manager.request_cancellation()
    
    if _executor:
        await _executor.stop()
        _executor = None
    
    return cancelled


def get_executor() -> Optional[BatchExecutor]:
    """Get current executor"""
    return _executor