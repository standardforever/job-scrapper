
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

# =============================================================================
# ATS Detector
# =============================================================================




@dataclass
class ATSDetectionResult:
    is_ats: bool
    is_external_application: bool
    is_known_ats: bool
    ats_provider: Optional[str]
    job_domain: str
    company_domain: str
    detection_reason: str


class ATSDetector:
    KNOWN_ATS_DOMAINS = frozenset({
        "allhires.com",
        "amris.com",
        "earcu.com",
        "ashbyhq.com",
        "avature.net",
        "bamboohr.com",
        "beapplied.com",
        "brassring.com",
        "breezy.hr",
        "brighthr.com",
        "bullhorn.com",
        "candidatemanager.net",
        "changeworknow.co.uk",
        "ciphr.com",
        "civica.com",
        "cloudonlinerecruitment.co.uk",
        "cohesionrecruitment.com",
        "cornerstoneondemand.com",
        "cvminder.co.uk",
        "cvmail.net",
        "darwinbox.com",
        "dayforcehcm.com",
        "eightfold.ai",
        "employmenthero.com",
        "havaspeople.com",
        "eploy.com",
        "eteach.com",
        "factorialhr.com",
        "firefishsoftware.com",
        "fourth.com",
        "gohire.io",
        "greenhouse.com",
        "greenhouse.io",
        "groupgti.com",
        "harbourats.com",
        "harri.com",
        "healthboxhr.com",
        "hibob.com",
        "hirebridge.com",
        "hirehive.com",
        "hireroad.com",
        "hireserve.com",
        "icims.com",
        "inploi.com",
        "webitrent.com",
        "jazzhr.com",
        "jobtrain.co.uk",
        "jobadder.com",
        "jobvite.com",
        "kallidus.com",
        "lever.co",
        "logicmelon.com",
        "lumesse-engage.com",
        "manatal.com",
        "mynewterm.com",
        "workday.com",
        "myworkdayjobs.com",
        "networxrecruitment.com",
        "iris.co.uk",
        "occupop.com",
        "cezannehr.com",
        "oleeo.com",
        "oraclecloud.com",
        "oracleoutsourcing.com",
        "pageuppeople.com",
        "peoplehr.com",
        "personio.com",
        "personio.de",
        "pinpointhq.com",
        "reach-ats.com",
        "recruitgenie.co.uk",
        "recruitee.com",
        "recruiterbox.com",
        "recruiterflow.com",
        "recruitive.com",
        "seemehired.com",
        "occy.com",
        "smartrecruiters.com",
        "staffsavvy.com",
        "successfactors.eu",
        "successfactors.com",
        "cegid.com",
        "talos360.co.uk",
        "teamtailor.com",
        "tes.com",
        "tribepad.com",
        "trac.jobs",
        "ultipro.com",
        "vacancyfiller.co.uk",
        "webrecruit.co",
        "workable.com",
        "adp.com",
        "zoho.com",
        "applytojob.com",
        "recruitingbypaycor.com",
        "paylocity.com",
        "paycomonline.net",
        "applicantpro.com",
        "hrmdirect.com",
        "clearcompany.com",
        "talentreef.com",
    })

    @classmethod
    def extract_base_domain(cls, url: str) -> str:
        """Extract base domain (e.g., 'example.com' from 'jobs.example.com')."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            parts = domain.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return domain
        except Exception:
            return ""

    @classmethod
    def extract_full_domain(cls, url: str) -> str:
        """Extract full domain including subdomains."""
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""

    @classmethod
    def find_matching_ats(cls, url: str) -> Optional[str]:
        """Find matching ATS provider from known list."""
        base_domain = cls.extract_base_domain(url)
        full_domain = cls.extract_full_domain(url)

        for ats_domain in cls.KNOWN_ATS_DOMAINS:
            if base_domain == ats_domain:
                return ats_domain
            if full_domain.endswith(f".{ats_domain}"):
                return ats_domain
            if full_domain == ats_domain:
                return ats_domain

        return None

    @classmethod
    def is_same_domain(cls, url1: str, url2: str) -> bool:
        """Check if two URLs belong to the same domain."""
        domain1 = cls.extract_base_domain(url1)
        domain2 = cls.extract_base_domain(url2)
        return domain1 == domain2 and domain1 != ""

    @classmethod
    def detect_ats(cls, job_url: str, company_domain: str) -> dict[str, Any]:
        """
        Detect if a job URL is using an ATS.
        
        Detection logic:
        1. If job URL domain is in KNOWN_ATS_DOMAINS → Confirmed ATS
        2. If job URL domain differs from company domain → External (likely ATS)
        3. If same domain → Internal application
        
        Args:
            job_url: The job application/listing URL
            company_domain: The company's main domain (e.g., "openai.com")
            
        Returns:
            Dictionary with ATS detection results
        """
        # Normalize company domain (handle both "openai.com" and "https://openai.com")
        if company_domain.startswith("http"):
            company_domain_clean = cls.extract_base_domain(company_domain)
        else:
            company_domain_clean = company_domain.lower().replace("www.", "")
            parts = company_domain_clean.split(".")
            if len(parts) >= 2:
                company_domain_clean = ".".join(parts[-2:])

        job_domain = cls.extract_base_domain(job_url)
        job_full_domain = cls.extract_full_domain(job_url)

        # Check if domains match
        is_external = job_domain != company_domain_clean

        # Check if it's a known ATS
        known_ats_provider = cls.find_matching_ats(job_url)
        is_known_ats = known_ats_provider is not None

        # Determine if it's an ATS:
        # - Confirmed ATS if domain is in known list
        # - Likely ATS if external domain (different from company)
        is_ats = is_known_ats or is_external

        # Determine ATS provider:
        # - Known ATS: use the matched ATS domain
        # - External unknown: use the job domain as provider
        # - Internal: None
        if is_known_ats:
            ats_provider = known_ats_provider
        elif is_external:
            ats_provider = job_domain  # Return external domain as provider
        else:
            ats_provider = None

        # Determine detection reason
        if is_known_ats:
            reason = f"Known ATS provider: {known_ats_provider}"
        elif is_external:
            reason = f"External domain ({job_domain}) differs from company ({company_domain_clean})"
        else:
            reason = "Internal application on company domain"

        return {
            "is_ats": is_ats,
            "is_external_application": is_external,
            "is_known_ats": is_known_ats,
            "ats_provider": ats_provider,
            "job_domain": job_domain,
            "company_domain": company_domain_clean,
            "detection_reason": reason,
    }

    @classmethod
    def detect_ats_batch(cls, job_urls: list[str], company_domain: str) -> list[dict[str, Any]]:
        """
        Detect ATS for multiple job URLs.
        
        Args:
            job_urls: List of job URLs to check
            company_domain: The company's main domain
            
        Returns:
            List of detection results for each URL
        """
        return [cls.detect_ats(url, company_domain) for url in job_urls]

    @classmethod
    def filter_ats_jobs(cls, job_urls: list[str], company_domain: str) -> dict[str, list[str]]:
        """
        Separate job URLs into ATS and internal categories.
        
        Args:
            job_urls: List of job URLs
            company_domain: The company's main domain
            
        Returns:
            Dictionary with 'ats' and 'internal' URL lists
        """
        result = {
            "ats": [],
            "internal": [],
            "known_ats": [],
            "external_unknown": [],
        }

        for url in job_urls:
            detection = cls.detect_ats(url, company_domain)

            if detection["is_known_ats"]:
                result["known_ats"].append(url)
                result["ats"].append(url)
            elif detection["is_external_application"]:
                result["external_unknown"].append(url)
                result["ats"].append(url)
            else:
                result["internal"].append(url)

        return result