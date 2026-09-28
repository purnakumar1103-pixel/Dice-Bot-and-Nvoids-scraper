"""
Groq Resume Scorer
==================

Extends the existing Groq AI integration (used in the Outreach pipeline) into
the main Dice bot job-application matching loop.

Uses llama-3.1-8b-instant via Groq's free-tier API (~80ms, 14,400 req/day)
to perform intelligent resume-to-job fit scoring when the keyword-based matcher
returns two profiles that are too close to call (within 20% score difference).

The LLM evaluates:
  - Which profile keywords semantically match the job's requirements
  - What critical skills are missing from each profile
  - An overall 0-100 fit score
  - A short recommendation string

Key Design Decisions:
  - API calls are gated behind a "closeness" threshold in matcher.py, so Groq
    is only called when keyword scoring is genuinely ambiguous.
  - All results are cached in-memory per (profile_id, job_id) pair to avoid
    redundant API calls across multiple runs in the same session.
  - The same API key from outreach_settings.json is reused — no new credential needed.
  - Graceful degradation: any Groq failure returns a zero score so the keyword
    ranking is preserved unchanged.
"""

import json
import hashlib


class GroqResumeScorer:
    """
    LLM-powered resume fit scorer using Groq's ultra-fast inference API.

    Usage:
        scorer = GroqResumeScorer(api_key="gsk_...")
        result = scorer.score_fit(
            job_title="Senior LLM Engineer",
            job_description="We need...",
            profile_name="Agentic AI Engineer",
            profile_keywords=["LangGraph", "RAG", "Python", ...],
            job_id="dice-job-12345"       # optional, used for caching
        )
        # result → {fit_score: 87, missing_skills: [...], matching_skills: [...], recommendation: "..."}
    """

    # llama-3.1/3.3 were retired by Groq — replaced with their current open-weight lineup.
    # Both are reasoning models: pass reasoning_effort="low" on every call, otherwise
    # they can burn the whole max_tokens budget on internal reasoning and return empty content.
    SCORE_MODEL    = "openai/gpt-oss-20b"       # fast — sufficient for numeric scoring
    EXTRACT_MODEL  = "openai/gpt-oss-120b"      # accurate — used only for resume auto-extract
    MAX_DESC_CHARS = 2000     # Truncate JD to keep token cost low
    MAX_KW_COUNT   = 30       # Send only top N profile keywords to Groq
    MAX_TOKENS     = 350      # short JSON response, but 120 truncated mid-output on the current model

    # Session-level cache: avoids re-calling Groq for the same (profile, job) pair
    _CACHE: dict = {}
    _TOTAL_CALLS: int = 0
    _CACHED_CALLS: int = 0
    _LAST_CALL_TIME: str = "Never"

    @classmethod
    def get_stats(cls) -> dict:
        return {
            "total": cls._TOTAL_CALLS,
            "cached": cls._CACHED_CALLS,
            "last_time": cls._LAST_CALL_TIME
        }

    def __init__(self, api_key: str, log_callback=None):
        self._api_keys = [k.strip() for k in api_key.split(',')] if api_key else []
        self._current_key_idx = 0
        self._client  = None   # Lazy-init — only created when first used
        self.log      = log_callback or print

    def _get_client(self):
        if self._client is None and self._api_keys:
            from groq import Groq
            self._client = Groq(api_key=self._api_keys[self._current_key_idx])
        return self._client

    def score_fit(self, job_title: str, job_description: str,
                  profile_name: str, profile_keywords: list,
                  job_id: str = None) -> dict:
        """
        Asks Groq LLM to evaluate how well the profile fits the job.

        Returns dict with keys:
            fit_score       (int  0-100)  — overall match quality
            matching_skills (list[str])   — skills found in both profile and JD
            missing_skills  (list[str])   — important JD skills absent from profile
            recommendation  (str)         — one-sentence human-readable summary

        Never raises — returns zero-score dict on any error so the pipeline
        degrades gracefully to keyword-only ranking.
        """
        if not self._api_keys:
            return self._empty()

        # Build cache key from profile name + job identifier (or job description hash)
        key_source = f"{profile_name}|{job_id or job_description[:500]}"
        cache_key  = hashlib.md5(key_source.encode("utf-8")).hexdigest()

        if cache_key in self.__class__._CACHE:
            self.__class__._CACHED_CALLS += 1
            return self.__class__._CACHE[cache_key]

        # Trim keyword list to avoid token bloat
        kw_sample = profile_keywords[:self.MAX_KW_COUNT]
        kw_str    = ", ".join(str(k) for k in kw_sample)
        desc_trunc = job_description[:self.MAX_DESC_CHARS]

        prompt = (
            "You are a technical recruiter evaluating resume fit.\n"
            "Given the job title, job description, and a candidate's profile name "
            "and key skills, return a JSON object with exactly four fields:\n\n"
            "  fit_score       : integer 0-100 (100 = perfect match)\n"
            "  matching_skills : JSON array of skill strings found in BOTH the "
            "profile AND the job description (max 6 items)\n"
            "  missing_skills  : JSON array of important job-required skills NOT "
            "present in the profile keywords (max 4 items)\n"
            "  recommendation  : one concise sentence (max 20 words) explaining "
            "the fit quality\n\n"
            "Respond ONLY with valid JSON. No explanation outside the JSON object.\n\n"
            f"Job Title: {job_title}\n\n"
            f"Job Description:\n{desc_trunc}\n\n"
            f"Profile Name: {profile_name}\n"
            f"Profile Keywords: {kw_str}\n"
        )

        import time, datetime

        while self._current_key_idx < len(self._api_keys):
            try:
                client = self._get_client()
                if not client:
                    break
                response = client.chat.completions.create(
                    model=self.SCORE_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.MAX_TOKENS,
                    temperature=0,
                    reasoning_effort="low",
                )

                self.__class__._TOTAL_CALLS += 1
                self.__class__._LAST_CALL_TIME = datetime.datetime.now().strftime("%H:%M:%S")
                raw = response.choices[0].message.content.strip()

                # Extract the JSON block robustly even if model adds prose
                start = raw.find('{')
                end   = raw.rfind('}')
                if start != -1 and end >= start:
                    raw = raw[start:end + 1]
                else:
                    raise ValueError("No JSON object found in Groq response")

                parsed = json.loads(raw)

                result = {
                    "fit_score":       int(parsed.get("fit_score", 0)),
                    "matching_skills": [str(s) for s in parsed.get("matching_skills", [])],
                    "missing_skills":  [str(s) for s in parsed.get("missing_skills", [])],
                    "recommendation":  str(parsed.get("recommendation", "")).strip(),
                }

                # Clamp fit_score to valid range
                result["fit_score"] = max(0, min(100, result["fit_score"]))

                self.__class__._CACHE[cache_key] = result
                self.log(f"[GroqScorer] ✅ '{profile_name}' → fit_score={result['fit_score']} "
                         f"| missing={result['missing_skills']}")
                return result

            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str or "rate_limit_exceeded" in exc_str:
                    # Check for Retry-After header before rotating keys
                    retry_after = None
                    try:
                        import re as _re
                        m = _re.search(r'retry.after["\s:]+(\d+(?:\.\d+)?)', exc_str, _re.I)
                        if m:
                            retry_after = float(m.group(1))
                    except Exception:
                        pass

                    if retry_after and retry_after <= 10:
                        self.log(f"[GroqScorer] ⚠️ Rate limited — retrying in {retry_after}s")
                        time.sleep(retry_after + 0.1)
                        continue  # retry same key

                    # No short Retry-After — rotate to next key
                    self._current_key_idx += 1
                    if self._current_key_idx < len(self._api_keys):
                        self.log("[GroqScorer] ⚠️ Key rate limited — rotating to next API key...")
                        self._client = None
                        continue
                self.log(f"[GroqScorer] ⚠️  Scoring failed for '{profile_name}': {exc}")
                return self._empty()

        return self._empty()

    @staticmethod
    def _empty() -> dict:
        """Returns a safe zero-score dict when Groq is unavailable or fails."""
        return {
            "fit_score":       0,
            "matching_skills": [],
            "missing_skills":  [],
            "recommendation":  "",
        }

    @classmethod
    def auto_extract_profile(cls, api_key: str, resume_text: str, log_callback=None) -> dict:
        """
        Uses Groq AI to parse a raw resume and automatically extract a profile name,
        priority skills, and general skills.
        """
        keys = [k.strip() for k in api_key.split(',')] if api_key else []
        if not keys:
            return {}

        prompt = (
            "You are an expert technical recruiter analyzing a resume.\n"
            "Based on the resume text below, extract the following into a valid JSON object:\n"
            "1. 'name': A concise, professional job title that best fits this candidate (e.g. 'Data Engineer', 'Backend Developer').\n"
            "2. 'unique_keywords': A list of all highly specialized, priority, or unique technical skills found in the resume.\n"
            "3. 'keywords': A list of all general technical skills, tools, and methodologies found in the resume.\n"
            "\n"
            "IMPORTANT RULES:\n"
            "- Output ONLY valid JSON, nothing else.\n"
            "- Extract as many relevant skills as possible for both keyword lists. Do not artificially limit them.\n"
            "- Do not include any introductory or explanatory text.\n"
            "\n"
            f"RESUME TEXT:\n{resume_text[:15000]}"
        )

        try:
            from groq import Groq
            for i, key in enumerate(keys):
                try:
                    client = Groq(api_key=key)
                    try:
                        response = client.chat.completions.create(
                            model=cls.EXTRACT_MODEL,
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=4000,  # real resumes need 100+ keywords extracted — 1000 truncated mid-JSON
                            temperature=0.2,
                            reasoning_effort="low",
                        )
                    except Exception as e:
                        if "429" in str(e) or "rate_limit_exceeded" in str(e):
                            response = client.chat.completions.create(
                                model=cls.SCORE_MODEL,
                                messages=[{"role": "user", "content": prompt}],
                                max_tokens=4000,  # real resumes need 100+ keywords extracted — 1000 truncated mid-JSON
                                temperature=0.2,
                                reasoning_effort="low",
                            )
                        else:
                            raise e
                    break  # Success
                except Exception as inner_e:
                    if "429" in str(inner_e) or "rate_limit_exceeded" in str(inner_e):
                        if i < len(keys) - 1:
                            if log_callback: log_callback("[GroqScorer] ⚠️ Key rate limited during auto-extract! Rotating...")
                            continue
                    raise inner_e
                    
            raw = response.choices[0].message.content.strip()

            start = raw.find('{')
            end   = raw.rfind('}')
            if start != -1 and end >= start:
                raw = raw[start:end + 1]
            else:
                raise ValueError("No JSON object found")

            import json
            parsed = json.loads(raw)
            return {
                "name": str(parsed.get("name", "Unknown Profile")),
                "unique_keywords": [str(s) for s in parsed.get("unique_keywords", [])],
                "keywords": [str(s) for s in parsed.get("keywords", [])],
            }
        except Exception as exc:
            if log_callback:
                log_callback(f"[GroqScorer] Auto-extract failed: {exc}")
            return {}

if __name__ == "__main__":
    # Quick smoke test — replace with your real key
    import os
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        print("Set GROQ_API_KEY env var to test.")
    else:
        scorer = GroqResumeScorer(api_key=key)
        r = scorer.score_fit(
            job_title="LLM Engineer",
            job_description="We need a Python developer with LangChain, RAG, and AWS Bedrock experience.",
            profile_name="Agentic AI Engineer",
            profile_keywords=["Python", "LangChain", "RAG", "LangGraph", "AWS Bedrock", "Snowflake"]
        )
        print(r)
