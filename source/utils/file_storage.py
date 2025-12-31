import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Optional


class JobFileManager:
    """Manages job data storage to JSON files with automatic file rotation."""
    
    def __init__(
        self,
        output_dir: str = "job_outputs",
        max_records_per_file: int = 1000,
        file_prefix: str = "jobs"
    ):
        self.output_dir = Path(output_dir)
        self.max_records = max_records_per_file
        self.file_prefix = file_prefix
        self.current_file_index = 0
        self.current_records: list[dict] = []
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Find existing files and continue from last one
        self._initialize_from_existing()
    
    def _initialize_from_existing(self):
        """Check for existing files and load the latest one if not full."""
        existing_files = sorted(self.output_dir.glob(f"{self.file_prefix}_*.json"))
        
        if existing_files:
            # Get the highest index
            last_file = existing_files[-1]
            try:
                # Extract index from filename (e.g., jobs_003.json -> 3)
                index_str = last_file.stem.split("_")[-1]
                self.current_file_index = int(index_str)
                
                # Load existing records from last file
                with open(last_file, "r", encoding="utf-8") as f:
                    self.current_records = json.load(f)
                
                # If last file is full, prepare for new file
                if len(self.current_records) >= self.max_records:
                    self.current_file_index += 1
                    self.current_records = []
                    
                print(f"Resuming from {last_file.name} with {len(self.current_records)} records")
                
            except (ValueError, json.JSONDecodeError) as e:
                print(f"Error reading existing file, starting fresh: {e}")
                self.current_file_index = 0
                self.current_records = []
    
    def _get_current_filepath(self) -> Path:
        """Generate the current file path with zero-padded index."""
        filename = f"{self.file_prefix}_{self.current_file_index:03d}.json"
        return self.output_dir / filename
    
    def _save_current_file(self):
        """Save current records to the JSON file."""
        filepath = self._get_current_filepath()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.current_records, f, indent=2, ensure_ascii=False, default=str)
        print(f"Saved {len(self.current_records)} records to {filepath.name}")
    
    def _rotate_file(self):
        """Create a new file when current one reaches the limit."""
        self.current_file_index += 1
        self.current_records = []
        print(f"Rotating to new file: {self._get_current_filepath().name}")
    
    def add_job(self, job_data: dict) -> dict:
        """
        Add a job record to the current file.
        Automatically rotates to a new file when limit is reached.
        
        Returns:
            dict with file info and record count
        """
        # Add metadata
        job_data["_saved_at"] = datetime.now().isoformat()
        job_data["_file_index"] = self.current_file_index
        
        # Add to current records
        self.current_records.append(job_data)
        
        # Save to file
        self._save_current_file()
        
        result = {
            "file": str(self._get_current_filepath()),
            "record_count": len(self.current_records),
            "file_index": self.current_file_index
        }
        
        # Check if we need to rotate
        if len(self.current_records) >= self.max_records:
            self._rotate_file()
        
        return result
    
    def add_jobs_batch(self, jobs: list[dict]) -> dict:
        """Add multiple jobs at once."""
        added = 0
        for job in jobs:
            self.add_job(job)
            added += 1
        
        return {
            "added": added,
            "current_file": str(self._get_current_filepath()),
            "current_count": len(self.current_records)
        }
    
    def get_stats(self) -> dict:
        """Get current storage statistics."""
        all_files = list(self.output_dir.glob(f"{self.file_prefix}_*.json"))
        total_records = 0
        
        for f in all_files:
            try:
                with open(f, "r") as file:
                    data = json.load(file)
                    total_records += len(data)
            except:
                pass
        
        return {
            "total_files": len(all_files),
            "total_records": total_records,
            "current_file": str(self._get_current_filepath()),
            "current_file_records": len(self.current_records),
            "max_records_per_file": self.max_records
        }


# Global instance (or create per-run)
job_file_manager: Optional[JobFileManager] = None


def save_job_to_file(job_data: dict, manager: Optional[JobFileManager] = None) -> dict:
    """
    Convenience function to save a job to file.
    
    Args:
        job_data: The job document to save
        manager: Optional JobFileManager instance (uses global if not provided)
    
    Returns:
        dict with save info
    """
    global job_file_manager
    
    if manager:
        return manager.add_job(job_data)
    
    if job_file_manager is None:
        job_file_manager = JobFileManager()
    
    return job_file_manager.add_job(job_data)





class TaskStorage:
    """Manages task persistence to avoid data loss"""
    
    def __init__(self, file_path: str = "tasks_db.json"):
        self.file_path = Path(file_path)
        self.tasks = self._load()
    
    def _load(self) -> dict:
        """Load tasks from file"""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print("Error loading tasks file, starting fresh")
                return {}
        return {}
    
    def _save(self):
        """Save tasks to file"""
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.tasks, f, indent=2, ensure_ascii=False, default=str)
    
    def _deserialize_dates(self, data: dict) -> dict:
        """Convert ISO date strings back to datetime objects"""
        if not data:
            return data
        
        date_fields = ["created_at", "completed_at"]
        for field in date_fields:
            if field in data and isinstance(data[field], str):
                try:
                    data[field] = datetime.fromisoformat(data[field])
                except (ValueError, TypeError):
                    pass
        return data
    
    def get(self, task_id: str) -> Optional[dict]:
        """Get a task by ID with datetime conversion"""
        data = self.tasks.get(task_id)
        return self._deserialize_dates(data) if data else None
    
    def set(self, task_id: str, data: dict):
        """Set/update a task"""
        self.tasks[task_id] = data
        self._save()
    
    def update(self, task_id: str, updates: dict):
        """Update specific fields of a task"""
        if task_id in self.tasks:
            self.tasks[task_id].update(updates)
            self._save()
    
    def all(self) -> dict:
        """Get all tasks with datetime conversion"""
        return {
            task_id: self._deserialize_dates(data.copy()) 
            for task_id, data in self.tasks.items()
        }
    
    def __contains__(self, task_id: str) -> bool:
        """Check if task exists"""
        return task_id in self.tasks