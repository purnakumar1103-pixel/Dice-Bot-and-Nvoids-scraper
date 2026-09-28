"""
SkillRanker – RAG-based personal skill relevance extractor.

Uses sentence-transformers (local, free) to semantically compare YOUR
resume skills against a job description, then returns them ranked by
how relevant they are to that specific JD.

Why this beats a static keyword list
──────────────────────────────────────
• "PySpark" and "distributed computing" score high together even if
  the JD never spells out PySpark explicitly — semantic overlap.
• Your most impressive/specific skills (Databricks, Delta Lake) beat
  generic ones (Python, SQL) when the JD is specifically asking for them.
• Zero API cost — runs entirely on CPU with an 80 MB local model.
• First call downloads the model (~5-15 s). Every call after is ~50 ms.

Usage
──────
    ranker = SkillRanker(["Python", "Databricks", "PySpark", "Kafka"])
    keywords = ranker.rank_skills(job_description_text, top_k=6)
    # → "Databricks, PySpark, Kafka, Python"
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── Optional fast path: if the user doesn't have sentence-transformers,
#    we degrade gracefully instead of crashing the scraper on import.
try:
    from sentence_transformers import SentenceTransformer, util as st_util
    import torch
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False


# ── "Required Skills" section header patterns ──────────────────────────────
# Nvoids JDs frequently have a labeled block like:
#   "Required Skills:\n  • Python\n  • Spark\n..."
# Parsing this directly is more precise than scanning the whole body.
_SKILLS_SECTION_RE = re.compile(
    r'(?:required\s+skills?|key\s+(?:technical\s+)?skills?|'
    r'tech(?:nical)?\s+(?:skills?|requirements?)|'
    r'must[\s\-]have(?:\s+skills?)?|mandatory\s+skills?|'
    r'core\s+skills?|essential\s+skills?|'
    r'key\s+qualifications?|primary\s+skills?)'
    r'\s*[:\-]?\s*\n'
    r'(.*?)'
    r'(?:\n\s*\n|\n(?:[A-Z][A-Za-z ]{4,}[:\-])|$)',
    re.IGNORECASE | re.DOTALL,
)

# Lines that are definitely NOT skill names (section headers, filler text)
_SKIP_LINE_RE = re.compile(
    r'^(?:years?|experience|must|should|will|the|and|or|with|'
    r'we|you|our|this|that|have|has|be|is|are|'
    r'preferred|desirable|nice|bonus|plus)\b',
    re.IGNORECASE,
)


# ── Skill display formatter ────────────────────────────────────────────────
# Naive .title() mangles acronyms: "AWS S3" -> "Aws S3", "dbt" -> "Dbt".
# This helper preserves existing capitalisation for acronyms/mixed-case words.
_KNOWN_UPPER = {
    "aws", "gcp", "sql", "etl", "elt", "api", "sdk", "iac",
    "ai", "ml", "bi", "ci", "cd", "nlp", "llm", "rag",
    "s3", "ec2", "rds", "ecs", "eks", "emr", "glue",
    "hdfs", "adls", "hdl", "sla", "sox", "gdpr", "hipaa",
}
_KNOWN_MIXED = {
    "pyspark": "PySpark",
    "databricks": "Databricks",
    "snowflake": "Snowflake",
    "powerbi": "PowerBI",
    "devops": "DevOps",
    "mlops": "MLOps",
    "aiops": "AIOps",
    "openai": "OpenAI",
    "langchain": "LangChain",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "github": "GitHub",
    "gitlab": "GitLab",
    "nosql": "NoSQL",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "mongodb": "MongoDB",
    "elasticsearch": "Elasticsearch",
    "kubernetes": "Kubernetes",
    "bigquery": "BigQuery",
    "redshift": "Redshift",
    "dynamodb": "DynamoDB",
    "stepfunctions": "StepFunctions",
    "sagemaker": "SageMaker",
}


def _fmt_skill(skill: str) -> str:
    """
    Format a skill name for display, preserving known acronyms and mixed-case
    names rather than blindly applying .title().

    Examples:
        'aws s3'        -> 'AWS S3'
        'pyspark'       -> 'PySpark'
        'delta lake'    -> 'Delta Lake'
        'dbt'           -> 'dbt'   (already lowercase short tool name, keep as-is)
        'python'        -> 'Python'
    """
    low = skill.strip().lower()
    # Exact match in known mixed-case table
    if low in _KNOWN_MIXED:
        return _KNOWN_MIXED[low]
    # Word-by-word formatting for multi-word skills like "AWS S3", "Delta Lake"
    parts = skill.strip().split()
    formatted_parts = []
    for part in parts:
        pl = part.lower()
        if pl in _KNOWN_UPPER:
            formatted_parts.append(pl.upper())
        elif pl in _KNOWN_MIXED:
            formatted_parts.append(_KNOWN_MIXED[pl])
        elif part == part.upper() and len(part) <= 6:  # already an acronym like "ETL"
            formatted_parts.append(part.upper())
        elif part[0].isupper() and not part.isupper():  # already mixed-case like "PySpark"
            formatted_parts.append(part)
        elif len(pl) <= 3:  # short tool names like "dbt", "sql" stay lowercase unless known
            formatted_parts.append(pl if pl not in _KNOWN_UPPER else pl.upper())
        else:
            formatted_parts.append(part.title())
    return " ".join(formatted_parts)


def extract_skills_section(body_text: str) -> list[str]:
    """
    Find a 'Required Skills' (or similar) labeled section in the JD body
    and return the skill names found in it.

    Returns a list of skill strings (may be empty if no section found).
    This is the fast, zero-cost, high-precision path.
    """
    match = _SKILLS_SECTION_RE.search(body_text)
    if not match:
        return []

    section = match.group(1)
    skills: list[str] = []
    seen: set[str] = set()

    for line in section.split('\n'):
        # Strip bullet characters, numbers, leading/trailing whitespace
        line = re.sub(r'^[\s\*\•\-\–\—\d\.\)]+', '', line).strip()
        if not line or _SKIP_LINE_RE.match(line):
            continue

        # Take everything before a dash/colon description
        # e.g. "Python – used for scripting" → "Python"
        skill = re.split(r'\s*[\-–:]\s+', line)[0].strip()

        # Sanity: real skill names are short and don't start with a digit
        if skill and 1 < len(skill) < 55 and not skill[0].isdigit():
            key = skill.lower()
            if key not in seen:
                seen.add(key)
                skills.append(skill)

    return skills


class SkillRanker:
    """
    Ranks a candidate's personal skills by semantic relevance to a specific
    job description using a locally-run sentence-transformer model.

    Falls back gracefully if sentence-transformers is not installed.
    """

    # all-MiniLM-L6-v2: 80 MB, CPU-friendly, ~50 ms per encode
    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, my_skills: list[str], log_callback=None):
        self.log = log_callback or logger.info
        self.my_skills = [s.strip() for s in my_skills if s.strip()]
        self._model = None
        self._skill_embeddings = None

        if not _ST_AVAILABLE:
            self.log(
                "[SkillRanker] WARNING: sentence-transformers not installed. "
                "Install with: pip install sentence-transformers\n"
                "              Falling back to regex keyword extraction."
            )
            return

        if len(self.my_skills) < 1:
            self.log("[SkillRanker] No personal skills configured -- RAG disabled.")
            return

        try:
            self.log(
                f"[SkillRanker] Loading '{self.MODEL_NAME}' model "
                "(first run downloads ~80 MB)..."
            )
            self._model = SentenceTransformer(self.MODEL_NAME)
            # Pre-encode the user's skills once at startup -- reused for every JD
            self._skill_embeddings = self._model.encode(
                self.my_skills,
                convert_to_tensor=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            self.log(
                f"[SkillRanker] OK - Ready. "
                f"Indexed {len(self.my_skills)} personal skill(s)."
            )
        except Exception as exc:
            self.log(f"[SkillRanker] ERROR - Model load failed: {exc}. Using regex fallback.")
            self._model = None
            self._skill_embeddings = None

    @property
    def is_available(self) -> bool:
        """True if the model loaded successfully and is ready to rank."""
        return self._model is not None and self._skill_embeddings is not None

    def rank_skills(self, jd_text: str, top_k: int = 6) -> str:
        """
        Return top_k of YOUR skills most relevant to this JD, comma-separated,
        ranked by cosine similarity to the JD embedding.

        Returns an empty string if ranking is unavailable (model not loaded,
        no personal skills configured, or sentence-transformers not installed).
        """
        if not self.is_available or not jd_text.strip():
            return ""

        try:
            # Encode the JD — semantic meaning is dense in the first ~1500 chars,
            # using more chars adds latency without meaningful accuracy gain.
            jd_embedding = self._model.encode(
                jd_text[:1500],
                convert_to_tensor=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            # Cosine similarity: shape (1, n_skills)
            scores = st_util.cos_sim(jd_embedding, self._skill_embeddings)[0]

            k = min(top_k, len(self.my_skills))
            top_indices = torch.topk(scores, k=k).indices.tolist()

            ranked = [self.my_skills[i] for i in top_indices if scores[i].item() > 0.1]
            if not ranked:
                return ""

            # Format: SHORT skills (<=3 chars) in UPPER, longer ones in Title Case
            formatted = [_fmt_skill(s) for s in ranked]
            return ", ".join(formatted)

        except Exception as exc:
            self.log(f"[SkillRanker] ERROR - Ranking error: {exc}")
            return ""

    def merge_with_section_skills(
        self,
        section_skills: list[str],
        jd_text: str,
        top_k: int = 6,
    ) -> str:
        """
        Best-of-both: merge structured section skills with RAG-ranked
        personal skills.

        Strategy:
          1. Section skills (high precision) fill the first slots.
          2. RAG-ranked personal skills backfill remaining slots — but ONLY
             if they are semantically close to the JD (score > 0.25).
          3. Cap at top_k total.

        This ensures the output always contains the JD's explicit requirements
        AND highlights YOUR matching experience.
        """
        seen: set[str] = set()
        result: list[str] = []

        # --- Slot 1: Structured section skills (highest confidence) ---
        for skill in section_skills:
            if len(result) >= top_k:
                break
            key = skill.lower()
            if key not in seen:
                seen.add(key)
                result.append(_fmt_skill(skill))

        if len(result) >= top_k:
            return ", ".join(result)

        # --- Slot 2: RAG personal skills to fill remaining slots ---
        if not self.is_available:
            return ", ".join(result) if result else ""

        try:
            jd_embedding = self._model.encode(
                jd_text[:1500],
                convert_to_tensor=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            scores = st_util.cos_sim(jd_embedding, self._skill_embeddings)[0]
            k = min(top_k * 2, len(self.my_skills))  # over-fetch, then filter
            top_indices = torch.topk(scores, k=k).indices.tolist()

            for i in top_indices:
                if len(result) >= top_k:
                    break
                score = scores[i].item()
                if score < 0.25:   # too low similarity → not really relevant
                    continue
                skill = self.my_skills[i]
                key = skill.lower()
                if key not in seen:
                    seen.add(key)
                    result.append(_fmt_skill(skill))

        except Exception as exc:
            self.log(f"[SkillRanker] ERROR - Merge ranking error: {exc}")

        return ", ".join(result)
