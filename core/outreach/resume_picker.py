import math
from core.matcher import ResumeMatcher
from core.semantic_matcher import SemanticResumeMatcher

class ResumePicker:
    def __init__(self, profiles):
        self.profiles = profiles
        self.semantic_matcher = None
        self._semantic_initialized = False
        # Start with keyword-only matcher — semantic loads lazily on first pick
        self.matcher = ResumeMatcher(self.profiles, semantic_matcher=None)

    def _ensure_semantic(self):
        """Initialize SemanticResumeMatcher on first actual use (not at startup)."""
        if self._semantic_initialized or not self.profiles:
            return
        print("Initializing Semantic Matcher for Outreach (first use)...")
        self.semantic_matcher = SemanticResumeMatcher(self.profiles)
        self.matcher = ResumeMatcher(self.profiles, semantic_matcher=self.semantic_matcher)
        self._semantic_initialized = True

    def pick_resume(self, job_title, job_description, raw_job=None):
        """
        Takes the JD and title, returns the best matching profile dict.
        """
        if not self.profiles:
            return None

        self._ensure_semantic()

        text_to_score = f"{job_title}\n{job_description}"
        results = self.matcher.score_profiles(text_to_score, job_title=job_title, name_boost_mode="off")

        if not results:
            return self.profiles[0] # fallback to default

        # Results are automatically sorted descending by the Matcher engine.
        best_result = results[0]
        best_match_id = best_result['id']

        if raw_job is not None:
            raw_job["Matched Skills Info"] = {
                "matched_uni": list(best_result.get("matched_uni", [])),
                "matched_gen": list(best_result.get("matched_gen", [])),
                "score": best_result.get("score", 0.0),
                "confidence": best_result.get("confidence_pct", 0.0),
                "name_affinity": best_result.get("name_affinity", 0.0)
            }

        for p in self.profiles:
            if p.get('id') == best_match_id:
                return p

        return self.profiles[0] # fallback

    def extract_recruiter_info(self, job_description):
        """
        Optional: Basic regex or NLP extraction could go here.
        For now, returns empty since Dice doesn't easily expose this in text
        without an LLM.
        """
        import re
        emails = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', job_description)
        phones = re.findall(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', job_description)

        return {
            "emails": emails,
            "phones": phones
        }
