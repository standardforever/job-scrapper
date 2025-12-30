from pydantic_settings import BaseSettings
from pydantic import AnyUrl
from typing import Optional


class Setttings(BaseSettings):
    OPENAI_API_KEY: str

    # Vector store configuration
    USE_QDRANT: bool = True
    QDRANT_URL: AnyUrl = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None

    #Mongdb configuration
    MONGO_URI: str = "mongodb://127.0.0.1:27017"
    DATABASE_NAME: str = "job_scraper"

    
    # Model configuration
    LLM_MODEL: str =  "gpt-4o-mini"
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    
    # Chunking configuration
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int =  200
    
    # PDF processing configuration
    ENABLE_OCR: bool = True
    ENABLE_TABLES: bool = True
    MAX_FILE_SIZE_MB: int = 50


    class Config:
        env_file = ".env"  # Load values from .env file


settings = Setttings()