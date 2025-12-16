from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import json 
from utils.llm_prompt import (
    create_job_page_analysis_prompt,
    create_job_page_analysis_prompt_detail,
)
from openai import OpenAI


# =============================================================================
# Assuming these are imported from your existing modules:
# from chrome_manager import ChromeCDPManager, ChromeConfig
# from dom_extractor import DOMContentExtractor, ExtractedContent
# from web_searcher import WebSearcher, SearchEngine, SearchResult
# from utils.llm_prompt import create_job_page_analysis_prompt, create_job_page_analysis_prompt_rag
# =============================================================================


# =============================================================================
# Job Page Analyzer
# =============================================================================


class AnalysisPromptType(Enum):
    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"


@dataclass
class AnalysisResult:
    response: dict[str, Any] | str
    success: bool
    error: Optional[str] = None


class JobPageAnalyzer:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._client = OpenAI(api_key=api_key)
        self._model = model

    async def analyze(
        self,
        url: str,
        content: str,
        prompt_type: AnalysisPromptType = AnalysisPromptType.UNSTRUCTURED,
        json_resonse: bool = True
    ) -> AnalysisResult:
        

        prompt = (
            create_job_page_analysis_prompt_detail(url, content)
            if prompt_type == AnalysisPromptType.STRUCTURED
            else create_job_page_analysis_prompt(url, content)
        )
        error = ""
        for i in range(2):
            try:
                response = self._client.responses.create(
                    model=self._model,
                    input=prompt,
                )
                output = response.output_text
                if json_resonse:
                    output = json.loads(output)
                return AnalysisResult(
                    response=output,
                    success=True,
                )
            except Exception as e:
                print(f"Faild calling analysis {e}")
                error = e

                continue
        return AnalysisResult(
            response={},
            success=False,
            error=str(error),
        )
    
    async def analyze_data(
        self,
        prompt_type: AnalysisPromptType = AnalysisPromptType.STRUCTURED,
        json_resonse: bool = True,
        **kwargs
        
    ) -> AnalysisResult:
        
        if prompt_type == AnalysisPromptType.STRUCTURED:
            if not kwargs.get("url") or not kwargs.get("content"):
                raise ValueError("url and page content needs to be provided to use the STRUCTED PROMPT")
            else:
                prompt = create_job_page_analysis_prompt(kwargs.get("url", ""), kwargs.get("content", ""))

        elif prompt_type == AnalysisPromptType.UNSTRUCTURED:
            if not kwargs.get("url") or not kwargs.get("content"):
                raise ValueError("url and page content needs to be provided to use the STRUCTED PROMPT")
            else:
                prompt = create_job_page_analysis_prompt_detail(kwargs.get("url", ""), kwargs.get("content", ""))


        error = ""
        for i in range(2):
            try:
                response = self._client.responses.create(
                    model=self._model,
                    input=prompt,
                )
                output = response.output_text
                if json_resonse:
                    output = json.loads(output)
                return AnalysisResult(
                    response=output,
                    success=True,
                )
            except Exception as e:
                print(f"Faild calling analysis {e}")
                error = e

                continue
        return AnalysisResult(
            response={},
            success=False,
            error=str(error),
        )
