import csv
import io
from fastapi import HTTPException

from pathlib import Path
from typing import List, Dict, Any
import json

# Add this to your main.py

def flatten_job_data(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten nested job JSON structure for CSV export.
    
    Handles nested objects, lists, and dynamic keys.
    """
    flat = {}
    
    # Simple fields
    simple_fields = [
        "is_job_page", "confidence_reason", "title", "company_name", 
        "holiday", "job_type", "contract_type", "remote_option",
        "job_reference", "description", "company_info", "how_to_apply", "main_domain",
        "raw_text", "filter_domain", "url", "is_known_ats", "is_ats", "is_external_application",
        "ats_provider", "detection_reason", "created_at", "domain", "result", "success", "message", "error", "job_urls_checked"
    ]
    for field in simple_fields:
        flat[field] = job.get(field)
    

    
    # Location fields
    location = job.get("location") or {}
    flat["location_address"] = location.get("address")
    flat["location_city"] = location.get("city")
    flat["location_region"] = location.get("region")
    flat["location_postcode"] = location.get("postcode")
    flat["location_country"] = location.get("country")
    
    # Salary fields
    salary = job.get("salary") or {}
    flat["salary_min"] = salary.get("min")
    flat["salary_max"] = salary.get("max")
    flat["salary_currency"] = salary.get("currency")
    flat["salary_period"] = salary.get("period")
    flat["salary_actual"] = salary.get("actual_salary")
    flat["salary_raw"] = salary.get("raw")
    
    # Hours fields
    hours = job.get("hours") or {}
    flat["hours_weekly"] = hours.get("weekly")
    flat["hours_daily"] = hours.get("daily")
    flat["hours_details"] = hours.get("details")
    
    # ai ats fields
    ai_ats = job.get("ai_ats_details", {}) or {}
    flat["is_ats_ai"] = ai_ats.get("is_ats")
    flat["ats_apply_url_ai"] = ai_ats.get("apply_url")
    flat["ats_platform_name_ai"] = ai_ats.get("platform_name")
    
    # Date fields
    for date_field in ["closing_date", "interview_date", "start_date", "post_date"]:
        date_obj = job.get(date_field) or {}
        flat[f"{date_field}_iso"] = date_obj.get("iso_format")
        flat[f"{date_field}_raw"] = date_obj.get("raw_text")
    
    # Contact fields
    contact = job.get("contact") or {}
    flat["contact_name"] = contact.get("name")
    flat["contact_email"] = contact.get("email")
    flat["contact_phone"] = contact.get("phone")
    
    # Application method fields
    app_method = job.get("application_method") or {}
    flat["application_type"] = app_method.get("type")
    flat["application_url"] = app_method.get("url")
    flat["application_email"] = app_method.get("email")
    flat["application_instructions"] = app_method.get("instructions")
    
    # List fields - join with semicolons
    flat["responsibilities"] = "; ".join(job.get("responsibilities") or [])
    flat["requirements"] = "; ".join(job.get("requirements") or [])
    flat["benefits"] = "; ".join(job.get("benefits") or [])
    
    # Additional sections - convert to JSON string or concatenate
    additional = job.get("additional_sections") or {}
    if additional:
        flat["additional_sections"] = json.dumps(additional)
    else:
        flat["additional_sections"] = None
    
    # Metadata fields (if added by file manager)
    flat["_saved_at"] = job.get("_saved_at")
    flat["_file_index"] = job.get("_file_index")
    
    return flat


def read_all_jobs_from_files(output_dir: str = "job_outputs", task_id: str | None = None) -> List[Dict[str, Any]]:
    """
    Read all job records from JSON files in the output directory.

    Args:
        output_dir: Directory containing job JSON files
        task_id: Optional task identifier to filter files

    Returns:
        List of all job records
    """
    output_path = Path(output_dir)
    
    if not output_path.exists():
        raise HTTPException(status_code=404, detail=f"Output directory '{output_dir}' not found")
    
    all_jobs = []
    # ✅ Filter files by task_id if provided
    if task_id:
        json_files = sorted(
            output_path.glob(f"jobs_{task_id}*.json")
        )
    else:
        json_files = sorted(output_path.glob("jobs_*.json"))
    
    if not json_files:
        raise HTTPException(status_code=404, detail="No job files found")
    
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                jobs = json.load(f)
                if isinstance(jobs, list):
                    all_jobs.extend(jobs)
                else:
                    all_jobs.append(jobs)
        except json.JSONDecodeError as e:
            print(f"Error reading {json_file}: {e}")
            continue
    
    return all_jobs


def generate_csv_from_jobs(jobs: List[Dict[str, Any]]) -> str:
    """
    Convert list of job dictionaries to CSV format.
    
    Args:
        jobs: List of job dictionaries
        
    Returns:
        CSV content as string
    """
    if not jobs:
        raise HTTPException(status_code=404, detail="No jobs found to export")
    
    # Flatten all jobs
    flattened_jobs = [flatten_job_data(job) for job in jobs]
    
    # Get all unique field names (in case some jobs have different fields)
    all_fields = set()
    for job in flattened_jobs:
        all_fields.update(job.keys())
    
    # Define field order (put important fields first)
    priority_fields = [
        "title", "company_name", "location_city", "location_region", "location_country",
        "salary_min", "salary_max", "salary_currency", "salary_period", "salary_raw",
        "job_type", "contract_type", "remote_option", "closing_date_iso", "closing_date_raw"
    ]
    
    # Sort fields: priority first, then alphabetically
    remaining_fields = sorted(all_fields - set(priority_fields))
    ordered_fields = [f for f in priority_fields if f in all_fields] + remaining_fields
    
    # Write to CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ordered_fields, extrasaction='ignore')
    
    writer.writeheader()
    for job in flattened_jobs:
        writer.writerow(job)
    
    return output.getvalue()


