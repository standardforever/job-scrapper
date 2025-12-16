import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from browser_use import Agent, BrowserSession, ChatOpenAI

from openai import OpenAI
from pydantic import BaseModel

# =============================================================================
# Pydantic Models for Agent Output
# =============================================================================


class PaginationCheck(BaseModel):
    has_pagination: bool
    current_page: Optional[int] = None
    total_pages: Optional[int] = None


class LoadMoreCheck(BaseModel):
    has_load_more_button: bool
    button_text: Optional[str] = None