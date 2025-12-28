"""
Resource Manager for Server Resource Monitoring and Worker Allocation
Handles CPU, memory monitoring and determines optimal worker count
"""

import psutil
import os
from typing import Dict, Any, Tuple
from dataclasses import dataclass
import asyncio
from datetime import datetime


@dataclass
class ResourceThresholds:
    """Thresholds for resource management"""
    max_cpu_percent: float = 80.0  # Don't allocate new workers above this
    max_memory_percent: float = 85.0  # Don't allocate new workers above this
    min_memory_per_worker_gb: float = 0.5  # Minimum memory per worker
    base_workers: int = 1  # Minimum workers
    max_workers: int = 8  # Maximum workers regardless of resources
    cpu_per_worker: float = 15.0  # Estimated CPU % per worker


class ResourceManager:
    """
    Manages server resources and worker allocation.
    
    Monitors CPU and memory usage to determine how many
    concurrent scraping workers can be safely spawned.
    """
    
    def __init__(self, thresholds: ResourceThresholds | None = None):
        self.thresholds = thresholds or ResourceThresholds()
        self._current_workers = 0
        self._lock = asyncio.Lock()
        self._start_time = datetime.now()
    
    def get_cpu_usage(self) -> float:
        """Get current CPU usage percentage"""
        return psutil.cpu_percent(interval=0.1)
    
    def get_memory_info(self) -> Dict[str, float]:
        """Get memory usage information"""
        mem = psutil.virtual_memory()
        return {
            "total_gb": mem.total / (1024 ** 3),
            "available_gb": mem.available / (1024 ** 3),
            "used_gb": mem.used / (1024 ** 3),
            "percent": mem.percent
        }
    
    def get_resource_snapshot(self) -> Dict[str, Any]:
        """Get current resource snapshot"""
        cpu = self.get_cpu_usage()
        mem = self.get_memory_info()
        
        return {
            "cpu_percent": cpu,
            "memory_percent": mem["percent"],
            "memory_available_gb": round(mem["available_gb"], 2),
            "memory_total_gb": round(mem["total_gb"], 2),
            "memory_used_gb": round(mem["used_gb"], 2),
            "current_workers": self._current_workers,
            "timestamp": datetime.now().isoformat()
        }
    
    def calculate_recommended_workers(self) -> Tuple[int, str]:
        """
        Calculate recommended number of workers based on available resources.
        
        Returns:
            Tuple of (recommended_workers, reason)
        """
        cpu = self.get_cpu_usage()
        mem = self.get_memory_info()
        
        # Check if resources are already strained
        if cpu > self.thresholds.max_cpu_percent:
            return 0, f"CPU usage too high ({cpu:.1f}%)"
        
        if mem["percent"] > self.thresholds.max_memory_percent:
            return 0, f"Memory usage too high ({mem['percent']:.1f}%)"
        
        # Calculate available headroom
        available_cpu = self.thresholds.max_cpu_percent - cpu
        available_memory_gb = mem["available_gb"] - 1.0  # Keep 1GB buffer
        
        # Calculate max workers based on each resource
        workers_by_cpu = int(available_cpu / self.thresholds.cpu_per_worker)
        workers_by_memory = int(available_memory_gb / self.thresholds.min_memory_per_worker_gb)
        
        # Take the minimum and apply bounds
        recommended = min(
            workers_by_cpu,
            workers_by_memory,
            self.thresholds.max_workers
        )
        
        # Ensure at least base workers if resources allow any
        recommended = max(recommended, self.thresholds.base_workers)
        
        # Don't exceed max
        recommended = min(recommended, self.thresholds.max_workers)
        
        reason = f"Based on {available_cpu:.1f}% CPU and {available_memory_gb:.1f}GB memory available"
        
        return recommended, reason
    
    def can_accept_batch(self) -> Tuple[bool, str]:
        """
        Check if server can accept a new batch.
        
        Returns:
            Tuple of (can_accept, reason)
        """
        if self._current_workers > 0:
            return False, f"Server is busy with {self._current_workers} active workers"
        
        recommended, reason = self.calculate_recommended_workers()
        
        if recommended == 0:
            return False, reason
        
        return True, f"Server can accept batch with up to {recommended} workers"
    
    def is_server_busy(self) -> bool:
        """Check if server is currently processing a batch"""
        return self._current_workers > 0
    
    async def allocate_workers(self, requested: int = None) -> int:
        """
        Allocate workers for a batch.
        
        Args:
            requested: Requested number of workers (uses recommended if None)
            
        Returns:
            Number of workers allocated
        """
        async with self._lock:
            if self._current_workers > 0:
                return 0  # Already busy
            
            recommended, _ = self.calculate_recommended_workers()
            
            if requested is not None:
                allocated = min(requested, recommended)
            else:
                allocated = recommended
            
            self._current_workers = allocated
            return allocated
    
    async def release_workers(self, count: int = None):
        """
        Release workers after batch completion.
        
        Args:
            count: Number to release (releases all if None)
        """
        async with self._lock:
            if count is None:
                self._current_workers = 0
            else:
                self._current_workers = max(0, self._current_workers - count)
    
    def get_current_workers(self) -> int:
        """Get current number of active workers"""
        return self._current_workers
    
    def get_uptime_seconds(self) -> float:
        """Get server uptime in seconds"""
        return (datetime.now() - self._start_time).total_seconds()
    
    def get_resource_info_dict(self) -> Dict[str, Any]:
        """Get resource info as dictionary for API response"""
        recommended, _ = self.calculate_recommended_workers()
        snapshot = self.get_resource_snapshot()
        
        return {
            "cpu_percent": snapshot["cpu_percent"],
            "memory_percent": snapshot["memory_percent"],
            "memory_available_gb": snapshot["memory_available_gb"],
            "memory_total_gb": snapshot["memory_total_gb"],
            "recommended_workers": recommended,
            "max_workers": self.thresholds.max_workers,
            "current_workers": self._current_workers,
            "is_busy": self._current_workers > 0
        }


# Global resource manager instance
resource_manager = ResourceManager()