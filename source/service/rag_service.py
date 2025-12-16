"""
Qdrant RAG (Retrieval-Augmented Generation) Class
Handles embedding and querying scraped web pages in Qdrant vector database
"""

import os
from typing import List, Dict, Any, Optional
from uuid import uuid4
from utils.logging import setup_logger
from dotenv import load_dotenv
load_dotenv()

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue
)
from openai import OpenAI

# Configure logging
logger = setup_logger(__name__)


class QdrantRAG:
    """
    RAG class for storing and querying scraped web pages using Qdrant vector database.
    
    Features:
    - Embeds text content using OpenAI embeddings
    - Stores embeddings with metadata in Qdrant
    - Supports similarity search and filtering
    - Handles batch operations
    """
    
    def __init__(
        self,
        collection_name: str,
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",
        vector_size: int = 1536,
        distance_metric: Distance = Distance.COSINE,
        use_local: bool = False,
        local_path: str = "./qdrant_data"
    ):
        """
        Initialize the Qdrant RAG system.
        
        Args:
            collection_name: Name of the Qdrant collection
            qdrant_url: Qdrant server URL (for cloud/remote)
            qdrant_api_key: Qdrant API key (for cloud)
            openai_api_key: OpenAI API key for embeddings
            embedding_model: OpenAI embedding model to use
            vector_size: Dimension of embedding vectors (1536 for text-embedding-3-small)
            distance_metric: Distance metric for similarity search
            use_local: Whether to use local Qdrant instance
            local_path: Path for local Qdrant storage
        """
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        self.vector_size = vector_size
        
        # Initialize OpenAI client
        self.openai_client = OpenAI(
            api_key=openai_api_key or os.getenv("OPENAI_API_KEY")
        )
        
        # Initialize Qdrant client
        if use_local:
            logger.info(f"Using local Qdrant at {local_path}")
            self.qdrant_client = QdrantClient(path=local_path)
        else:
            logger.info(f"Connecting to Qdrant at {qdrant_url}")
            self.qdrant_client = QdrantClient(
                url=qdrant_url or os.getenv("QDRANT_URL"),
                api_key=qdrant_api_key or os.getenv("QDRANT_API_KEY")
            )
        
        # Create collection if it doesn't exist
        self._ensure_collection_exists(distance_metric)
    
    def _ensure_collection_exists(self, distance_metric: Distance):
        """Create collection if it doesn't exist."""
        try:
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.collection_name not in collection_names:
                logger.info(f"Creating collection: {self.collection_name}")
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=distance_metric
                    )
                )
                logger.info(f"Collection {self.collection_name} created successfully")
            else:
                logger.info(f"Collection {self.collection_name} already exists")
        except Exception as e:
            logger.error(f"Error ensuring collection exists: {e}")
            raise
    
    def _get_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text using OpenAI.
        
        Args:
            text: Text to embed
            
        Returns:
            List of floats representing the embedding vector
        """
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise
    
    def _get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in batch.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"Error generating batch embeddings: {e}")
            raise
    
    def add_page(
        self,
        text: str,
        url: str,
        metadata: Optional[Dict[str, Any]] = None,
        page_id: Optional[str] = None
    ) -> str:
        """
        Add a single scraped page to the collection.
        
        Args:
            text: Content of the scraped page
            url: URL of the page
            metadata: Additional metadata (e.g., title, timestamp, tags)
            page_id: Optional custom ID, otherwise generated automatically
            
        Returns:
            ID of the stored point
        """
        try:
            # Generate embedding
            embedding = self._get_embedding(text)
            
            # Prepare metadata
            payload = {
                "text": text,
                "url": url,
                "text_length": len(text),
                **(metadata or {})
            }
            
            # Generate ID if not provided
            point_id = page_id or str(uuid4())
            
            # Create point
            point = PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload
            )
            
            # Upload to Qdrant
            self.qdrant_client.upsert(
                collection_name=self.collection_name,
                points=[point]
            )
            
            logger.info(f"Added page: {url} with ID: {point_id}")
            return point_id
            
        except Exception as e:
            logger.error(f"Error adding page {url}: {e}")
            raise
    
    def add_pages_batch(
        self,
        pages: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> List[str]:
        """
        Add multiple scraped pages in batch.
        
        Args:
            pages: List of dicts with keys: 'text', 'url', and optional 'metadata', 'page_id'
            batch_size: Number of pages to process at once
            
        Returns:
            List of IDs for stored points
        """
        point_ids = []
        
        try:
            for i in range(0, len(pages), batch_size):
                batch = pages[i:i + batch_size]
                
                # Extract texts for batch embedding
                texts = [page["text"] for page in batch]
                
                # Generate embeddings in batch
                embeddings = self._get_embeddings_batch(texts)
                
                # Create points
                points = []
                for page, embedding in zip(batch, embeddings):
                    point_id = page.get("page_id") or str(uuid4())
                    payload = {
                        "text": page["text"],
                        "url": page["url"],
                        "text_length": len(page["text"]),
                        **page.get("metadata", {})
                    }
                    
                    points.append(PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload=payload
                    ))
                    point_ids.append(point_id)
                
                # Upload batch to Qdrant
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=points
                )
                
                logger.info(f"Added batch of {len(points)} pages (batch {i//batch_size + 1})")
            
            logger.info(f"Successfully added {len(point_ids)} pages in total")
            return point_ids
            
        except Exception as e:
            logger.error(f"Error in batch upload: {e}")
            raise
    
    def query(
        self,
        query_text: str,
        limit: int = 5,
        score_threshold: Optional[float] = None,
        url_filter: Optional[str] = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Query the collection for similar pages.
        
        Args:
            query_text: Text to search for
            limit: Maximum number of results
            score_threshold: Minimum similarity score (0-1)
            url_filter: Filter by URL (partial match)
            metadata_filter: Additional metadata filters
            
        Returns:
            List of matching pages with scores
        """
        try:
            # Generate query embedding
            query_embedding = self._get_embedding(query_text)
            
            # Build filter if needed
            search_filter = None
            if url_filter or metadata_filter:
                conditions = []
                
                if url_filter:
                    conditions.append(
                        FieldCondition(
                            key="url",
                            match=MatchValue(value=url_filter)
                        )
                    )
                
                if metadata_filter:
                    for key, value in metadata_filter.items():
                        conditions.append(
                            FieldCondition(
                                key=key,
                                match=MatchValue(value=value)
                            )
                        )
                
                if conditions:
                    search_filter = Filter(must=conditions)
            
            # Search Qdrant
            results = self.qdrant_client.query_points(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                limit=limit,
                score_threshold=score_threshold,
                query_filter=search_filter
            ).points
            
            # Format results
            formatted_results = []
            for result in results:
                formatted_results.append({
                    "id": result.id,
                    "score": result.score,
                    "text": result.payload.get("text"),
                    "url": result.payload.get("url"),
                    "metadata": {k: v for k,  v in result.payload.items() 
                               if k not in ["text", "url", "text_length"]}
                })
            
            logger.info(f"Query returned {len(formatted_results)} results")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error querying collection: {e}")
            raise
    
    def delete_page(self, page_id: str) -> bool:
        """
        Delete a page by ID.
        
        Args:
            page_id: ID of the page to delete
            
        Returns:
            True if successful
        """
        try:
            self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=[page_id]
            )
            logger.info(f"Deleted page with ID: {page_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting page {page_id}: {e}")
            return False
    
    def get_collection_info(self) -> Dict[str, Any]:
        """
        Get information about the collection.
        
        Returns:
            Dictionary with collection stats
        """
        try:
            info = self.qdrant_client.get_collection(self.collection_name)
            return {
                "name": self.collection_name,
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": info.status
            }
        except Exception as e:
            logger.error(f"Error getting collection info: {e}")
            raise
    
    def clear_collection(self) -> bool:
        """
        Delete all points from the collection.
        
        Returns:
            True if successful
        """
        try:
            self.qdrant_client.delete_collection(self.collection_name)
            self._ensure_collection_exists(Distance.COSINE)
            logger.info(f"Cleared collection: {self.collection_name}")
            return True
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            return False