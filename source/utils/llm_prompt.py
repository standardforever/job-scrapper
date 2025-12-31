from typing import Dict, Any


def create_job_page_analysis_prompt(url: str, text: str) -> str:
    """
    Creates the analysis prompt with embedded response schema.
    """

    prompt = f"""Analyze the webpage below and classify its job-related status.

URL: {url}

PAGE CONTENT:
{text}

---

PAGE CATEGORIES (choose exactly ONE):

1. **jobs_listed**
   - Multiple job postings are directly visible on this page
   - Job titles with links such as Apply, More Info, or View Details are present
   - This represents a full job listings page
   - next_action: "scrape_jobs"

2. **job_listings_preview_page**
   - A limited or featured subset of jobs is visible (eg "Featured roles")
   - A link or button exists to view ALL jobs on another page
   - next_action: "navigate"
   - Populate next_action_target with the link/button to the full listings

3. **navigation_required**
   - No job postings are visible on this page
   - The page indicates jobs exist and requires navigation to find them
   - Examples: "View open roles", "Careers", "We're hiring"
   - next_action: "navigate"
   - Populate next_action_target

4. **single_job_posting** 
    - A specific job opportunity is described on this page
   This includes BOTH:

   a) **Detailed job postings** with full descriptions:
      - Comprehensive job description, requirements, responsibilities
      - Salary, benefits, qualifications listed
      
   b) **Minimal job postings** with basic information:
      - Just a job title/role and brief description
      - "We're hiring for X role" announcements
      - Simple vacancy notices with contact info to apply or inquire
      - Posts that mention a position and how to apply/get more info

5. **not_job_related**
   - No job, career, or hiring content
   - next_action: "stop"

RULES:
- Links next to job titles (Apply, View Details, More Info) are job_url, not navigation
- Navigation is only for finding where jobs are listed
- If SOME jobs are shown AND a link exists to view all jobs, classify as job_listings_preview_page
- Extract ALL jobs visible on the page only

FILTER:
- Include jobs with UK location or unspecified location (including remote)
- Exclude jobs with locaion specified and non-UK locations

RESPONSE FORMAT:
- Return ONLY valid JSON
- Do NOT wrap in markdown code blocks (no ```json or ```)
- Do NOT include any text before or after the JSON
- Start directly with {{ and end with }}
- Return the result strictly using the schema below.

RESPONSE SCHEMA:

{{
    "page_category": "jobs_listed" | "job_listings_preview_page" | "navigation_required" | "single_job_posting" | "not_job_related",
    "next_action": "scrape_jobs" | "navigate" | "scrape_single_job" | "stop",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief explanation>",
    "domain_name": "<website main domain>",
    "url": "<main url>",

    "next_action_target": {{
        "url": "<URL or null>",
        "link_text": "<text or null>",
        "element_type": "link" | "button" | null
    }},

    "jobs_listed_on_page": [
        {{
            "title": "<job title>",
            "job_url": "<URL or null>",
            "path": "<path>"
        }}
    ],

    "pagination": {{
        "is_paginated_page": <boolean>,
        "has_more_pages": <boolean>,
        "next_page_url": "<URL or null>",
        "total_pages": <integer or null>,
        "total_jobs": <integer or null>,
        "current_page": <integer or null>
    }}
}}

"""
    return prompt

def get_job_extraction_prompt(text: str) -> str:
    """
    Generate a prompt for extracting structured job data from raw text.
    
    Args:
        text: Raw text content from a job listing page
        
    Returns:
        Formatted prompt string for LLM processing
    """
    return f"""You are a job listing data extraction expert. Extract structured information from the text below.

OUTPUT FORMAT:
- Return ONLY valid JSON
- Start with {{ and end with }}
- Do NOT wrap in markdown code blocks (no ```json or ```)
- No markdown, no explanations, no preamble
- Use null for missing fields (never use empty strings)

LOCATION FILTER (CRITICAL):
1. INCLUDE: Jobs in UK locations OR unspecified/remote locations
2. EXCLUDE: Jobs with explicitly stated non-UK locations
3. If job is excluded, return empty dictionary: {{}}

CONTENT EXTRACTION RULES:
1. Extract ALL meaningful job-related content from the text
2. EXCLUDE navigation elements, buttons, footer text, cookie notices, and unrelated website content
3. Map content to the most appropriate standard field - do NOT duplicate in additional_sections
4. If a section fits a standard field (description, responsibilities, requirements, benefits, company_info, how_to_apply), use that field
5. Only use additional_sections for unique sections that don't fit standard fields

FIELD MAPPING GUIDE:
→ "description": Role Overview, About the Role, Job Summary, Position Description
→ "responsibilities": Key Responsibilities, Main Duties, Day-to-Day Tasks, What You'll Do
→ "requirements": Essential Criteria, Required Skills, Qualifications, Experience Needed, Must-Have Skills
→ "benefits": What We Offer, Package, Perks, Employee Benefits
→ "company_info": About Us, Company Overview, Who We Are, Our Culture
→ "how_to_apply": Application Instructions, How to Apply, Application Process, Next Steps

JSON SCHEMA:
{{
  "is_job_page": true,
  "confidence_reason": "Why you determined this is/isn't a valid job listing",
  "title": "Job title as stated",
  "company_name": "Employer name",
  "holiday": "Holiday/vacation days (e.g., '25 days' or '25 days plus bank holidays')",
  "location": {{
    "address": "Full address if provided",
    "city": "City name",
    "region": "County/Region/State",
    "postcode": "Postal code",
    "country": "Country name (extract 'UK', 'United Kingdom', etc.)"
  }},
  "salary": {{
    "min": "Minimum as number (e.g., 30000)",
    "max": "Maximum as number (e.g., 45000)",
    "currency": "GBP|USD|EUR (3-letter code)",
    "period": "annually|monthly|weekly|hourly|daily",
    "actual_salary": "Exact salary if single figure (e.g., 35000)",
    "raw": "Original salary text exactly as written"
  }},
  "job_type": "full-time|part-time",
  "contract_type": "permanent|temporary|contract|freelance",
  "remote_option": "remote|hybrid|on-site",
  "hours": {{
    "weekly": "Weekly hours as number (e.g., 37.5)",
    "daily": "Daily hours as number (e.g., 7.5)",
    "details": "Any additional hours information as written"
  }},
  "closing_date": {{
    "iso_format": "YYYY-MM-DD format if parseable",
    "raw_text": "Exactly as written in listing"
  }},
  "interview_date": {{
    "iso_format": "YYYY-MM-DD format if parseable",
    "raw_text": "Exactly as written in listing"
  }},
  "start_date": {{
    "iso_format": "YYYY-MM-DD format if parseable",
    "raw_text": "Exactly as written in listing"
  }},
  "post_date": {{
    "iso_format": "YYYY-MM-DD format if parseable",
    "raw_text": "Exactly as written in listing"
  }},
  "contact": {{
    "name": "Contact person name",
    "email": "Contact email",
    "phone": "Contact phone number"
  }},
  "job_reference": "Reference/ID number for the position",
  "description": "Main job description and overview paragraph(s)",
  "responsibilities": ["List of key duties and responsibilities"],
  "requirements": ["List of required qualifications, skills, and experience"],
  "benefits": ["List of benefits, perks, and package details"],
  "company_info": "Information about the employer/organization",
  "how_to_apply": "Application instructions and process details",
  "application_method": {{
    "type": "email|online_form|external_link|post|phone|in_person",
    "url": "Application URL if applicable",
    "email": "Application email if applicable",
    "instructions": "Specific application instructions"
  }},
  "additional_sections": {{
    "Unique Section Name": "Content that doesn't fit standard fields above"
  }}
}}

EXAMPLES OF WHAT TO EXCLUDE:
- "Apply Now" buttons or "Click Here" links
- Navigation menus and website headers
- "Cookie Policy", "Privacy Policy", "Terms of Service"
- Social media links and share buttons
- "Related Jobs", "You may also like" sections
- Job board branding and footer content
- Timestamps like "Posted 2 hours ago" (unless part of post_date)

EXAMPLES OF WHAT TO INCLUDE:
- All descriptive text about the role and company
- Bullet points listing duties, requirements, or benefits
- Salary and compensation details
- Working hours and schedule information
- Location and remote work details
- Application deadline and process
- Interview or start date information
- Company culture and values descriptions

TEXT TO EXTRACT FROM:
{text}"""

# def get_job_extraction_prompt(text: str) -> str:
#     """
#     Generate a prompt for extracting structured job data from raw text.
    
#     Args:
#         text: Raw text content from a job listing page
        
#     Returns:
#         Formatted prompt string for LLM processing
#     """
#     return f"""Extract job listing data from the text below. Return ONLY valid JSON starting with {{ and ending with }}.

# RULES:
# 1. Map to standard fields when present (use null if not found)
# 2. ALL text content must be returned - nothing omitted except buttons/URLs

# SCHEMA:
# {{
#   "title": null,
#   "company_name": null,
#   "holiday": null,
#   "location": {{
#     "address": "full address",
#     "city": null,
#     "region": null,
#     "postcode": null,
#     "country": null
#   }},
#   "salary": {{
#     "min": null,
#     "max": null,
#     "currency": "GBP|USD|EUR|etc",
#     "period": "annually|monthly|weekly|hourly|daily",
#     "actual_salary": null,
#     "raw": "original salary text"
#   }},
#   "job_type": "full-time|part-time|null",
#   "contract_type": "permanent|temporary|contract|freelance|null",
#   "remote_option": "remote|hybrid|on-site|null",
#   "hours": {{
#     "weekly": null,
#     "daily": null,
#     "details": "raw hours text"
#   }},
#   "closing_date": {{"iso_format": "ISO format", "raw_text": "raw text"}}
#   "interview_date": {{"iso_format": "ISO format", "raw_text": "raw text"}}
#   "start_date": {{"iso_format": "ISO format", "raw_text": "raw text"}}
#   "post_date": {{"iso_format": "ISO format", "raw_text": "raw text"}}
#   "contact": {{"name": null, "email": null, "phone": null}},
#   "job_reference": null,
#   "description": "main job description text",
#   "responsibilities": [],
#   "requirements": [],
#   "benefits": [],
#   "company_info": null,
#   "how_to_apply": null,
#   "additional_sections": {{
#     "section_name": "full text content"
#   }},
#   "is_job_page": true,
#   "confidence_reason": "brief explanation",
#    "application_method": {{
#     "type": "email|online_form|external_link|post|phone|in_person|null",
#     "url": null,
#     "email": null,
#     "instructions": "how to apply text"
#   }}
  
# }}

# FIELD MAPPING (use these, NOT additional_sections):
# - "Role Overview", "About the Role", "Job Description" → description
# - "Key Responsibilities", "Duties" → responsibilities
# - "Requirements", "Qualifications", "Skills", "Experience" → requirements  
# - "Benefits", "Perks", "What We Offer" → benefits
# - "About Us", "Company Overview", "Who We Are" → company_info
# - "How to Apply", "Application Process", "To Apply" → how_to_apply

# FILTER:
# - Include jobs with UK location or unspecified location (including remote)
# - Exclude jobs with locaion specified and non-UK locations
# NOTE: if non-UK location or remote or unspecified location return {{}} (empty dictionary)
# NOTE: All text content must be return except things not related to the job(noise), button/link
# NOTE: Any paragraph or section already have a key don't include in additional_sections(i.e don't repeat yourself in additional_sections)


# TEXT:
# {text}"""


# def get_job_extraction_prompt(text: str) -> str:
#     """
#     Generate a prompt for extracting structured job data from raw text.
    
#     Args:
#         text: Raw text content from a job listing page
        
#     Returns:
#         Formatted prompt string for LLM processing
#     """
#     return f"""You are a job listing data extractor. Extract information from the provided text and return ONLY valid JSON with no additional text, explanations, or markdown code blocks.

# Extract these fields (use null if not found):

# {{
#   "title": "job title",
#   "company_name": "employer name",
#   "contract_type": "permanent|temporary|fixed-term|contract|freelance|null",
#   "job_type": "full-time|part-time|null",
#   "hours": {{
#     "weekly": null,
#     "daily": null,
#     "details": "raw hours text"
#   }},
#   "location": {{
#     "address": "full address",
#     "city": null,
#     "region": null,
#     "postcode": null,
#     "country": null
#   }},
#   "remote_option": "remote|hybrid|on-site|null",
#   "salary": {{
#     "min": null,
#     "max": null,
#     "currency": "GBP|USD|EUR|etc",
#     "period": "annually|monthly|weekly|hourly|daily",
#     "actual_salary": null,
#     "raw": "original salary text"
#   }},
#   "holiday": null,
#   "dates": {{
#     "closing_date": "ISO format or raw text",
#     "interview_date": null,
#     "start_date": null
#   }},
#   "contact": {{
#     "name": null,
#     "email": null,
#     "phone": null,
#     "job_title": null
#   }},
#   "page_category": "job_detail|not_job_detail",
#   "reasoning": "brief explanation of why this is or isn't a job detail page",
#   "metadata": {{
#     "job_reference": null,
#     "job_description": null,
#     "responsibilities": [],
#     "requirements": [],
#     "benefits": [],
#     "company_info": null,
#     "application_notes": null,
#     "additional_info": {{}}
#   }}
# }}

# Rules:
# - Return ONLY the JSON object
# - Use null for missing fields, never empty strings
# - Extract ALL job-relevant information into metadata
# - Ignore navigation links, ads, footers, and page chrome
# - Parse salary numbers as integers
# - Preserve original text in "raw" or "details" fields when parsing structured data
# - For metadata.additional_info, capture any other job-relevant key-value pairs not covered above
# - Start directly with {{ and end with }}
# - Return the result strictly using the schema below.
# - NOTE: everything on the page most be return as part of a section key. All information is needed accroding to how it is started on the page

# TEXT TO EXTRACT:
# {text}"""




def create_job_page_analysis_prompt_detail(url: str, text: str) -> str:
    """
    Creates the analysis prompt for scraping a single job detail page.
    Focused exclusively on extracting job details - ignores any job listings on the page.
    """
    
    prompt = f"""Analyze this webpage and extract job details if a job posting's full details are visible.

URL: {url}

PAGE CONTENT:
{text}

---

CLASSIFICATION RULES:

1. **single_job_posting** - A job's FULL DETAILS are visible on this page
   - You can see: job description, requirements, responsibilities, salary, how to apply, etc.
   - The page may ALSO show a list of other jobs - IGNORE the listings
   - Focus ONLY on extracting the detailed job information
   - next_action: "scrape_job"

2. **not_job_detail_page** - NO job details are visible
   - Page only shows job listings without any expanded details
   - Page requires navigation/clicking to see job details
   - Page is not job-related at all
   - next_action: "stop"

IMPORTANT:
- If the page shows BOTH a detailed job AND a list of other jobs, classify as "single_job_posting"
- Extract ONLY the job that has full details visible - ignore any job listings/sidebar jobs
- We want the rich detail content (description, requirements, etc.), not just titles and links

LOCATION FILTER:
- Only extract jobs with UK locations, remote positions, or unspecified locations
- If the detailed job explicitly states a non-UK location, classify as "not_job_detail_page"

---

RESPONSE FORMAT:
- Return ONLY valid JSON
- Do NOT wrap in markdown code blocks (no ```json or ```)
- Do NOT include any text before or after the JSON
- Start directly with {{ and end with }}

RESPONSE SCHEMA:

{{
    "page_category": "single_job_posting" | "not_job_detail_page",
    "next_action": "scrape_job" | "stop",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief explanation of classification decision>",
    
    "job_details": {{
        "title": "<job title or null>",
        "job_url": "{url}",
        "job_description": "<full job description text or null>",
        "job_description_word_count": <integer word count or null>,
        
        "company_name": "<employer/company name or null>",
        "domain_name": "<website main domain>",
        
        "contract_type": "<permanent/temporary/fixed-term/contract/freelancer or null>",
        "job_type": "<full-time/part-time or null>",
        "hours": "<working hours info or null>",
        
        "location": "<job location or null>",
        "location_postcode": "<postcode if mentioned or null>",
        "remote_option": "<remote/hybrid/on-site or null>",
        
        "salary": "<salary info as stated or null>",
        "benefits": "<benefits listed or null>",
        "holiday": "<holiday/annual leave info or null>",
        
        "requirements": "<qualifications/requirements or null>",
        "responsibilities": "<key responsibilities or null>",
        
        "closing_date": "<application deadline or null>",
        "interview_date": "<interview date if mentioned or null>",
        "start_date": "<job start date or null>",
        
        "key_contact_name": null,
        "key_contact_email": null,
        "key_contact_job_title": null
        }},
        
    "application_methods": {{
        "form: {{
            "available": <boolean>,
            "form_url": "<url or 'current_page' or null>",
            "fields_visible": ["<field1>", "<field2>"]
        }},
        "email": {{
            "available": <boolean>,
            "email_address": "<email to send application or null>",
            "instructions": "<any specific instructions or null>"
        }},
        "additional_application_methods": {{}}
    }},
    "company_name": "<company name or null>",
    "page_title": "<page title or null>"
    
    }}
    

Extract ALL available information from the detailed job posting. Ignore any job listings or "related jobs" sections. Use null for fields not found.
"""

    return prompt




# def create_job_page_analysis_prompt_detail(url: str, text: str) -> str:
#     """
#     Creates the analysis prompt for scraping a single job detail page.
#     Focused exclusively on extracting job details - ignores any job listings on the page.
#     """
    
#     prompt = f"""Analyze this webpage and extract job details if a job posting's full details are visible.

# URL: {url}

# PAGE CONTENT:
# {text}

# ---

# CLASSIFICATION RULES:

# 1. **single_job_posting** - A job's FULL DETAILS are visible on this page
#    - You can see: job description, requirements, responsibilities, salary, how to apply, etc.
#    - The page may ALSO show a list of other jobs - IGNORE the listings
#    - Focus ONLY on extracting the detailed job information
#    - next_action: "scrape_job"

# 2. **not_job_detail_page** - NO job details are visible
#    - Page only shows job listings without any expanded details
#    - Page requires navigation/clicking to see job details
#    - Page is not job-related at all
#    - next_action: "stop"

# IMPORTANT:
# - If the page shows BOTH a detailed job AND a list of other jobs, classify as "single_job_posting"
# - Extract ONLY the job that has full details visible - ignore any job listings/sidebar jobs
# - We want the rich detail content (description, requirements, etc.), not just titles and links

# LOCATION FILTER:
# - Only extract jobs with UK locations, remote positions, or unspecified locations
# - If the detailed job explicitly states a non-UK location, classify as "not_job_detail_page"

# ---

# RESPONSE FORMAT:
# - Return ONLY valid JSON
# - Do NOT wrap in markdown code blocks (no ```json or ```)
# - Do NOT include any text before or after the JSON
# - Start directly with {{ and end with }}

# RESPONSE SCHEMA:

# {{
#     "page_category": "single_job_posting" | "not_job_detail_page",
#     "next_action": "scrape_job" | "stop",
#     "confidence": <float 0.0-1.0>,
#     "reasoning": "<brief explanation of classification decision>",
    
#     "job_details": {{
#         "title": "<job title or null>",
#         "job_url": "{url}",
#         "job_description": "<full job description text or null>",
#         "job_description_word_count": <integer word count or null>,
        
#         "company_name": "<employer/company name or null>",
#         "domain_name": "<website main domain>",
        
#         "contract_type": "<permanent/temporary/fixed-term/contract/freelancer or null>",
#         "job_type": "<full-time/part-time or null>",
#         "hours": "<working hours info or null>",
        
#         "location": "<job location or null>",
#         "location_postcode": "<postcode if mentioned or null>",
#         "remote_option": "<remote/hybrid/on-site or null>",
        
#         "salary": "<salary info as stated or null>",
#         "benefits": "<benefits listed or null>",
#         "holiday": "<holiday/annual leave info or null>",
        
#         "requirements": "<qualifications/requirements or null>",
#         "responsibilities": "<key responsibilities or null>",
        
#         "closing_date": "<application deadline or null>",
#         "interview_date": "<interview date if mentioned or null>",
#         "start_date": "<job start date or null>",
        
#         "key_contact_name": null,
#         "key_contact_email": null,
#         "key_contact_job_title": null
#         }},
        
#     "application_methods": {{
#         "form: {{
#             "available": <boolean>,
#             "form_url": "<url or 'current_page' or null>",
#             "fields_visible": ["<field1>", "<field2>"]
#         }},
#         "email": {{
#             "available": <boolean>,
#             "email_address": "<email to send application or null>",
#             "instructions": "<any specific instructions or null>"
#         }},
#         "additional_application_methods": {{}}
#     }},
#     "company_name": "<company name or null>",
#     "page_title": "<page title or null>"
    
#     }}
    

# Extract ALL available information from the detailed job posting. Ignore any job listings or "related jobs" sections. Use null for fields not found."""

#     return prompt





# def create_job_page_analysis_prompt_detail(url: str, text: str) -> str:
#     """
#     Creates the analysis prompt with embedded response schema.
#     """
    
#     prompt = f"""Analyze this webpage and determine if it contains job listings.

# URL: {url}

# PAGE CONTENT:
# {text}

# ---

# CLASSIFICATION RULES:

# 1. **jobs_listed** - Job postings ARE VISIBLE on this page
#    - You can see job titles with their details
#    - next_action: "scrape_jobs"
#    - Extract ALL jobs into the jobs_listed_on_page array with their job_url

# 2. **navigation_required** - Page indicates jobs exist but you need to click to SEE THE JOB LISTINGS
#    - Example: "View Open Positions" button, "We're hiring! Click here"
#    - This is NOT for clicking to see a job's full description
#    - next_action: "navigate"
#    - Populate next_action_target with where to click

# 3. **single_job_posting** - Detailed page for ONE specific job
#    - Full job description, requirements, responsibilities visible
#    - next_action: "scrape_single_job"
#    - Put the single job in the jobs_listed_on_page array

# 4. **not_job_related** - No job/career/hiring content
#    - next_action: "stop"

# IMPORTANT:
# - "More Info" or "View Details" links next to job titles are NOT navigation - those are job_url for each job
# - Navigation means clicking to FIND WHERE jobs are listed, not to see details of a specific job
# - If you see a list of jobs with "More Info" links, category is "jobs_listed" and those links go in each job's job_url
# - next_action_target points to a link/button where the job listings page is located (only when navigation_required)
# ---

# NOTE: Make sure to return all jobs found on the page, and don't truncate your response
# NOTE: Filter results: include jobs with UK locations or unspecified location(including remote); exclude jobs with non-UK locations.

# RESPONSE FORMAT:
# - Return ONLY valid JSON
# - Do NOT wrap in markdown code blocks (no ```json or ```)
# - Do NOT include any text before or after the JSON
# - Start directly with {{ and end with }}
# - Return the result strictly using the schema below.

# RESPONSE SCHEMA:

# {{
#     "page_category": "jobs_listed" | "navigation_required" | "single_job_posting" | "not_job_related",
#     "next_action": "scrape_jobs" | "navigate" | "scrape_single_job" | "stop",
#     "confidence": <float 0.0-1.0>,
#     "reasoning": "<brief explanation>",
#     "domain_name: "<website main domain>",
#     "url": "<main url>",
    
#     "jobs_listed_on_page":
#         {{
#             "title": "<job title>",
#             "job_url": "<URL to job details/Apply now/More Info page or null>",
#             "path": <path to job listen>",
#             "employer_job_id": null,
#             "date_created_in_db": null,
#             "job_title": null,
#             "job_description": null,
#             "job_description_word_count": null,
#             "contract_type": null,
#             "job_type": null,
#             "hours": null,
#             "location": null,
#             "location_postcode": null,
#             "holiday": null,
#             "salary": null,
#             "benefits": null,
#             "closing_date": null,
#             "interview_date": null,
#             "start_date": null,
#             "non_ats_application_method": null,
#             "key_contact_name": null,
#             "key_contact_email": null,
#             "key_contact_job_title": null

# #         }},
    
#     "application_methods": {{
#         "form: {{
#             "available": <boolean>,
#             "form_url": "<url or 'current_page' or null>",
#             "fields_visible": ["<field1>", "<field2>"]
#         }},
#         "email": {{
#             "available": <boolean>,
#             "email_address": "<email to send application or null>",
#             "instructions": "<any specific instructions or null>"
#         }},
#         "additional_application_methods": {{}}
#     }},

#     "company_name": "<company name or null>",
#     "page_title": "<page title or null>"
# }}

# Extract ALL jobs visible on the page. Include all metadata available for each job."""

#     return prompt

# def create_job_page_analysis_prompt_detail(url: str, text: str) -> str:
#     """
#     Creates the analysis prompt with embedded response schema.
#     """
    
#     prompt = f"""Analyze this webpage and determine if it contains job listings.

# URL: {url}

# PAGE CONTENT:
# {text}

# ---

# CLASSIFICATION RULES:

# 1. **jobs_listed** - Job postings ARE VISIBLE on this page
#    - You can see job titles with their details
#    - next_action: "scrape_jobs"
#    - Extract ALL jobs into the jobs_listed_on_page array with their job_url

# 2. **navigation_required** - Page indicates jobs exist but you need to click to SEE THE JOB LISTINGS
#    - Example: "View Open Positions" button, "We're hiring! Click here"
#    - This is NOT for clicking to see a job's full description
#    - next_action: "navigate"
#    - Populate next_action_target with where to click

# 3. **single_job_posting** - Detailed page for ONE specific job
#    - Full job description, requirements, responsibilities visible
#    - next_action: "scrape_single_job"
#    - Put the single job in the jobs_listed_on_page array

# 4. **not_job_related** - No job/career/hiring content
#    - next_action: "stop"

# IMPORTANT:
# - "More Info" or "View Details" links next to job titles are NOT navigation - those are job_url for each job
# - Navigation means clicking to FIND WHERE jobs are listed, not to see details of a specific job
# - If you see a list of jobs with "More Info" links, category is "jobs_listed" and those links go in each job's job_url
# - next_action_target points to a link/button where the job listings page is located (only when navigation_required)
# ---

# NOTE: Make sure to return all jobs found on the page, and don't truncate your response
# NOTE: Filter results: include jobs with UK locations or unspecified location(including remote); exclude jobs with non-UK locations.

# RESPONSE FORMAT:
# - Return ONLY valid JSON
# - Do NOT wrap in markdown code blocks (no ```json or ```)
# - Do NOT include any text before or after the JSON
# - Start directly with {{ and end with }}
# - Return the result strictly using the schema below.

# RESPONSE SCHEMA:

# {{
#     "page_category": "jobs_listed" | "navigation_required" | "single_job_posting" | "not_job_related",
#     "next_action": "scrape_jobs" | "navigate" | "scrape_single_job" | "stop",
#     "confidence": <float 0.0-1.0>,
#     "reasoning": "<brief explanation>",
#     "domain_name: "<website main domain>",
#     "url": "<main url>",
    
#     "next_action_target": {{
#         "url": "<URL to navigate to or null>",
#         "link_text": "<text of the link/button or null>",
#         "element_type": "link" | "button" | null
#     }},
#     "jobs_listed_on_page":
#         {{
#             "title": "<job title>",
#             "job_url": "<URL to job details/Apply now/More Info page or null>",
#             "path": <path to job listen>",
#             "employer_job_id": null,
#             "date_created_in_db": null,
#             "job_title": null,
#             "job_description": null,
#             "job_description_word_count": null,
#             "contract_type": null,
#             "job_type": null,
#             "hours": null,
#             "location": null,
#             "location_postcode": null,
#             "holiday": null,
#             "salary": null,
#             "benefits": null,
#             "closing_date": null,
#             "interview_date": null,
#             "start_date": null,
#             "non_ats_application_method": null,
#             "key_contact_name": null,
#             "key_contact_email": null,
#             "key_contact_job_title": null

#         }},
    
#     "application_methods": {{
#         "form: {{
#             "available": <boolean>,
#             "form_url": "<url or 'current_page' or null>",
#             "fields_visible": ["<field1>", "<field2>"]
#         }},
#         "email": {{
#             "available": <boolean>,
#             "email_address": "<email to send application or null>",
#             "instructions": "<any specific instructions or null>"
#         }},
#         "additional_application_methods": {{}}
#     }},
    
#     "pagination": {{
#         "has_more_pages": <boolean>,
#         "next_page_url": "<URL or null>",
#         "total_pages": <integer or null>,
#         "total_jobs": <integer or null>,
#         "current_page": <integer or null>
#     }},
    
#     "company_name": "<company name or null>",
#     "page_title": "<page title or null>"
# }}

# Extract ALL jobs visible on the page. Include all metadata available for each job."""

#     return prompt



def create_job_page_analysis_prompt_rag(url: str, text: str) -> str:
    """
    Creates the analysis prompt with embedded response schema.
    """
    
    prompt = f"""Analyze this webpage and determine if it contains job listings.

URL: {url}

PAGE CONTENT:
{text}

---

CLASSIFICATION RULES:

1. **jobs_listed** - Job postings ARE VISIBLE on this page
   - You can see job titles with their details
   - next_action: "scrape_jobs"
   - Extract ALL jobs into the jobs_listed_on_page array with their job_url

2. **navigation_required** - Page indicates jobs exist but you need to click to SEE THE JOB LISTINGS
   - Example: "View Open Positions" button, "We're hiring! Click here"
   - This is NOT for clicking to see a job's full description
   - next_action: "navigate"
   - Populate next_action_target with where to click

3. **single_job_posting** - Detailed page for ONE specific job
   - Full job description, requirements, responsibilities visible
   - next_action: "scrape_single_job"
   - Put the single job in the jobs_listed_on_page array

4. **not_job_related** - No job/career/hiring content
   - next_action: "stop"

IMPORTANT:
- "More Info" or "View Details" links next to job titles are NOT navigation - those are job_url for each job
- Navigation means clicking to FIND WHERE jobs are listed, not to see details of a specific job
- next_action_target points to a link/button where the job listings page is located (only when navigation_required)
---


RESPONSE SCHEMA:

{{
    "page_category": "jobs_listed" | "navigation_required" | "single_job_posting" | "not_job_related",
    "next_action": "scrape_jobs" | "navigate" | "scrape_single_job" | "stop",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief explanation>",
    "domain_name: "<website main domain>",
    "url": "<main url>",
    
    "next_action_target": {{
        "url": "<URL to navigate to or null>",
        "link_text": "<text of the link/button or null>",
        "element_type": "link" | "button" | null
    }},
    "pagination": {{
        "has_more_pages": <boolean>,
        "next_page_url": "<URL or null>",
        "total_pages": <integer or null>,
        "total_jobs": <integer or null>,
        "current_page": <integer or null>
    }},
    
    "company_name": "<company name or null>",
    "page_title": "<page title or null>"
}}

Extract ALL jobs visible on the page. Include all metadata available for each job."""

    return prompt





job_classification_prompt = """
You are analysing the extracted content of a webpage.

Classify the page into exactly one of the following categories:

- not_job_related
- career_info_only
- job_navigation_page
- job_listings_preview_page
- job_listings_page
- job_detail_page

Definitions:
- not_job_related: The page contains no job or career-related content.
- career_info_only: The page discusses careers, culture, or working at the company but shows no job openings.
- job_navigation_page: The page discusses jobs or careers but shows no job listings and requires clicking a link or button to reach job listings.
- job_listings_preview_page: The page displays a limited or featured subset of job listings but requires navigation to another page to view all available jobs.
- job_listings_page: The page displays a full list of job openings directly on the page.
- job_detail_page: The page displays detailed information about one specific job.

Rules:
- Choose exactly one category.
- If the page shows some jobs AND also includes a link or button to view all jobs, classify it as job_listings_preview_page.
- If the page is classified as job_navigation_page or job_listings_preview_page, identify the primary link or button used to navigate to the full job listings and provide its visible text and destination URL.
- If the page is classified as job_listings_page, determine whether the page uses a load-more button, infinite scroll, or pagination links to show additional jobs.
- For all other page categories, navigation and pagination fields must be null.

Return the result strictly using the provided schema.
"""
