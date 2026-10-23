"""Shared Nvoids job-detail link detection helpers."""

import re


# Nvoids has used both routes over time. Keep the old route supported while
# accepting the current abbreviated route as well.
NVOIDS_JOB_DETAIL_URL_RE = re.compile(
    r"(?:^|/)\b(?:job_details|j_details)\.jsp(?:[?#]|$)",
    re.IGNORECASE,
)

# These selectors are consumed by Selenium/XPath and browser-side JavaScript.
NVOIDS_JOB_DETAIL_XPATH = (
    "//a[contains(@href, 'job_details.jsp') or contains(@href, 'j_details.jsp')]"
)
NVOIDS_JOB_DETAIL_CSS_SELECTOR = (
    "a[href*='job_details.jsp'], a[href*='j_details.jsp']"
)


def is_nvoids_job_detail_url(url: str) -> bool:
    """Return whether *url* points to a Nvoids job-detail page."""
    return bool(NVOIDS_JOB_DETAIL_URL_RE.search(str(url or "")))
