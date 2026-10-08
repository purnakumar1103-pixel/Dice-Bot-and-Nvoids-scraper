"""
NvoidsScraper – Stealth edition.

Anti-bot measures implemented:
1. Hides the navigator.webdriver flag (Chrome's automation fingerprint).
2. Removes the "enable-automation" infobar Chrome normally shows.
3. Uses a realistic User-Agent string.
4. Types into fields character-by-character with random human-speed delays.
5. Adds random micro-pauses between every major action (click, navigate, etc).
6. Waits for elements to be CLICKABLE (not just present) before interacting.
7. Scrolls to elements before clicking — just like a real user would.
8. Waits for the page to reach a stable 'interactive' DOM state before
   extracting text, rather than just waiting for <body> to exist.
"""

import re
import os
import json
import time
import random
import traceback
import platform
import subprocess
from datetime import datetime, timedelta, timezone

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

import undetected_chromedriver as uc

from core.outreach.scraper_engine import BaseScraper
from core.outreach.ai_extractor import AIExtractor
from core.learning_engine import LearningEngine

# ── Constants ────────────────────────────────────────────────────────────────

_IGNORED_EMAIL_PATTERNS = re.compile(
    r'(noreply|no-reply|support|admin|info|contact|webmaster|postmaster|nvoids\.com)@|@nvoids\.com',
    re.IGNORECASE
)


def _normalize_session_title(title: str) -> str:
    """
    Produce a compact, noise-free title string for session-level dedup.
    Strips work-mode tags, location fragments, C2C/W2 noise, delimiters,
    and punctuation so that semantically identical roles compare equal
    even when their Nvoids titles differ slightly.

    Examples
    --------
    "Data Engineer – Austin, TX (Hybrid) || W2 Only"  →  "data engineer"
    "AWS Data Engineer | Remote | Direct Client"       →  "aws data engineer"
    """
    t = str(title or "").lower()
    # Strip experience requirement noise
    t = re.sub(r'\b\d+\+\s*(?:years?|yrs?|yr)?\s*(?:of)?\s*(?:exp(?:erience)?)?\b', '', t)
    t = re.sub(r'\b\d+\s*[\+\-]\s*(?:years?|yrs?|yr)\s*(?:of)?\s*(?:exp(?:erience)?)?\b', '', t)
    t = re.sub(r'\b\d+\s+(?:years?|yrs?|yr)\s+(?:of\s+)?exp(?:erience)?\b', '', t)
    t = re.sub(r'\b\d+\+\b', '', t)
    # Split on common Nvoids delimiters and take the first meaningful segment
    t = re.split(r'\|+|\u2013|-{2,}', t)[0]
    # Remove noise tokens
    noise = (
        r'\b(remote|hybrid|onsite|on-site|c2c|w2|local|urgent|'
        r'direct\s+client|contract|fulltime|full[\s-]time|part[\s-]time|'
        r'need\s+local|visa|gc|citizen)\b'
    )
    t = re.sub(noise, '', t)
    # Strip city/state patterns like "austin, tx" \u2014 use lowercase pattern since t is already lowercased (#16)
    t = re.sub(r'\b[a-z][a-z]+(?:[\s-][a-z][a-z]+)?,?\s*[a-z]{2}\b', '', t)
    # Strip leftover punctuation and collapse whitespace
    t = re.sub(r'[^\w\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

_DATE_FORMATS = [
    "%I:%M %p %d-%b-%y",
    "%H:%M %d-%b-%y",
    "%I:%M%p %d-%b-%y",
    "%d-%b-%y %I:%M %p",
    "%d-%b-%y %I:%M%p",
    "%d-%b-%y %H:%M",
]

IST = timezone(timedelta(hours=5, minutes=30))

# ── Per-query yield stats (drives best-queries-first ordering) ──────────────

# Absolute path — resolves to <project_root>/data/query_yield.json regardless of CWD (#20)
_QUERY_STATS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "query_yield.json"
)


def _load_query_stats() -> dict:
    try:
        with open(_QUERY_STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_query_stats(stats: dict):
    try:
        os.makedirs(os.path.dirname(_QUERY_STATS_FILE), exist_ok=True)
        with open(_QUERY_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
    except Exception:
        pass


# ── Auto-pause persistently zero-yield queries ──────────────────────────────
# A search term that has found ~0 jobs across many recent runs is very likely
# to keep finding 0 (dead/niche term for this site right now). Skipping it
# most of the time — but still trying it occasionally in case new postings
# start appearing — saves a full search-and-scan pass for no benefit, without
# ever permanently blinding the bot to that term.
_DEAD_QUERY_MIN_RUNS = 5            # need this much history before judging a query "dead"
_DEAD_QUERY_FOUND_THRESHOLD = 0.5   # EMA of jobs FOUND below this = dead
_DEAD_QUERY_REVISIT_EVERY = 5       # still run it once every N would-be-skips


# ── Human-behaviour helpers ──────────────────────────────────────────────────

def _human_pause(low: float = 0.3, high: float = 0.8):
    """Sleep for a random interval to mimic human reaction time."""
    time.sleep(random.uniform(low, high))


def _human_type(element, text: str):
    """
    Type with human rhythm: initial hesitation, burst typing, slower keys
    after word boundaries, and occasional mid-word pauses. A flat per-char
    delay distribution is itself a bot signature.
    """
    time.sleep(random.uniform(0.3, 0.9))             # "thinking" pause before first key
    for char in text:
        element.send_keys(char)
        if char == ' ':
            time.sleep(random.uniform(0.08, 0.25))   # slower after word boundaries
        elif random.random() < 0.06:
            time.sleep(random.uniform(0.25, 0.7))    # occasional mid-word hesitation
        else:
            time.sleep(random.uniform(0.03, 0.12))   # normal burst typing


def _scroll_to(driver, element):
    """Scroll the element into the centre of the viewport before clicking."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        element
    )
    time.sleep(random.uniform(0.3, 0.7))


def _human_read_scroll(driver):
    """Scroll down the page in randomized steps like a person reading, then jump back up."""
    try:
        total = driver.execute_script("return document.body.scrollHeight") or 0
        if total <= 900:
            return
        pos = 0
        while pos < total - 600 and random.random() > 0.05:
            step = random.randint(250, 700)
            pos += step
            driver.execute_script(f"window.scrollTo(0, {pos});")
            time.sleep(random.uniform(0.25, 0.9))
            if random.random() < 0.12:                 # occasional re-read scroll-up
                pos = max(0, pos - random.randint(100, 300))
                driver.execute_script(f"window.scrollTo(0, {pos});")
                time.sleep(random.uniform(0.2, 0.5))
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(random.uniform(0.2, 0.5))
    except Exception:
        pass


def _stealth_click(driver, element):
    """
    Move the mouse to the element first, then click.
    This triggers real mouseover/mousemove events that bots usually skip.
    """
    _scroll_to(driver, element)
    ActionChains(driver).move_to_element(element).pause(
        random.uniform(0.2, 0.5)
    ).click().perform()


# ── Date / email helpers ─────────────────────────────────────────────────────

def _parse_nvoids_date(text: str):
    # Collapse whitespace but preserve original case — lowercasing breaks %p (AM/PM) on Windows (#19)
    text = re.sub(r'\s+', ' ', text.strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    # Second pass: try uppercased text to handle sources that emit "am"/"pm" in lowercase
    text_upper = text.upper()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text_upper, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


# Shared regex for Nvoids date strings (reused by _is_fresh and _extract_post_date)
_DATE_PATTERN = re.compile(
    r'\d{1,2}:\d{2}\s*[aApP][mM]\s+\d{2}-[a-zA-Z]{3}-\d{2}'
    r'|\d{1,2}:\d{2}\s+\d{2}-[a-zA-Z]{3}-\d{2}'
    r'|\d{2}-[a-zA-Z]{3}-\d{2}\s+\d{1,2}:\d{2}\s*[aApP][mM]'
    r'|\d{2}-[a-zA-Z]{3}-\d{2}\s+\d{1,2}:\d{2}',
    re.IGNORECASE
)


def _extract_post_date(text: str):
    """
    Return the most recent datetime found in *text* using the standard
    Nvoids date pattern, or None if no recognisable date is present.
    This is the authoritative "posted date" for a job listing.
    """
    matches = _DATE_PATTERN.findall(text)
    parsed = [d for m in matches for d in [_parse_nvoids_date(m)] if d]
    return max(parsed) if parsed else None


def _is_fresh(body_text: str, max_hours: int = 24) -> bool:
    dt = _extract_post_date(body_text)
    if dt is None:
        return True  # No date found → assume fresh
    age_hours = (datetime.now(timezone.utc).astimezone(IST) - dt).total_seconds() / 3600
    return age_hours <= max_hours


def _extract_recruiter_emails(body_text: str) -> list:
    raw = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', body_text)
    seen, clean = set(), []
    for e in raw:
        el = e.lower()
        if any(el.endswith(x) for x in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg')):
            continue
        if _IGNORED_EMAIL_PATTERNS.search(el):
            continue
        if el not in seen:
            seen.add(el)
            clean.append(e)
            
    # Prioritize corporate domains over generic webmail providers
    generic_domains = ('@gmail.', '@yahoo.', '@hotmail.', '@outlook.', '@aol.', '@live.', '@icloud.')
    
    def sort_key(email):
        email_lower = email.lower()
        is_generic = any(gen in email_lower for gen in generic_domains)
        return (1 if is_generic else 0, email_lower)
        
    clean.sort(key=sort_key)
    return clean


def _extract_phone(body_text: str) -> str:
    # Matches common US phone formats with optional extensions, allowing multiple spaces/dashes
    pattern = re.compile(
        r'(?:\+?1[\s.-]*)?\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}(?:\s*(?:ext|x|extension|extn)\.?\s*\d{1,5})?',
        re.IGNORECASE
    )
    matches = pattern.findall(body_text)
    if matches:
        return matches[0].strip()
    return ""


def _extract_keywords(body_text: str) -> str:
    """Finds common tech keywords in the job description using synonym-aware matching."""
    tech_keywords = [
        # Cloud
        "aws", "azure", "gcp", "cloud",
        # AI / ML / GenAI
        "gen ai", "llm", "rag", "agentic ai", "machine learning", "deep learning",
        "nlp", "computer vision", "mlops", "openai", "langchain", "pytorch", "tensorflow",
        "hugging face", "vertex ai", "sagemaker", "bedrock", "mlflow",
        "model registry", "feature store", "inference",
        # Data & ETL
        "python", "sql", "pyspark", "spark", "scala", "java",
        "etl", "dbt", "airflow", "glue", "nifi", "informatica",
        "databricks", "snowflake", "redshift", "bigquery", "synapse",
        "hadoop", "hive", "kafka", "kinesis", "flink", "alteryx",
        "pandas", "numpy", "polars", "delta live tables",
        "lakehouse", "medallion", "adls", "data factory", "data catalog", "data lineage",
        # Infra & DevOps
        "kubernetes", "docker", "terraform", "ansible", "ci/cd",
        "jenkins", "github actions", "devops", "azure devops", "collibra",
        # Storage & BI
        "postgres", "mysql", "mongodb", "cassandra", "elasticsearch",
        "power bi", "tableau", "looker", "quicksight",
        # Compliance
        "hipaa", "gdpr", "sox",
    ]

    text_lower = body_text.lower()
    found = []
    seen = set()

    for kw in tech_keywords:
        if _skill_matches(kw, text_lower):
            display = kw.upper() if len(kw) <= 3 else kw.title()
            if display.lower() not in seen:
                seen.add(display.lower())
                found.append(display)

    # Cap at 6 most relevant keywords to keep subject line readable
    return ", ".join(found[:6])


def _extract_location_from_title(title: str) -> str:
    """
    Extract location from the raw Nvoids job title string.
    Nvoids titles frequently embed location directly, e.g.:
      "AWS Data Engineer - Saint Louis, MO (Hybrid) || Remote || W2"
    This is more reliable than parsing the freeform body text.
    """
    # Priority 1: explicit City, ST pattern in the title
    city_st = re.search(
        r'\b([A-Z][a-z]+(?:[\s-][A-Z][a-z]+)*),\s*([A-Z]{2})\b',
        title
    )
    if city_st:
        city = city_st.group(1).strip()
        state = city_st.group(2).strip()
        # Optionally append Remote/Hybrid qualifier if present
        qualifier = re.search(
            r'\b(Remote|Hybrid|Onsite|On-site)\b', title, re.IGNORECASE
        )
        suffix = f" ({qualifier.group(1).title()})" if qualifier else ""
        return f"{city}, {state}{suffix}"

    # Priority 2: work-mode keywords when no city is present
    if re.search(r'\bFully\s+Remote\b', title, re.IGNORECASE):
        return "Remote"
    if re.search(r'\bRemote\b', title, re.IGNORECASE):
        return "Remote"
    if re.search(r'\bHybrid\b', title, re.IGNORECASE):
        return "Hybrid"

    return ""


def _extract_location(body_text: str) -> str:
    """Extract a City/State location string from the job description body (fallback)."""
    # 1. Explicit label: "Location: Austin, TX" or "Location: Remote"
    label_match = re.search(
        r'(?:location|loc|city|job\s+location)\s*[:\-]\s*([A-Za-z ]+(?:,\s*[A-Z]{2})?(?:\s*\((?:Remote|Hybrid|Onsite)\))?)',
        body_text, re.IGNORECASE
    )
    if label_match:
        loc = label_match.group(1).strip().split('\n')[0].strip()
        if 2 < len(loc) < 60:
            return loc

    # 1.5 Nvoids header line: "<job title> at City, State, USA"
    # (full state names like "California" don't match the City, ST pattern below)
    at_match = re.search(
        r'\bat\s+([A-Za-z][A-Za-z .\-]*,\s*[A-Za-z][A-Za-z .\-]*?),?\s+USA\b',
        body_text
    )
    if at_match:
        loc = at_match.group(1).strip()
        if loc.lower().startswith('remote'):
            return "Remote"
        if 2 < len(loc) < 60:
            return loc

    # 2. Work-mode standalone
    if re.search(r'\bFully\s+Remote\b', body_text, re.IGNORECASE):
        return "Remote"
    if re.search(r'\bRemote\b', body_text, re.IGNORECASE):
        return "Remote"
    if re.search(r'\bHybrid\b', body_text, re.IGNORECASE):
        return "Hybrid"

    # 3. City, ST pattern
    city_st = re.search(
        r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*([A-Z]{2})\b',
        body_text
    )
    if city_st:
        return f"{city_st.group(1)}, {city_st.group(2)}"

    return ""


_SYNONYM_FAMILIES = {
    "ai": ["ai", "ml", "machine learning", "llm", "genai", "generative ai", "nlp", "computer vision", "data science"],
    "data": ["data", "etl", "big data", "hadoop", "spark", "database"],
    "cloud": ["cloud", "aws", "azure", "gcp"],
    "engineer": ["engineer", "developer", "architect", "scientist", "programmer"],
    "analyst": ["analyst", "analytics", "analysis"],
    "python": ["python", "py", "pyspark", "django", "fastapi", "flask"],
    "sql": ["sql", "t-sql", "pl/sql", "postgres", "postgresql", "mysql", "snowflake"],
    "aws": ["aws", "amazon web services", "s3", "redshift", "glue", "emr", "athena"],
    "azure": ["azure", "adf", "synapse", "databricks", "adls"],
}

def _has_unnegated_match(pattern: str, text: str) -> bool:
    """
    True only if `pattern` appears in `text` in a NON-negated context.
    "no clearance required" / "clearance not needed" must not count as a
    'clearance' hit — otherwise we'd exclude exactly the jobs we want.
    """
    neg_before = re.compile(r"(?:\bno\b|\bnot\b|\bwithout\b|\bdon'?t\b|\bnon[- ]?)\s*(?:\w+\s+){0,2}$")
    neg_after  = re.compile(r"^\s*(?:is\s+)?(?:not|isn'?t)\s+(?:required|needed|mandatory|necessary)")
    for m in re.finditer(pattern, text):
        prefix = text[max(0, m.start() - 30):m.start()]
        if neg_before.search(prefix):
            continue
        suffix = text[m.end():m.end() + 30]
        if neg_after.match(suffix):
            continue
        return True
    return False


def _query_keywords(query: str) -> list:
    return [w.lower() for w in query.split() if len(w) > 1]

def _title_matches_query(title: str, query: str) -> bool:
    """
    Smart title matcher that uses synonym families.
    If the user searches 'AI Engineer', we extract ['ai', 'engineer'].
    We then check if the title contains ANY word from the 'ai' family AND ANY word from the 'engineer' family.
    """
    title_lower = title.lower()
    query_words = _query_keywords(query)
    
    if not query_words:
        return True
        
    for qw in query_words:
        family = []
        for fam_list in _SYNONYM_FAMILIES.values():
            if qw in fam_list:
                family.extend(fam_list)
                
        if not family:
            family = [qw] # fallback to the exact word
            
        matched_family = False
        for syn in family:
            if re.search(r'\b' + re.escape(syn) + r'\b', title_lower):
                matched_family = True
                break
        
        if not matched_family:
            return False
            
    return True


def _skill_matches(skill: str, text_lower: str) -> bool:
    """
    Advanced skill matching with plural tolerance, flexible spacing/hyphens,
    and automatic AI/Data engineering synonym expansions.
    """
    skill_clean = skill.strip().lower()
    if not skill_clean:
        return False
        
    # Build robust regex for the exact skill
    # e.g., "power bi" -> r'\bpower[\s\-]*bis?\b'
    escaped = re.escape(skill_clean).replace(r'\ ', r'[\s\-]*')
    if skill_clean[-1].isalpha() and not skill_clean.endswith('s'):
        escaped += r's?'
    pattern = r'\b' + escaped + r'\b'
    if re.search(pattern, text_lower):
        return True
        
    # AI/Data synonym map for common variations
    synonyms = {
        "gen ai": ["genai", "generative ai", "gen-ai", "generative artificial intelligence"],
        "llm": ["llms", "large language model", "large language models", "foundation model"],
        "rag": ["retrieval augmented generation", "retrieval-augmented"],
        "agentic ai": ["ai agent", "ai agents", "agentic workflow", "agentic llm", "agentic framework"],
        "power bi": ["powerbi", "power-bi"],
        "sage maker": ["sagemaker", "aws sagemaker"],
        "snow flake": ["snowflake", "snow-flake"],
        "dbt": ["data build tool"],
        "dlt": ["delta live tables"],
        "adls": ["azure data lake", "data lake storage", "adls gen2"],
        "gcp": ["google cloud platform", "google cloud"],
        "aws": ["amazon web services"],
        "etl": ["elt", "data pipeline", "data pipelines"],
        "nlp": ["natural language processing"],
        "ml": ["machine learning"],
        "ai": ["artificial intelligence"],
    }
    
    for syn in synonyms.get(skill_clean, []):
        syn_esc = re.escape(syn).replace(r'\ ', r'[\s\-]*')
        if syn[-1].isalpha() and not syn.endswith('s'):
            syn_esc += r's?'
        if re.search(r'\b' + syn_esc + r'\b', text_lower):
            return True
            
    return False


# ── Main class ───────────────────────────────────────────────────────────────

class NvoidsScraper(BaseScraper):

    def __init__(
        self,
        queries: str,
        limit: int,
        pipeline,
        skip_email: bool = False,
        log_callback=None,
        stats_callback=None,
        max_age_hours: int = 24,
        continuous_loop: bool = False,
        rest_time_mins: int = 5,
        groq_api_key: str = "",
        my_core_skills: str = "",
        min_match_score: int = 0,
        exclude_keywords: str = "",
        always_accept_titles: str = "",
        headless: bool = False,
        email_recontact_days: float = 2.0,
    ):
        # We split by comma and support optional limits e.g. "Data Engineer:50, AI Engineer:10"
        raw_queries = [q.strip() for q in queries.split(",") if q.strip()]
        self.limit        = int(limit)
        
        self.parsed_queries = []
        for q in raw_queries:
            if ":" in q:
                parts = q.split(":")
                try:
                    q_limit = int(parts[-1].strip())
                    q_str = ":".join(parts[:-1]).strip()
                    self.parsed_queries.append((q_str, q_limit))
                except ValueError:
                    self.parsed_queries.append((q, self.limit))
            else:
                self.parsed_queries.append((q, self.limit))
        self.pipeline     = pipeline
        self.skip_email   = skip_email
        self.log          = log_callback if log_callback else print
        self.stats_callback = stats_callback
        self.max_hours    = max_age_hours if max_age_hours > 0 else float('inf')
        self.continuous_loop = continuous_loop
        self.rest_time_mins = rest_time_mins
        self.my_core_skills = [s.strip().lower() for s in my_core_skills.split(",") if s.strip()]
        self.min_match_score = min_match_score
        self.exclude_keywords = [k.strip().lower() for k in exclude_keywords.split(",") if k.strip()]
        # Always-accept title phrases: each entry is a phrase whose words must ALL appear
        # in the job title for the job to bypass the skill match score filter entirely.
        # Stored as a list of word-lists for fast matching.
        self._parse_always_accept(always_accept_titles)
        self.headless     = headless
        # Recruiters become re-contactable after this many days (vendors
        # repost the same roles every few days — a permanent ban skips them all)
        self.email_recontact_days = max(0.5, float(email_recontact_days or 2.0))
        # Adaptive freshness: later continuous-loop cycles shrink the window
        # to "time since last cycle + 1h" (set per-cycle in _run_scraper)
        self._effective_max_hours = self.max_hours
        self._last_cycle_start = None
        self.driver       = None
        self.stop_flag    = False
        self.pause_flag   = False
        self.skip_flag    = False   # ← set True to skip the current job and move to the next

        # Groq AI extractor — lazily used as fallback when regex returns empty
        groq_key = groq_api_key or ""
        self._ai = AIExtractor(groq_key, log_callback=self.log) if groq_key else None
        if self._ai:
            self.log("[Nvoids] ✅ Groq AI extractor enabled (fallback for location/keywords).")
        else:
            self.log("[Nvoids] ℹ️  No Groq API key — using regex-only extraction.")

        # -- RAG Skill Ranker -- ranks YOUR skills by relevance to each JD --
        # Falls back silently if sentence-transformers is not installed.
        try:
            from core.outreach.skill_ranker import SkillRanker
            if self.my_core_skills:
                self._skill_ranker = SkillRanker(
                    my_skills=self.my_core_skills,
                    log_callback=self.log,
                )
            else:
                self._skill_ranker = None
                self.log("[Nvoids] No core skills configured -- RAG ranker disabled.")
        except Exception as _sr_err:
            self._skill_ranker = None
            self.log(f"[Nvoids] WARNING: SkillRanker init failed: {_sr_err}")

        # Persistent cross-session email dedup
        try:
            self._dedup_db = LearningEngine()
            email_stats = self._dedup_db.get_email_stats()
            self.log(f"[Nvoids] 📧 Persistent email dedup loaded: {email_stats['total']} recruiter(s) already contacted in past sessions.")
        except Exception as e:
            self._dedup_db = None
            self.log(f"[Nvoids] ⚠️ Could not load email dedup DB: {e}")

    def _parse_always_accept(self, raw: str):
        """
        Parse the always_accept_titles string into a list of word-sets.
        Each comma-separated phrase becomes a frozenset of its lowercase words.
        A job title matches if it contains ALL words in any one phrase.

        e.g. "Data Operational Engineer, AI Platform" →
             [frozenset({'data','operational','engineer'}), frozenset({'ai','platform'})]
        """
        self.always_accept_titles: list = []
        raw_str = raw if isinstance(raw, str) else ""
        for phrase in raw_str.split(","):
            phrase = phrase.strip()
            if not phrase:
                continue
            words = frozenset(w.lower() for w in phrase.split() if len(w) > 1)
            if words:
                self.always_accept_titles.append(words)

    def _is_always_accept(self, title: str) -> str:
        """
        Return the matching phrase (for logging) if title matches any always-accept entry,
        else return empty string.
        """
        title_lower = title.lower()
        for word_set in self.always_accept_titles:
            if all(re.search(r'\b' + re.escape(w) + r'\b', title_lower) for w in word_set):
                return " ".join(sorted(word_set))
        return ""

    def _check_pause(self):
        while self.pause_flag and not self.stop_flag:
            time.sleep(1)

    def _is_driver_alive(self) -> bool:
        """Return False if the ChromeDriver process has died."""
        try:
            _ = self.driver.title   # Any command will fail if driver is dead
            return True
        except Exception:
            return False

    def _restart_driver(self):
        """Quit the dead driver (best-effort) and launch a fresh one."""
        self.log("[Nvoids] ⚠ Driver connection lost. Restarting browser...")
        try:
            self.driver.quit()
        except Exception:
            pass
        time.sleep(3)
        try:
            self.driver = self._build_driver()
            self.log("[Nvoids] ✅ Browser restarted successfully.")
        except Exception as e:
            self.log(f"[Nvoids] ❌ Could not restart browser: {e}")
            raise


    def start(self):
        """Run synchronously in the calling (background) thread."""
        self._run_scraper()

    # ── Browser setup ────────────────────────────────────────────────────────

    def _make_uc_options(self):
        """Builds a fresh uc.ChromeOptions() — undetected_chromedriver marks the
        options object as consumed after one use, so retries need a new one."""
        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")

        if self.headless:
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            # Set custom stealth User-Agent to bypass Turnstile in headless mode
            options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        # ── Low-RAM optimisations ─────────────────────────────────────────
        options.add_argument("--disable-extensions")
        options.add_argument("--mute-audio")
        options.add_argument("--shm-size=512mb")
        # ─────────────────────────────────────────────────────────────────

        # Eager load strategy: return immediately after DOM is interactive
        options.page_load_strategy = 'eager'
        return options

    def _build_driver(self):
        from webdriver_manager.chrome import ChromeDriverManager
        import random

        options = self._make_uc_options()

        # Use webdriver_manager to get the EXACT right chromedriver for the user's Chrome version
        driver_path = ChromeDriverManager().install()
        
        # Make a copy for undetected_chromedriver to patch to avoid PermissionError
        # when the main email_engine driver is already running and locking the file.
        import shutil
        uc_driver_path = driver_path.replace(".exe", "_uc.exe")
        try:
            shutil.copy2(driver_path, uc_driver_path)
        except Exception:
            pass  # If it fails (e.g. uc_driver_path is locked), just use original

        if self.headless:
            self.log(
                "[Nvoids] ⚠️  Headless mode is ON. "
                "If Cloudflare blocks the scraper, disable Headless Mode and retry."
            )

        # IMPORTANT: Do NOT pass headless= kwarg to uc.Chrome() — UC already
        # reads the --headless=new flag from options. Passing it twice causes
        # a driver version mismatch inside undetected_chromedriver.
        try:
            driver = uc.Chrome(
                options=options,
                driver_executable_path=uc_driver_path,
            )
        except Exception as e:
            # macOS Gatekeeper kills (SIGKILL / exit -9) the ad-hoc binary that
            # undetected_chromedriver just patched & re-signed in-place, even if
            # the original file was already fixed once before — UC's patch step
            # overwrites it with a fresh, Gatekeeper-rejected signature every
            # time. Re-sign the now-patched file and retry once.
            if platform.system() == "Darwin" and "unexpectedly exited" in str(e):
                self.log("[Nvoids] Chromedriver was blocked by macOS Gatekeeper after patching — re-signing and retrying once...")
                try:
                    subprocess.run(["xattr", "-cr", uc_driver_path], stderr=subprocess.DEVNULL)
                    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", uc_driver_path], stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                # UC marks a ChromeOptions instance as "used" after one attempt —
                # must build a fresh one for the retry or it raises RuntimeError.
                driver = uc.Chrome(
                    options=self._make_uc_options(),
                    driver_executable_path=uc_driver_path,
                )
            else:
                raise
        return driver

    # ── Scraping orchestration ────────────────────────────────────────────────

    def _run_scraper(self):
        self.log(
            f"[Nvoids] Starting | Queries: {self.parsed_queries} | "
            f"Limit: {self.limit} | skip_email={self.skip_email}"
        )

        try:
            self.driver = self._build_driver()
            # We'll use a set to track (email, normalized_title) pairs processed
            # in THIS run. This allows the same recruiter to be contacted for
            # genuinely different job titles, while blocking duplicate role reposts.
            session_seen: set = set()
            # Session-wide URL dedup: the same posting shows up under multiple
            # overlapping queries; re-opening it wastes a page load + Groq call.
            self._session_urls = set()
            self.total_scraped = 0
            self.total_pipeline = 0

            # Run summary counters
            self._summary = {"emailed": 0, "pending": 0, "dedup": 0, "content_dup": 0,
                             "cooldown": 0, "session_dup": 0, "no_email": 0,
                             "skill_skip": 0, "cap": 0, "old": 0, "dead": 0}
            
            if self.stats_callback:
                self.stats_callback(self.total_scraped, self.total_pipeline, self.limit)

            # Loop endlessly if continuous_loop is checked. Otherwise run once.
            max_loops = 999999 if self.continuous_loop else 1
            
            for loop_idx in range(max_loops):
                if self.stop_flag:
                    break
                    
                if loop_idx > 0:
                    self.log(f"[Nvoids] Cycle {loop_idx} complete. Waiting {self.rest_time_mins} minutes before refreshing...")
                    
                    # Close browser to bypass Cloudflare Turnstile correctly for the next run
                    if self.driver:
                        try:
                            self.driver.quit()
                        except:
                            pass
                        self.driver = None

                    sleep_seconds = self.rest_time_mins * 60
                    for _ in range(sleep_seconds): 
                        if self.stop_flag: break
                        self._check_pause()
                        time.sleep(1)
                        
                    # Rebuild driver for the new cycle
                    if not self.stop_flag:
                        self.driver = self._build_driver()
                        
                self._check_pause()
                if self.stop_flag:
                    break

                # ── Per-cycle send budget reset (spreads daily cap across cycles) ──
                if hasattr(self.pipeline, "start_new_cycle"):
                    self.pipeline.start_new_cycle()

                # ── Adaptive freshness window ──────────────────────────────
                # First cycle uses the full max_age window; later cycles only
                # need "time since the previous cycle started + 1h buffer" —
                # everything older was already scraped or deduped.
                cycle_start = datetime.now()
                if (loop_idx > 0 and self.max_hours != float('inf')
                        and self._last_cycle_start is not None):
                    gap_h = (cycle_start - self._last_cycle_start).total_seconds() / 3600
                    self._effective_max_hours = min(self.max_hours, max(1.0, gap_h + 1.0))
                    self.log(f"[Nvoids] ⏱ Adaptive freshness: only jobs newer than "
                             f"{self._effective_max_hours:.1f}h this cycle.")
                else:
                    self._effective_max_hours = self.max_hours
                self._last_cycle_start = cycle_start

                # ── Yield-ordered queries: best historical producers first ──
                # (a dead query running first wastes the freshest minutes of the
                # cycle; jitter keeps the order from being perfectly robotic)
                _stats = _load_query_stats()
                loop_queries = list(self.parsed_queries)
                loop_queries.sort(
                    key=lambda q: (_stats.get(q[0].lower(), {}).get("ema_yield", 0.0)
                                   + random.uniform(0, 0.5)),
                    reverse=True
                )

                for query, q_limit in loop_queries:
                    self._check_pause()
                    if self.stop_flag:
                        self.log("[Nvoids] 🛑 Scraper stopped by user.")
                        break
                    if self._should_skip_dead_query(query, _stats):
                        continue
                    # ── Auto-recover from a dead driver before each query ──
                    if not self._is_driver_alive():
                        try:
                            self._restart_driver()
                        except Exception:
                            self.log("[Nvoids] ❌ Cannot recover browser. Stopping.")
                            self.stop_flag = True
                            break
                    # Process up to q_limit for THIS query
                    self._scrape_query(query, q_limit, session_seen)

                total_processed_all_loops = self.total_pipeline

            s = self._summary
            _cd_hrs = self.email_recontact_days * 24
            self.log(
                f"[Nvoids] ✅ Run Complete ─ "
                f"Scraped: {self.total_scraped} | "
                f"Emailed/Drafted: {s['emailed']} | "
                f"Pending: {s['pending']} | "
                f"Dedup (URL): {s['dedup']} | "
                f"Dedup (Content): {s['content_dup']} | "
                f"Vendor Cooldown ({_cd_hrs:g}h): {s['cooldown']} | "
                f"Session dup: {s['session_dup']} | "
                f"No email: {s['no_email']} | "
                f"Skill skip: {s['skill_skip']} | "
                f"Daily cap: {s['cap']} | "
                f"Too old: {s['old']} | "
                f"Dead listings: {s['dead']}"
            )

        except Exception as e:
            self.log(f"[Nvoids] CRITICAL ERROR: {e}\n{traceback.format_exc()}")

        finally:
            # Excel writes are buffered in memory and only auto-flush every 10
            # records — any run that scrapes fewer than that (e.g. a small test,
            # or just a quiet cycle) would otherwise lose all its pending rows
            # when the process/app closes. Always flush here so nothing is lost.
            try:
                if hasattr(self.pipeline, "excel"):
                    self.pipeline.excel.flush()
            except Exception as e:
                self.log(f"[Nvoids] ⚠️ Could not flush pending Excel records: {e}")

            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass

    def _scrape_query(self, query: str, max_jobs: int, session_seen: set) -> int:
        """Run one query and record its yield so future cycles run the best queries first."""
        found_holder = {"found": 0}
        processed = self._scrape_query_inner(query, max_jobs, session_seen, found_holder)
        self._update_query_stats(query, processed, found_holder["found"])
        return processed

    def _update_query_stats(self, query: str, processed: int, found: int):
        """EMA-smoothed per-query yield, persisted to data/query_yield.json."""
        try:
            stats = _load_query_stats()
            key = query.lower()
            rec = stats.get(key, {"runs": 0, "ema_yield": 0.0, "ema_found": 0.0})
            alpha = 0.3   # recent runs weigh more, but one fluke doesn't dominate
            rec["runs"] = rec.get("runs", 0) + 1
            rec["ema_yield"] = round((1 - alpha) * rec.get("ema_yield", 0.0) + alpha * processed, 3)
            rec["ema_found"] = round((1 - alpha) * rec.get("ema_found", 0.0) + alpha * found, 3)
            rec["last_run"] = datetime.now().isoformat(timespec="seconds")
            rec["skips_in_a_row"] = 0   # ran live this time, so reset the auto-pause streak
            stats[key] = rec
            _save_query_stats(stats)
        except Exception:
            pass

    def _should_skip_dead_query(self, query: str, stats: dict) -> bool:
        """True if `query` has found ~0 jobs over many recent runs and should be
        auto-paused this cycle. Still lets it through every `_DEAD_QUERY_REVISIT_EVERY`
        would-be-skips, in case new postings started appearing for it."""
        try:
            key = query.lower()
            rec = stats.get(key)
            if not rec or rec.get("runs", 0) < _DEAD_QUERY_MIN_RUNS:
                return False
            if rec.get("ema_found", 0.0) >= _DEAD_QUERY_FOUND_THRESHOLD:
                return False
            skips = rec.get("skips_in_a_row", 0)
            if skips >= _DEAD_QUERY_REVISIT_EVERY:
                rec["skips_in_a_row"] = 0
                stats[key] = rec
                _save_query_stats(stats)
                return False
            rec["skips_in_a_row"] = skips + 1
            stats[key] = rec
            _save_query_stats(stats)
            self.log(f"[Nvoids] ⏭ Auto-pausing dead search term '{query}' "
                     f"(0-yield across last {rec.get('runs')} runs) — "
                     f"will retry in {_DEAD_QUERY_REVISIT_EVERY - rec['skips_in_a_row']} cycle(s).")
            return True
        except Exception:
            return False

    def _scrape_query_inner(self, query: str, max_jobs: int, session_seen: set,
                            found_holder: dict) -> int:
        self.log(f"[Nvoids] Searching: '{query}'")
        processed = 0

        try:
            # ── Load home page ────────────────────────────────────────
            self._safe_get("https://jobs.nvoids.com/index.jsp")

            # Wait for search box, handling Cloudflare if it appears
            try:
                # Cloudflare check loop (up to 60 seconds; longer wait per tick in headless
                # mode since Turnstile takes more time without GPU/display rendering)
                #
                # NOTE: previously also matched on the word "cloudflare" appearing
                # anywhere in the page source — but that text is present on every
                # normal, unblocked page load too (verified directly: title stays
                # normal, search box is immediately clickable, load takes <2s), so
                # it was triggering this wait needlessly on nearly every query.
                # The page title switching to "Just a moment..." is Cloudflare's
                # actual interstitial-challenge signal — that alone is reliable.
                cf_wait = 5 if self.headless else 3
                for _ in range(15):
                    title = self.driver.title or ""
                    if "just a moment" in title.lower():
                        self.log("[Nvoids] Cloudflare protection active. Waiting for auto-bypass...")
                        time.sleep(cf_wait)
                    else:
                        break

                search_box = WebDriverWait(self.driver, 15).until(
                    EC.element_to_be_clickable((By.ID, "search_id"))
                )
            except TimeoutException:
                self.log(f"[Nvoids] Search box not found for '{query}'. Site may be blocking.")
                return 0

            # ── Human-like: scroll to box, pause, clear, type slowly ──
            _scroll_to(self.driver, search_box)
            _human_pause(0.5, 1.2)
            search_box.clear()
            _human_pause(0.2, 0.5)
            _human_type(search_box, query)

            # ── Wait a moment before clicking submit ──────────────────
            _human_pause(0.3, 0.8)

            try:
                submit_btn = WebDriverWait(self.driver, 8).until(
                    EC.element_to_be_clickable((By.ID, "submit_id"))
                )
                _stealth_click(self.driver, submit_btn)
            except (TimeoutException, NoSuchElementException):
                # Fallback: press Enter on the search box
                from selenium.webdriver.common.keys import Keys
                search_box.send_keys(Keys.RETURN)

            # ── After clicking submit: WAIT for the results page to fully load.
            # The submit button triggers a full POST/page-reload on Nvoids.
            # We must wait for that NEW page to finish loading before touching the DOM.
            self.log("[Nvoids] Waiting for results page to load...")
            self._wait_for_page_ready(min_wait=2, max_wait=4)

            try:
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//a[contains(@href, 'job_details')]")
                    )
                )
            except TimeoutException:
                self.log(f"[Nvoids] No results found for '{query}'.")
                return 0

            # Single JS execution to extract all links AND surrounding row text
            # (the row text often contains the post date, used for pre-sorting)
            try:
                js_links = self.driver.execute_script("""
                    return Array.from(document.querySelectorAll("a[href*='job_details']"))
                        .map(a => {
                            // Walk up to find a table row or container that holds the post date
                            let rowText = "";
                            let node = a;
                            for (let i = 0; i < 5; i++) {
                                node = node.parentElement;
                                if (!node) break;
                                let tag = node.tagName;
                                if (tag === "TR" || tag === "LI" || tag === "DIV") {
                                    rowText = (node.textContent || "").trim();
                                    break;
                                }
                            }
                            return { text: (a.textContent || "").trim(), href: a.href, row: rowText };
                        });
                """)
            except Exception as jse:
                self.log(f"[Nvoids] JS extraction failed: {jse}")
                js_links = []

            job_links = []  # list of (title, url, post_date_or_None)
            for item in js_links:
                try:
                    title = item.get("text", "").strip()
                    url = item.get("href", "").strip()
                    if title and url and _title_matches_query(title, query):
                        row_text = item.get("row", "")
                        post_dt = _extract_post_date(row_text) if row_text else None
                        job_links.append((title, url, post_dt))
                except Exception:
                    continue

            # ── Sort newest-first so the freshest jobs are processed first ──
            # Jobs where we couldn't parse a date from the listing row are placed
            # at the end (they may still be fresh — we'll verify on the detail page).
            _EPOCH = datetime.min.replace(tzinfo=IST)
            job_links.sort(key=lambda x: x[2] if x[2] else _EPOCH, reverse=True)

            found_holder["found"] = len(job_links)
            self.log(f"[Nvoids] {len(job_links)} matching job(s) for '{query}' (sorted newest-first).")
            
            self.total_scraped += len(job_links)
            if self.stats_callback:
                self.stats_callback(self.total_scraped, self.total_pipeline, max(0, self.limit - self.total_pipeline))

            # Session-wide (cross-query) URL dedup, not per-query
            seen_urls = getattr(self, "_session_urls", None)
            if seen_urls is None:
                seen_urls = self._session_urls = set()
            too_old_streak = 0
            for title, url, _listing_date in job_links:
                self._check_pause()
                if self.stop_flag: break
                self.skip_flag = False   # reset before each new job
                if processed >= max_jobs:
                    break
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # ── Pre-filter: skip obviously-excluded jobs by TITLE alone, ──
                # before opening the page at all. Many exclude-keywords (role
                # type, seniority) already show up in the search-result title,
                # so we save a full page load + content-poll wait for those.
                # This is just an early exit for the OBVIOUS cases — the full
                # body-text exclude check still runs later for anything that
                # passes this quick title-only look, so nothing gets contacted
                # that wouldn't have been already.
                if self.exclude_keywords:
                    title_check = title.lower()
                    _pre_excluded_kw = None
                    for exc in self.exclude_keywords:
                        pat = r'\b' + re.escape(exc) + r'\b'
                        if _has_unnegated_match(pat, title_check):
                            _pre_excluded_kw = exc
                            break
                    if _pre_excluded_kw:
                        self.log(f"[Nvoids] Skipped (title contains excluded keyword '{_pre_excluded_kw}'): {title[:60]}")
                        if hasattr(self, '_summary'):
                            self._summary["skill_skip"] += 1
                        continue

                # Human pause between jobs — critical for bot-detection bypass
                _human_pause(0.5, 1.2)
                if random.random() < 0.07:   # ~1 in 14 jobs: human got distracted
                    time.sleep(random.uniform(4, 15))

                result = self._process_job_page(title, url, session_seen, query)
                if result == "Skipped (Too Old)":
                    # Don't abandon the query on the FIRST old job: listings with
                    # unparseable dates sort last and may still be fresh. Only
                    # move on after several consecutive old jobs.
                    if hasattr(self, '_summary'): self._summary["old"] += 1
                    too_old_streak += 1
                    if too_old_streak >= 3:
                        self.log("[Nvoids] 3 old jobs in a row — moving to next query.")
                        break
                    continue
                too_old_streak = 0

                # Update summary counters
                if hasattr(self, '_summary'):
                    r = str(result or "")
                    if "Content Duplicate" in r:
                        self._summary["content_dup"] += 1
                    elif "Cooldown" in r:
                        self._summary["cooldown"] += 1
                    elif "Session Duplicate" in r:
                        self._summary["session_dup"] += 1
                    elif "Duplicate" in r or "Previous session" in r or "already contacted" in r.lower():
                        self._summary["dedup"] += 1
                    elif "Dead Listing" in r:
                        self._summary["dead"] += 1
                    elif "No Email" in r or "Call Pending" in r or "No Contact" in r:
                        self._summary["no_email"] += 1
                    elif "Cap)" in r:   # "Pending (Daily Cap)" / "Pending (Cycle Cap)"
                        self._summary["cap"] += 1
                    elif "Pending" in r:
                        self._summary["pending"] += 1
                    elif result is not None and "Skipped" not in r:
                        self._summary["emailed"] += 1
                    elif "Skill" in r or result is None:
                        self._summary["skill_skip"] += 1

                if result is not None and "Skipped" not in str(result):
                    processed += 1
                    self.total_pipeline += 1
                    if self.stats_callback:
                        self.stats_callback(self.total_scraped, self.total_pipeline, max(0, self.limit - processed))

        except Exception as e:
            self.log(f"[Nvoids] Error in query '{query}': {e}\n{traceback.format_exc()}")

        return processed

    def _process_job_page(self, title: str, url: str, session_seen: set, query: str = ""):
        self._temp_header_location = ""
        # Clean title right at the start to strip recruiting noise (e.g., "Urgent Need", "Required")
        # This is a PRELIMINARY clean on the noisy search-result anchor text.
        # The authoritative title will be extracted from the detail page header below.
        if hasattr(self.pipeline, "_clean_job_title"):
            title = self.pipeline._clean_job_title(title)

        # Check if user clicked ⏭ Skip before we even load this job
        if self.skip_flag:
            self.skip_flag = False
            self.log(f"[Nvoids] ⏭ Job skipped by user: {title}")
            return "Skipped (User)"
        # Known-dead URL from a past session — skip without even loading the page
        if self._dedup_db and self._dedup_db.is_dead_url(url):
            return "Skipped (Dead Listing)"

        self.log(f"[Nvoids] Opening: {title}")
        try:
            self._safe_get(url, decrypt_emails=True)

            # Dead/expired listing phrases — checked both during the content poll
            # (to bail out early) and after it (as the authoritative dead check).
            # NOTE: do NOT add "job_kill" — it appears in the footer of every
            # live Nvoids listing ("To remove this job post send 'job_kill ...'")
            _DEAD_PHRASES = (
                "not available or removed",
                "no longer available",
                "this job has been removed",
                "job has expired",
                "listing is no longer",
                "page not found",
            )
            _looks_dead = lambda t: any(p in t.lower() for p in _DEAD_PHRASES)

            # With page_load_strategy='eager' the driver returns as soon as the DOM
            # is interactive, but JS-rendered job content may not be painted yet.
            # ── Smart content poll ────────────────────────────────────────────
            # Nvoids boilerplate alone (Home link + Time Taken + admin contact text)
            # is ~200-250 chars even on a DEAD listing, so a simple char-count
            # threshold of 120 is useless. Instead we poll until we see a signal
            # that real job content has loaded: either an email address OR
            # a body that is long enough to contain an actual JD (≥600 chars).
            # We wait up to 10 s (20 × 0.5s) before giving up — unless a dead-listing
            # phrase already appears, in which case we bail out immediately since
            # that text never turns into an email or a full JD no matter how long we wait.
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            _HAS_CONTENT = lambda t: (
                re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', t)
                or len(t.strip()) >= 600
            )
            if not _HAS_CONTENT(body_text) and not _looks_dead(body_text):
                for _ in range(20):  # up to 10s (20 × 0.5s)
                    time.sleep(0.5)
                    body_text = self.driver.find_element(By.TAG_NAME, "body").text
                    if _HAS_CONTENT(body_text) or _looks_dead(body_text):
                        break

            # ── Extract authoritative title from the detail page header ────
            # Nvoids always renders the first <tr><td> of the job table as:
            #   "<Raw Job Title> at City, State, USA"
            # This is FAR more reliable than the search-result anchor text,
            # which is often polluted with recruiter noise.
            try:
                first_td = self.driver.find_element(
                    By.XPATH, "//table//tr[1]/td[1]"
                )
                page_header = (first_td.text or "").strip()
                if page_header:
                    # Strip trailing " at City, State, USA" location suffix and capture location
                    split_parts = re.split(
                        r'\s+at\s+([A-Za-z].*?)(?:,\s*USA)?$',
                        page_header, flags=re.IGNORECASE
                    )
                    header_title = split_parts[0].strip()
                    if len(split_parts) > 1 and split_parts[1]:
                        loc_candidate = split_parts[1].strip()
                        if 2 < len(loc_candidate) < 60:
                            self._temp_header_location = loc_candidate
                            
                    if len(header_title) >= 4:
                        # Apply the same cleaner used everywhere so noise
                        # like "Requirement AI Engineer" → "AI Engineer"
                        if hasattr(self.pipeline, "_clean_job_title"):
                            header_title = self.pipeline._clean_job_title(header_title)
                        if len(header_title) >= 4:
                            self.log(f"[Nvoids] 🏷 Detail-page title: '{header_title}' (was: '{title}')")
                            title = header_title
            except Exception:
                pass  # Fallback: keep the search-result title already cleaned above

            # Dead/expired listing — bail BEFORE scrolling, Groq, or Excel writes.
            # A listing is dead when the page has no email AND no substantial JD body.
            # We also catch explicit Nvoids removal/error phrases (via _looks_dead,
            # defined earlier alongside the content poll).
            _has_dead_phrase = _looks_dead(body_text)
            _has_email = bool(re.search(
                r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', body_text
            ))
            _is_dead = _has_dead_phrase or (not _has_email and len(body_text.strip()) < 600)
            if _is_dead:
                self.log(f"[Nvoids] Dead listing (removed): {title[:60]}")
                if self._dedup_db:
                    self._dedup_db.mark_dead_url(url)
                return "Skipped (Dead Listing)"

            if not _is_fresh(body_text, max_hours=self._effective_max_hours):
                self.log(f"[Nvoids] Skipped (older than {self._effective_max_hours:.1f}h): {title}")
                return "Skipped (Too Old)"

            # Scroll through the JD like a person reading it (live pages only).
            _human_read_scroll(self.driver)

            emails        = _extract_recruiter_emails(body_text)
            recruiter_email = emails[0] if emails else ""
            dynamic_cc    = ",".join(e for e in emails[1:] if e != recruiter_email)
            phone_num     = _extract_phone(body_text)

            # No email AND no phone → nothing to outreach. Skip before any
            # Groq call or Excel write (these rows used to clog Pending forever).
            if not recruiter_email and not phone_num:
                self.log(f"[Nvoids] Skipped (no email or phone on page): {title[:60]}")
                return "Skipped (No Contact)"
            if not recruiter_email:
                self.log(f"[Nvoids] No recruiter email in: {title} (phone only)")

            # ── Persistent cross-session email dedup (time-windowed) ──
            # Only skip if this recruiter was contacted RECENTLY. Vendors repost
            # the same roles every few days; a permanent ban would skip them forever.
            if recruiter_email and self._dedup_db:
                if self._dedup_db.is_email_contacted(recruiter_email, within_days=self.email_recontact_days):
                    _cd_hrs = self.email_recontact_days * 24
                    self.log(f"[Nvoids] ⏳ Vendor cooldown ({_cd_hrs:g}h window): {recruiter_email} — will be contactable again after cooldown expires.")
                    return "Skipped (Cooldown)"

            # ── Session-level dedup: block same (email + normalized_title) pair ──
            # Using a tuple key lets the same recruiter be emailed for DIFFERENT
            # roles in the same session while still blocking reposted duplicates.
            if recruiter_email:
                session_key = (recruiter_email, _normalize_session_title(title))
                if session_key in session_seen:
                    self.log(f"[Nvoids] ⏭ Same recruiter+role already processed this session: {title[:50]}")
                    return "Skipped (Session Duplicate)"
                session_seen.add(session_key)

            company  = self._extract_company(body_text) or "your company"
            
            # ── Exclude Keywords Check ──
            if self.exclude_keywords:
                body_lower = body_text.lower()
                title_lower = title.lower()
                for exc in self.exclude_keywords:
                    # Word-boundary match: a raw substring check made 'sap'
                    # hit 'ASAP' (present in most hotlist posts), silently
                    # excluding a huge share of perfectly good jobs.
                    # Negation-aware: "no clearance required" must NOT count
                    # as a 'clearance' hit.
                    pat = r'\b' + re.escape(exc) + r'\b'
                    if _has_unnegated_match(pat, body_lower) or _has_unnegated_match(pat, title_lower):
                        self.log(f"[Nvoids] Skipped (Contains excluded keyword '{exc}'): {title}")
                        return None

            # ── Skill Scoring Engine (Deep Match) ──
            # Moved up to save Groq API credits! If we fail the match, we skip Groq.
            matched_skills = []
            if self.my_core_skills and self.min_match_score > 0:
                body_lower = body_text.lower()
                for skill in self.my_core_skills:
                    if _skill_matches(skill, body_lower):
                        matched_skills.append(skill)
                        
                # A typical job description might only list a few core skills.
                # Calculating the percentage out of ALL the user's skills unfairly punishes them for having a large skill pool.
                expected_job_skills = min(5, len(self.my_core_skills))
                score_pct = int((len(matched_skills) / expected_job_skills) * 100) if self.my_core_skills else 100
                score_pct = min(100, score_pct)
                
                bar_filled = int((score_pct / 100.0) * 10)
                match_bar = "█" * bar_filled + "░" * (10 - bar_filled)
                matched_str = ", ".join(matched_skills[:4]) if matched_skills else "none"
                self.log(f"[Nvoids] 🎯 Skill Match: {score_pct}% [{match_bar}] ({len(matched_skills)} matched: {matched_str}) for {title[:45]}")

                if score_pct < self.min_match_score:
                    # ── Always-Accept override: bypass score filter if title is whitelisted ──
                    always_match = self._is_always_accept(title)
                    if always_match:
                        self.log(f"[Nvoids] ⭐ Always-Accept bypass (matched: '{always_match}'): {title[:50]}")
                    else:
                        title_lower = title.lower()
                        # Include 2-char words so short but important queries like
                        # 'AI Engineer' or 'ML Ops' are not incorrectly bypassed.
                        query_words = [w for w in query.lower().split() if len(w) >= 2]
                        is_strong_match = bool(query_words)
                        for qw in query_words:
                            if qw not in title_lower:
                                is_strong_match = False
                                break
                                
                        if is_strong_match:
                            self.log(f"[Nvoids] ✅ Match Bypassed (Title explicitly contains '{query}'): {title[:30]}...")
                        else:
                            self.log(f"[Nvoids] Skipped (Skill Match {score_pct}% [{match_bar}] < {self.min_match_score}%): {title}")
                            return None
                
                self.log(f"[Nvoids] ✅ Skill Match: {score_pct}% ({len(matched_skills)}/{len(self.my_core_skills)}) for {title[:30]}...")

            # ── Local Regex Parsing FIRST ──
            location = (
                getattr(self, "_temp_header_location", "")
                or _extract_location_from_title(title)
                or _extract_location(body_text)
            )
            # Normalize location casing (e.g. Remote -> Remote, Dallas, tx -> Dallas, TX)
            if location:
                location = location.strip().title()
                if location.lower() == "remote":
                    location = "Remote"
            
            # ── 3-Tier Keyword Extraction ────────────────────────────────────
            # Tier 1: Parse a labelled 'Required Skills:' section if one exists
            #         (highest precision — the recruiter listed them explicitly).
            # Tier 2: RAG SkillRanker backfills slots with YOUR personal skills
            #         ranked by cosine similarity to the JD (personalised match).
            # Tier 3: Static regex list fallback (_extract_keywords) when neither
            #         of the above is available.

            if matched_skills:
                # Skill match already ran -- use all matched skills for internal
                # scoring, but cap at 6 for the email body display only.
                try:
                    from core.outreach.skill_ranker import _fmt_skill
                    display_skills = [_fmt_skill(s) for s in matched_skills]
                except Exception:
                    display_skills = [s.upper() if len(s) <= 3 else s.title() for s in matched_skills]
                # ALL matched_skills stay in memory for resume scoring (uncapped).
                # Only 6 go into the email keywords field.
                keywords = ", ".join(display_skills[:6])
            else:
                # Try Tier 1: structured section parser -- extract ALL skills, no cap.
                # The full list is used for resume matching; only 6 go into the email.
                try:
                    from core.outreach.skill_ranker import extract_skills_section
                    all_section_skills = extract_skills_section(body_text)  # uncapped
                except Exception:
                    all_section_skills = []

                if self._skill_ranker and getattr(self._skill_ranker, 'is_available', False):
                    # Tier 2: merge section skills + RAG personal ranking.
                    # Internal: pass all_section_skills (uncapped) so RAG can backfill
                    # from YOUR personal skills beyond slot 6.
                    # Display: cap final output at 6 for the email body.
                    keywords = self._skill_ranker.merge_with_section_skills(
                        all_section_skills, body_text, top_k=6  # 6 = email display cap
                    )
                    if keywords:
                        self.log(f"[Nvoids] [RAG] email keywords (6 max): {keywords}")
                    elif all_section_skills:
                        # RAG returned nothing but section parse worked
                        try:
                            from core.outreach.skill_ranker import _fmt_skill
                            keywords = ", ".join(_fmt_skill(s) for s in all_section_skills[:6])
                        except Exception:
                            keywords = ", ".join(
                                s.upper() if len(s) <= 3 else s.title()
                                for s in all_section_skills[:6]
                            )
                        self.log(f"[Nvoids] [Section] email keywords (6 max): {keywords}")
                    else:
                        # Tier 3 fallback
                        keywords = _extract_keywords(body_text)
                elif all_section_skills:
                    # Tier 1 only (no RAG model loaded) -- cap at 6 for email
                    try:
                        from core.outreach.skill_ranker import _fmt_skill
                        keywords = ", ".join(_fmt_skill(s) for s in all_section_skills[:6])
                    except Exception:
                        keywords = ", ".join(
                            s.upper() if len(s) <= 3 else s.title()
                            for s in all_section_skills[:6]
                        )
                    self.log(f"[Nvoids] [Section] email keywords (6 max): {keywords}")
                else:
                    # Tier 3: static regex fallback
                    keywords = _extract_keywords(body_text)

            # ── Company fallback from email domain BEFORE the Groq decision ──
            # Nvoids pages almost never label the company, so without this the
            # "company unknown" condition forced a Groq call on EVERY job page.
            if company == "your company" and recruiter_email:
                domain = recruiter_email.split('@')[-1].split('.')[0]
                if domain.lower() not in ["gmail", "yahoo", "hotmail", "outlook", "nvoids", "mail"]:
                    company = domain.title()

            # ── Groq AI Extractor: Trigger whenever fields/title are unsure ──
            kw_count = len([k for k in keywords.split(',') if k.strip()])
            title_has_noise = (
                len(title) > 30 or
                any(c in title for c in ['+', '|', ':', '-', '(', '[', ']']) or
                any(w in title.lower() for w in ['year', 'yr', 'exp', 'position', 'opening', 'urgent', 'req', 'need', 'wanted'])
            )
            is_unsure = (
                not location or
                not keywords or
                kw_count < 3 or
                company == "your company" or
                title_has_noise
            )

            if self._ai and is_unsure:
                self.log(f"[Nvoids] 🤖 Unsure on fields/title. Groq AI analyzing JD for: {title[:60]}")
                ai_result = self._ai.extract(title=title, body=body_text)
                
                if not ai_result.get("is_job_post", True):
                    self.log(f"[Nvoids] 🗑️ Skipped (Not a real job posting): {title}")
                    return "Skipped (Not Job Post)"

                ai_location  = ai_result.get("location", "")
                ai_keywords  = ai_result.get("keywords", "")
                ai_job_title = ai_result.get("job_title", "")
                ai_company   = ai_result.get("company", "")
                
                if ai_location:
                    location = ai_location
                if ai_keywords:
                    keywords = ai_keywords
                if ai_job_title:
                    # Run AI title through clean_job_title to guarantee clean formatting
                    candidate = self.pipeline._clean_job_title(ai_job_title)
                    if len(candidate) >= 4:
                        title = candidate
                if ai_company:
                    company = ai_company
            else:
                if self._ai:
                    self.log(f"[Nvoids] ⚡ Extraction confident. Bypassing Groq for: {title[:60]}")

            # ── Final Company Fallback ──
            if company == "your company" and recruiter_email:
                domain = recruiter_email.split('@')[-1].split('.')[0]
                if domain.lower() not in ["gmail", "yahoo", "hotmail", "outlook", "nvoids", "mail"]:
                    company = domain.title()

            # Extract and store the post date so the pipeline can sort pending
            # emails newest-first when the user clicks "Draft Emails" later.
            post_dt = _extract_post_date(body_text)
            posted_date_str = post_dt.strftime("%Y-%m-%d %H:%M") if post_dt else ""

            raw_job = {
                "Keywords":        keywords,
                "Location":        location,
                "Phone":           phone_num,
                "Job Title":       title,
                "Company":         company,
                "Description":     body_text,
                "URL":             url,
                "Recruiter Name":  "",
                "Recruiter Email": recruiter_email,
                "Dynamic CC":      dynamic_cc,
                "Source":          "Nvoids",
                "Posted Date":     posted_date_str,
            }

            if not self.skip_email:
                self.log(f"[Nvoids] Processing email for: {title}")
            
            status = self.pipeline.process_job(raw_job, skip_email=self.skip_email)

            if "Matched Skills Info" in raw_job:
                info = raw_job["Matched Skills Info"]
                resume_name = raw_job.get("Resume Used", "Default")
                # Format skills nicely
                try:
                    from core.outreach.skill_ranker import _fmt_skill
                    all_matched = sorted(set(info.get("matched_uni", []) + info.get("matched_gen", [])))
                    skills_str = ", ".join(_fmt_skill(s) for s in all_matched)
                except Exception:
                    skills_str = ", ".join(sorted(set(info.get("matched_uni", []) + info.get("matched_gen", []))))
                
                self.log(f"[Nvoids] matched resume: '{resume_name}' (score: {info.get('score')}, confidence: {info.get('confidence')}%)")
                if skills_str:
                    self.log(f"[Nvoids] matched skills: {skills_str}")
                else:
                    self.log(f"[Nvoids] matched skills: None (Name Affinity/Exact Match boost only)")

            # Mark this recruiter email as permanently contacted in the persistent DB.
            # Only do this when an email was actually sent/drafted — never on skips.
            _SENT_STATUSES = {"Sent", "Draft", "Queued for Background Draft",
                              "Draft (Not Sent via SMTP)", "Pending Sending"}
            if recruiter_email and self._dedup_db and status in _SENT_STATUSES:
                self._dedup_db.mark_email_contacted(recruiter_email, title, source="nvoids")

            if phone_num:
                self.log(f"[Pipeline] {status}: {title} (Phone: {phone_num})")
            else:
                self.log(f"[Pipeline] {status}: {title}")
            return status

        except Exception as e:
            self.log(f"[Nvoids] Error on {url}: {e}\n{traceback.format_exc()}")
            return None

    # ── Page-ready helper ─────────────────────────────────────────────────────

    def _wait_for_page_ready(self, min_wait: float = 1.5, max_wait: float = 3):
        """
        Two-phase wait used AFTER any navigation that causes a full page reload:

        Phase 1 — Hard sleep (min_wait..max_wait seconds).
                   This is non-negotiable: the browser needs time to fire the
                   unload event on the OLD page, start the network request,
                   receive the response, and begin parsing the NEW document.
                   Polling document.readyState before this completes still reads
                   the OLD page's readyState and returns 'complete' immediately.

        Phase 2 — Poll document.readyState until 'complete'.
                   After the hard sleep the new document is loading. We wait
                   here until it reports fully loaded (all resources fetched).
        """
        # Phase 1: unconditional sleep so the old page has time to unload
        time.sleep(random.uniform(min_wait, max_wait))

        # Phase 2: poll until the new page's DOM + resources are all loaded
        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass   # If still not complete after 30s, proceed with whatever is loaded

    # ── Navigation helper ─────────────────────────────────────────────────────

    def _safe_get(self, url: str, timeout: int = 60, decrypt_emails: bool = False):
        """
        Navigate to `url` with dead-driver recovery built in.
        If the ChromeDriver process has died (ReadTimeoutError / WebDriverException),
        the driver is automatically restarted and the navigation retried once.

        decrypt_emails: only run the Cloudflare email-decrypt JS when True
        (job detail pages). Running DOM-rewriting JS on every navigation at a
        fixed offset after load is a uniquely bot-like signature.
        """
        from selenium.common.exceptions import WebDriverException

        def _attempt(drv):
            drv.set_page_load_timeout(timeout)
            try:
                drv.get(url)
            except TimeoutException:
                self.log(f"[Nvoids] Page load exceeded {timeout}s; using partial DOM: {url}")
                try:
                    drv.execute_script("window.stop();")
                except Exception:
                    pass
                time.sleep(2)
            finally:
                try:
                    drv.set_page_load_timeout(300)
                except Exception:
                    pass

        # ── First attempt ─────────────────────────────────────────────
        try:
            _attempt(self.driver)
        except (WebDriverException, OSError, Exception) as exc:
            # Detect a dead-driver connection error (urllib3 ReadTimeoutError
            # or "connection refused" both bubble up as WebDriverException or OSError)
            err_str = str(exc).lower()
            is_dead = any(k in err_str for k in (
                "read timed out", "connection refused", "connection reset",
                "no such session", "invalid session", "newconnectionerror",
                "remotedisconnected", "http connection pool"
            ))
            if is_dead:
                self.log(f"[Nvoids] Driver appears dead ({type(exc).__name__}). Attempting recovery...")
                self._restart_driver()
                # ── Retry with fresh driver ───────────────────────────
                _attempt(self.driver)
            else:
                raise

        # ── Wait for the DOM to reach at least 'interactive' ──────────
        # This covers the case where driver.get() returned early but JS
        # is still building the page structure.
        try:
            WebDriverWait(self.driver, 20).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
        except Exception:
            pass

        # Inject JavaScript to decrypt Cloudflare obfuscated emails —
        # only on pages that actually contain emails (detail pages), and
        # after a jittered delay so it never fires at a constant offset.
        if decrypt_emails:
            time.sleep(random.uniform(0.4, 1.5))
            try:
                decrypt_script = """
            (function() {
                function decryptCfEmail(hex) {
                    let email = "";
                    let key = parseInt(hex.substr(0, 2), 16);
                    for (let i = 2; i < hex.length; i += 2) {
                        email += String.fromCharCode(parseInt(hex.substr(i, 2), 16) ^ key);
                    }
                    return email;
                }
                let cfElements = document.querySelectorAll('.__cf_email__, [data-cfemail]');
                cfElements.forEach(el => {
                    let hex = el.getAttribute('data-cfemail');
                    if (hex) {
                        try {
                            let email = decryptCfEmail(hex);
                            el.innerHTML = email;
                            if (el.tagName === 'A' && el.getAttribute('href') && el.getAttribute('href').includes('email-protection')) {
                                el.setAttribute('href', 'mailto:' + email);
                            }
                            el.removeAttribute('data-cfemail');
                            el.classList.remove('__cf_email__');
                        } catch(e) {}
                    }
                });
                let cfLinks = document.querySelectorAll('a[href*="/cdn-cgi/l/email-protection"]');
                cfLinks.forEach(el => {
                    let href = el.getAttribute('href');
                    let hashIdx = href.indexOf('#');
                    if (hashIdx !== -1) {
                        let hex = href.substring(hashIdx + 1);
                        try {
                            let email = decryptCfEmail(hex);
                            el.setAttribute('href', 'mailto:' + email);
                            if (el.textContent.includes('[email protected]') || el.textContent.includes('[email\\u00a0protected]') || el.textContent.includes('[email\\u00A0protected]')) {
                                el.textContent = email;
                            }
                        } catch(e) {}
                    }
                });
            })();
            """
                self.driver.execute_script(decrypt_script)
            except Exception as je:
                self.log(f"[Nvoids] Warning: Cloudflare email decryption script injection failed: {je}")

        # ── Human pause — let CSS paint + any lazy JS settle ─────────
        _human_pause(0.5, 1.0)

    # ── Company extractor ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_company(body_text: str) -> str:
        for pat in [r'(?:company|client|employer)\s*[:\-]\s*([A-Za-z0-9 ,\.&]+)']:
            m = re.search(pat, body_text, re.IGNORECASE)
            if m:
                name = m.group(1).strip().split('\n')[0].strip()
                if 3 < len(name) < 60:
                    return name
        return ""
