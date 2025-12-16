"""
MongoDB Service for Job Data Storage
Handles traditional database operations for scraped job data
Works in hybrid mode with RAG for semantic search
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from utils.logging import setup_logger

from pymongo import MongoClient, ASCENDING, DESCENDING, IndexModel
from pymongo.errors import DuplicateKeyError, ConnectionFailure
from bson import ObjectId

from core.config import settings

# Configure logging
logger = setup_logger(__name__)


class MongoDBService:
    """
    MongoDB service for storing and querying scraped job data.
    
    Features:
    - Store complete job data with all metadata
    - Traditional filtering and sorting
    - Pagination support
    - Efficient indexing for fast queries
    - Batch operations
    - Aggregation pipelines for analytics
    """
    
    def __init__(
        self,
        database_name: Optional[str] = None,
        collection_name: str = "jobs",
        mongo_uri: Optional[str] = None,
        create_indexes: bool = True
    ):
        """
        Initialize MongoDB service.
        
        Args:
            database_name: Name of the MongoDB database
            collection_name: Name of the collection
            mongo_uri: MongoDB connection URI (default: localhost)
            create_indexes: Whether to create indexes on initialization
        """
        self.database_name = database_name or settings.DATABASE_NAME
        self.collection_name = collection_name
        
        # Connect to MongoDB
        self.mongo_uri = mongo_uri or settings.MONGO_URI
        
        try:
            self.client = MongoClient(str(self.mongo_uri))
            # Test connection
            self.client.admin.command('ping')
            logger.info(f"Connected to MongoDB at {self.mongo_uri}")
        except ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        # Get database and collection
        self.db = self.client[self.database_name]
        self.collection = self.db[self.collection_name]
        
        # Create indexes for better performance
        if create_indexes:
            self._create_indexes()
        
        self.stats = {
            "total_inserted": 0,
            "total_updated": 0,
            "duplicates_skipped": 0,
            "errors": 0
        }
    
    def _create_indexes(self):
        """Create indexes for efficient querying."""
        try:
            indexes = [
                IndexModel([("url", ASCENDING)], unique=True),  # Unique URL constraint
                IndexModel([("title", ASCENDING)]),
                IndexModel([("company", ASCENDING)]),
                IndexModel([("location", ASCENDING)]),
                IndexModel([("scraped_at", DESCENDING)]),
                IndexModel([("posted_date", DESCENDING)]),
                IndexModel([("application_method", ASCENDING)]),
                IndexModel([("is_easy_apply", ASCENDING)]),
                IndexModel([("salary_min", ASCENDING)]),
                IndexModel([("skills", ASCENDING)]),  # For array field
                # Compound indexes for common queries
                IndexModel([("location", ASCENDING), ("title", ASCENDING)]),
                IndexModel([("company", ASCENDING), ("location", ASCENDING)]),
                IndexModel([("is_easy_apply", ASCENDING), ("location", ASCENDING)]),
                # Text index for full-text search
                IndexModel([("title", "text"), ("text", "text"), ("company", "text")])
            ]
            
            self.collection.create_indexes(indexes)
            logger.info("Database indexes created successfully")
        except Exception as e:
            logger.warning(f"Error creating indexes: {e}")
    
    def add_job(
        self,
        job_data: Dict[str, Any],
        update_if_exists: bool = True
    ) -> Optional[str]:
        """
        Add a single job to the database.
        
        Args:
            job_data: Dictionary containing job information
            update_if_exists: If True, update existing job instead of skipping
            
        Returns:
            Job ID (ObjectId as string) or None if duplicate and not updating
        """
        try:
            # Add metadata
            job_data["scraped_at"] = job_data.get("scraped_at", datetime.now())
            job_data["updated_at"] = datetime.now()
            
            if update_if_exists:
                # Update if exists, insert if not
                result = self.collection.update_one(
                    {"url": job_data["url"]},
                    {"$set": job_data},
                    upsert=True
                )
                
                if result.upserted_id:
                    self.stats["total_inserted"] += 1
                    job_id = str(result.upserted_id)
                    logger.info(f"Inserted job: {job_data.get('title', 'Unknown')}")
                else:
                    self.stats["total_updated"] += 1
                    # Get the existing job ID
                    existing = self.collection.find_one({"url": job_data["url"]})
                    job_id = str(existing["_id"]) if existing else None
                    logger.info(f"Updated job: {job_data.get('title', 'Unknown')}")
                
                return job_id
            else:
                # Insert only (skip if duplicate)
                result = self.collection.insert_one(job_data)
                self.stats["total_inserted"] += 1
                logger.info(f"Inserted job: {job_data.get('title', 'Unknown')}")
                return str(result.inserted_id)
                
        except DuplicateKeyError:
            self.stats["duplicates_skipped"] += 1
            logger.debug(f"Duplicate job skipped: {job_data.get('url', 'Unknown URL')}")
            return None
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"Error adding job: {e}")
            return None
    
    def add_jobs_batch(
        self,
        jobs: List[Dict[str, Any]],
        update_if_exists: bool = False
    ) -> Dict[str, int]:
        """
        Add multiple jobs in batch.
        
        Args:
            jobs: List of job dictionaries
            update_if_exists: If True, update existing jobs
            
        Returns:
            Dictionary with statistics
        """
        results = {
            "inserted": 0,
            "updated": 0,
            "duplicates": 0,
            "errors": 0
        }
        
        if update_if_exists:
            # Use bulk write for updates
            from pymongo import UpdateOne
            
            operations = []
            for job in jobs:
                job["scraped_at"] = job.get("scraped_at", datetime.now())
                job["updated_at"] = datetime.now()
                
                operations.append(
                    UpdateOne(
                        {"url": job["url"]},
                        {"$set": job},
                        upsert=True
                    )
                )
            
            try:
                result = self.collection.bulk_write(operations, ordered=False)
                results["inserted"] = result.upserted_count
                results["updated"] = result.modified_count
                
                self.stats["total_inserted"] += results["inserted"]
                self.stats["total_updated"] += results["updated"]
                
                logger.info(f"Batch processed: {results['inserted']} inserted, {results['updated']} updated")
            except Exception as e:
                logger.error(f"Error in batch operation: {e}")
                results["errors"] += 1
        else:
            # Insert many (ignore duplicates)
            for job in jobs:
                job["scraped_at"] = job.get("scraped_at", datetime.now())
                job["updated_at"] = datetime.now()
            
            try:
                result = self.collection.insert_many(jobs, ordered=False)
                results["inserted"] = len(result.inserted_ids)
                self.stats["total_inserted"] += results["inserted"]
                logger.info(f"Batch inserted: {results['inserted']} jobs")
            except Exception as e:
                # Some may have been inserted before the error
                results["errors"] += 1
                logger.error(f"Error in batch insert: {e}")
        
        return results
    
    def find_jobs(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        skip: int = 0,
        sort_by: Optional[List[Tuple[str, int]]] = None,
        projection: Optional[Dict[str, int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Find jobs with filtering, pagination, and sorting.
        
        Args:
            filters: MongoDB query filters (e.g., {"location": "UK", "is_easy_apply": True})
            limit: Maximum number of results
            skip: Number of results to skip (for pagination)
            sort_by: List of (field, direction) tuples. E.g., [("scraped_at", -1)]
            projection: Fields to include/exclude. E.g., {"text": 0} to exclude text field
            
        Returns:
            List of job documents
        """
        try:
            query = filters or {}
            cursor = self.collection.find(query, projection)
            
            if sort_by:
                cursor = cursor.sort(sort_by)
            
            cursor = cursor.skip(skip).limit(limit)
            
            jobs = []
            for job in cursor:
                job["_id"] = str(job["_id"])  # Convert ObjectId to string
                jobs.append(job)
            
            logger.info(f"Found {len(jobs)} jobs")
            return jobs
            
        except Exception as e:
            logger.error(f"Error finding jobs: {e}")
            return []
    
    def count_jobs(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """
        Count jobs matching filters.
        
        Args:
            filters: MongoDB query filters
            
        Returns:
            Number of matching jobs
        """
        try:
            query = filters or {}
            count = self.collection.count_documents(query)
            return count
        except Exception as e:
            logger.error(f"Error counting jobs: {e}")
            return 0
    
    def search_jobs_text(
        self,
        search_text: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        skip: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Full-text search using MongoDB text index.
        
        Args:
            search_text: Text to search for
            filters: Additional filters to apply
            limit: Maximum results
            skip: Results to skip
            
        Returns:
            List of matching jobs
        """
        try:
            query = {"$text": {"$search": search_text}}
            
            # Add additional filters
            if filters:
                query.update(filters)
            
            cursor = self.collection.find(
                query,
                {"score": {"$meta": "textScore"}}
            ).sort([("score", {"$meta": "textScore"})]).skip(skip).limit(limit)
            
            jobs = []
            for job in cursor:
                job["_id"] = str(job["_id"])
                jobs.append(job)
            
            logger.info(f"Text search returned {len(jobs)} jobs")
            return jobs
            
        except Exception as e:
            logger.error(f"Error in text search: {e}")
            return []
    
    def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single job by ID.
        
        Args:
            job_id: Job ID (ObjectId as string)
            
        Returns:
            Job document or None
        """
        try:
            job = self.collection.find_one({"_id": ObjectId(job_id)})
            if job:
                job["_id"] = str(job["_id"])
            return job
        except Exception as e:
            logger.error(f"Error getting job by ID: {e}")
            return None
    
    def get_job_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Get a job by URL.
        
        Args:
            url: Job URL
            
        Returns:
            Job document or None
        """
        try:
            job = self.collection.find_one({"url": url})
            if job:
                job["_id"] = str(job["_id"])
            return job
        except Exception as e:
            logger.error(f"Error getting job by URL: {e}")
            return None
    
    def delete_job(self, job_id: str) -> bool:
        """
        Delete a job by ID.
        
        Args:
            job_id: Job ID
            
        Returns:
            True if deleted
        """
        try:
            result = self.collection.delete_one({"_id": ObjectId(job_id)})
            if result.deleted_count > 0:
                logger.info(f"Deleted job: {job_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting job: {e}")
            return False
    
    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run aggregation pipeline for analytics.
        
        Args:
            pipeline: MongoDB aggregation pipeline
            
        Returns:
            Aggregation results
        """
        try:
            results = list(self.collection.aggregate(pipeline))
            return results
        except Exception as e:
            logger.error(f"Error in aggregation: {e}")
            return []
    
    # Analytics helper methods
    
    def get_jobs_by_location(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get job counts grouped by location."""
        pipeline = [
            {"$group": {"_id": "$location", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ]
        return self.aggregate(pipeline)
    
    def get_jobs_by_company(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get job counts grouped by company."""
        pipeline = [
            {"$group": {"_id": "$company", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ]
        return self.aggregate(pipeline)
    
    def get_average_salary_by_location(self) -> List[Dict[str, Any]]:
        """Get average salary by location."""
        pipeline = [
            {"$match": {"salary_min": {"$exists": True, "$ne": None}}},
            {"$group": {
                "_id": "$location",
                "avg_salary_min": {"$avg": "$salary_min"},
                "avg_salary_max": {"$avg": "$salary_max"},
                "count": {"$sum": 1}
            }},
            {"$sort": {"avg_salary_min": -1}}
        ]
        return self.aggregate(pipeline)
    
    def get_top_skills(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get most in-demand skills."""
        pipeline = [
            {"$match": {"skills": {"$exists": True, "$ne": []}}},
            {"$unwind": "$skills"},
            {"$group": {"_id": "$skills", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ]
        return self.aggregate(pipeline)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        return {
            "total_jobs": self.collection.count_documents({}),
            "unique_companies": len(self.collection.distinct("company")),
            "unique_locations": len(self.collection.distinct("location")),
            "easy_apply_count": self.collection.count_documents({"is_easy_apply": True}),
            **self.stats
        }
    
    def clear_collection(self) -> int:
        """
        Delete all documents from the collection.
        
        Returns:
            Number of documents deleted
        """
        try:
            result = self.collection.delete_many({})
            logger.info(f"Cleared collection: {result.deleted_count} documents deleted")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            return 0
    
    def close(self):
        """Close MongoDB connection."""
        self.client.close()
        logger.info("MongoDB connection closed")