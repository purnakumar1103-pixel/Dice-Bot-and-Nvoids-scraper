import hashlib
import json
import os
import re
import datetime
from core.file_utils import extract_text_from_file

CACHE_DIR = "data/keyword_cache"

class ResumeKeywordScanner:
    """
    Extracts tech keywords from resumes and job descriptions using Groq LLM.
    Caches resume scans by file hash to avoid redundant API calls.
    """
    def __init__(self, groq_scorer):
        self.groq_scorer = groq_scorer
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
            
    def scan_resume(self, file_path: str) -> dict:
        """
        Reads a resume file, sends to Groq to extract tech keywords, 
        and classifies them into unique (specialized) vs general.
        Caches the result using the file's MD5 hash.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Resume not found at {file_path}")
            
        # Get file hash
        with open(file_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
            
        cache_path = os.path.join(CACHE_DIR, f"resume_{file_hash}.json")
        if os.path.exists(cache_path):
            from core.groq_resume_scorer import GroqResumeScorer
            if isinstance(self.groq_scorer, GroqResumeScorer):
                GroqResumeScorer._CACHED_CALLS += 1
            with open(cache_path, 'r') as f:
                return json.load(f)
                
        # Not cached, read text
        text = extract_text_from_file(file_path)
        if not text:
            raise ValueError(f"Could not extract text from {file_path}")
            
        return self._extract_keywords_from_text(text, cache_path, is_resume=True)

    def extract_jd_keywords(self, jd_text: str, job_id: str = None) -> dict:
        """
        Extracts tech keywords from a job description.
        Caches by job_id if provided.
        """
        if job_id:
            cache_path = os.path.join(CACHE_DIR, f"jd_{job_id}.json")
            if os.path.exists(cache_path):
                from core.groq_resume_scorer import GroqResumeScorer
                if isinstance(self.groq_scorer, GroqResumeScorer):
                    GroqResumeScorer._CACHED_CALLS += 1
                with open(cache_path, 'r') as f:
                    return json.load(f)
        else:
            cache_path = None
            
        return self._extract_keywords_from_text(jd_text, cache_path, is_resume=False)

    def _extract_keywords_from_text(self, text: str, cache_path: str, is_resume: bool) -> dict:
        """Helper to call Groq for extraction and caching."""
        if not self.groq_scorer or not getattr(self.groq_scorer, '_api_key', None):
            return {"unique": [], "general": []}
            
        # Limit text length to avoid token limit
        text = text[:8000]
        
        prompt = (
            "You are an expert IT recruiter and tech keyword extractor.\n"
            "Extract ALL technical skills, tools, frameworks, languages, and specific domain concepts "
            f"from the following {'resume' if is_resume else 'job description'}.\n\n"
            "RULES:\n"
            "1. ONLY extract hard skills and technologies (e.g. 'React', 'Neo4j', 'Kubernetes', 'AWS IAM', 'Graph RAG').\n"
            "2. DO NOT extract soft skills (e.g. 'communication', 'leadership', 'agile', 'mentoring', 'teamwork').\n"
            "3. Classify each keyword as either 'unique' (specialized/advanced frameworks, niche tools like LangGraph, Neo4j, CrewAI) "
            "or 'general' (common skills like Python, SQL, Docker, Git).\n"
            "4. CRITICAL: You MUST extract an exhaustive list. Aim to extract 40 to 80+ keywords for a standard resume. Read sentence-by-sentence and extract every single technology, framework, database, or tool mentioned.\n"
            "5. DO NOT REPEAT ANY KEYWORDS.\n"
            "6. Return strictly valid JSON with this exact structure:\n"
            "{\n"
            "  \"unique\": [\"LangGraph\", \"Neo4j\", ...],\n"
            "  \"general\": [\"Python\", \"SQL\", ...]\n"
            "}\n"
            "Do not output markdown blocks or any other text.\n\n"
            f"TEXT TO ANALYZE:\n{text}"
        )
        
        try:
            client = self.groq_scorer._get_client()
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.groq_scorer.MODEL,
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"}
            )
            from core.groq_resume_scorer import GroqResumeScorer
            if isinstance(self.groq_scorer, GroqResumeScorer):
                GroqResumeScorer._TOTAL_CALLS += 1
                GroqResumeScorer._LAST_CALL_TIME = datetime.datetime.now().strftime("%H:%M:%S")
            raw_result = response.choices[0].message.content.strip()

            # Clean possible markdown formatting (```json, ```, etc)
            raw_result = re.sub(r"^```(?:json)?\s*", "", raw_result, flags=re.IGNORECASE)
            raw_result = re.sub(r"\s*```$", "", raw_result)
                
            parsed = json.loads(raw_result.strip())
            
            result = {
                "unique": [str(k) for k in parsed.get("unique", [])],
                "general": [str(k) for k in parsed.get("general", [])]
            }
            
            if cache_path:
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
                    
            return result
        except Exception as e:
            raise RuntimeError(f"Groq API Error: {e}")

    def find_gaps(self, resume_kws: dict, jd_kws: dict, profile_dict: dict) -> dict:
        """
        Intersection logic: returns keywords that are in BOTH resume and JD, 
        but MISSING from the profile's keyword lists.
        """
        # Normalize to lowercase for comparison
        def _norm(lst): return {k.lower(): k for k in lst}
        
        res_unique = _norm(resume_kws.get("unique", []))
        res_general = _norm(resume_kws.get("general", []))
        res_all = {**res_general, **res_unique}
        
        jd_unique = _norm(jd_kws.get("unique", []))
        jd_general = _norm(jd_kws.get("general", []))
        jd_all = {**jd_general, **jd_unique}
        
        # Current profile lists
        prof_unique_list = profile_dict.get("unique_keywords", [])
        prof_general_list = profile_dict.get("keywords", [])
        
        # Build set of all synonyms of current profile keywords
        # This prevents suggesting "Neo4j" if "Neo 4j" is already in profile
        from core.matcher import ResumeMatcher
        prof_all_regex = []
        for kw in prof_unique_list + prof_general_list:
            pat = ResumeMatcher.build_keyword_pattern(kw)
            if pat: prof_all_regex.append(re.compile(pat, re.IGNORECASE))
            
        def _is_in_profile(kw):
            for pat in prof_all_regex:
                if pat.search(kw): return True
            return False

        # Find the intersection (in resume AND in JD)
        intersection_keys = set(res_all.keys()).intersection(set(jd_all.keys()))
        
        gaps = {"unique": [], "general": []}
        
        for key in intersection_keys:
            if not _is_in_profile(key):
                original_case = res_all[key]
                # Keep classification from the JD extraction (or resume)
                if key in jd_unique or key in res_unique:
                    gaps["unique"].append(original_case)
                else:
                    gaps["general"].append(original_case)
                    
        return gaps
