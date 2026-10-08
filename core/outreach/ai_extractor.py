"""
AIExtractor – Groq-powered fallback for location & keyword extraction.

Only called when regex extraction returns empty results, so it adds
minimal latency to the overall pipeline (regex handles most cases).

Model: openai/gpt-oss-20b (llama-3.1-8b-instant was retired by Groq)
"""

import json
import re
import hashlib


class AIExtractor:
    """
    Lightweight Groq wrapper for extracting structured fields from
    unstructured job description text.

    Usage:
        extractor = AIExtractor(api_key="gsk_...")
        result = extractor.extract(title="AWS Data Engineer - ...", body="...")
        location = result.get("location", "")
        keywords = result.get("keywords", "")
    """

    # llama-3.1-8b-instant was retired by Groq — replaced with their current
    # open-weight lineup. This is a reasoning model: pass reasoning_effort="low"
    # on every call, otherwise it can burn the whole max_tokens budget on
    # internal reasoning and return empty/truncated content.
    MODEL = "openai/gpt-oss-20b"
    MAX_BODY_CHARS = 2000   # Truncate body to save tokens & reduce latency
    MAX_TOKENS     = 350    # Enough for full 5-field JSON — 200 truncated mid-output on the current model
    
    # Persist cache across multiple pipeline instantiations within the same app session
    _GLOBAL_CACHE = {}
    _GLOBAL_CALL_COUNT = 0   # Tracks total Groq API calls this session

    @classmethod
    def get_call_count(cls) -> int:
        return cls._GLOBAL_CALL_COUNT

    @classmethod
    def reset_call_count(cls):
        cls._GLOBAL_CALL_COUNT = 0

    def __init__(self, api_key: str, log_callback=None):
        self._api_key = api_key
        self._client  = None   # Lazy init — only create when first used
        self.log = log_callback or print

    def _get_client(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    def extract(self, title: str, body: str) -> dict:
        """
        Ask the LLM to extract location and top keywords from the JD.

        Returns a dict with keys:
            "job_title": str  e.g. "AWS Data Engineer"
            "location" : str  e.g. "Austin, TX" / "Remote" / ""
            "keywords" : str  e.g. "Python, AWS, Databricks, Snowflake"

        Never raises — returns empty strings on any error so the pipeline
        can fall back gracefully.
        """
        if not self._api_key:
            return {"job_title": "", "location": "", "keywords": "", "company": ""}
            
        cache_key = hashlib.md5(f"extract:{title}:{body}".encode('utf-8')).hexdigest()
        if cache_key in self.__class__._GLOBAL_CACHE:
            return self.__class__._GLOBAL_CACHE[cache_key]

        truncated_body = body[:self.MAX_BODY_CHARS]

        prompt = (
            "You are a job description parser. Extract exactly five fields "
            "from the job title and description below.\n\n"
            "Rules:\n"
            "- is_job_post: true if this text represents an actual job posting or someone actively hiring/looking for candidates. false if it is a general rant, meme, article, or unrelated story.\n"
            "- job_title: extract ONLY the clean, professional job title. "
            "NEVER include recruiting noise words or experience requirements such as: Required, Needed, Need, Wanted, Urgent, Urgently, Immediate, Immediately, ASAP, Hot, Opening, Opportunity, Hiring, Position, Role, Req, Requirement, JD, W2, C2C, Remote, Hybrid, Onsite, Contract, Fulltime, 15+, 10+, years of experience, or any location. "
            "BAD: 'Senior Data Engineer Required', 'Urgent - Python Developer Needed', '15+ Years Experience Data Engineer', 'Immediate Opening: ML Engineer'. "
            "GOOD: 'Senior Data Engineer', 'Python Developer', 'ML Engineer'.\n"
            "- location: city + state (e.g. 'Austin, TX') OR work mode "
            "('Remote', 'Hybrid', 'Onsite'). Empty string if not mentioned.\n"
            "- keywords: up to 6 most important tech skills, comma-separated "
            "(e.g. 'Python, AWS, Snowflake, Spark'). Only real tech terms.\n"
            "- company: the name of the hiring company or recruiting agency. If not found, use an empty string.\n\n"
            "Respond ONLY with a valid JSON object like:\n"
            "{\"is_job_post\": true, \"job_title\": \"Senior Data Engineer\", \"location\": \"Austin, TX\", \"keywords\": \"Python, AWS, Spark\", \"company\": \"Tech Corp\"}\n\n"
            f"Job Title: {title}\n\n"
            f"Job Description:\n{truncated_body}"
        )

        try:
            client = self._get_client()
            import time
            time.sleep(1.5)  # Prevent burst rate limit on Groq
            response = client.chat.completions.create(
                model=self.MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.MAX_TOKENS,
                temperature=0,        # Deterministic output
                reasoning_effort="low",
            )
            self.__class__._GLOBAL_CALL_COUNT += 1
            raw = response.choices[0].message.content.strip()

            # Extract JSON block even if the model is chatty
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end != -1 and end >= start:
                raw = raw[start:end+1]
            else:
                raise ValueError("No JSON object found in response")

            parsed = json.loads(raw)
            result = {
                "is_job_post": bool(parsed.get("is_job_post", True)),
                "job_title": str(parsed.get("job_title", "")).strip(),
                "location": str(parsed.get("location", "")).strip(),
                "keywords": str(parsed.get("keywords", "")).strip(),
                "company": str(parsed.get("company", "")).strip(),
            }
            self.__class__._GLOBAL_CACHE[cache_key] = result
            return result

        except Exception as e:
            # Never crash the pipeline — just return empty and let regex
            # results (even if empty) pass through
            self.log(f"[AIExtractor] ❌ Warning: {e}")
            return {"job_title": "", "location": "", "keywords": "", "company": ""}

    def generate_email_body(self, title: str, company: str, body: str, my_skills: str) -> str:
        """
        Ask the LLM to generate a customized, 3-sentence outreach email.
        """
        if not self._api_key:
            return ""
            
        cache_key = hashlib.md5(f"draft:{title}:{company}:{body}:{my_skills}".encode('utf-8')).hexdigest()
        if cache_key in self.__class__._GLOBAL_CACHE:
            return self.__class__._GLOBAL_CACHE[cache_key]

        truncated_body = body[:self.MAX_BODY_CHARS]
        
        prompt = (
            "You are an expert technical job seeker writing a direct, concise outreach email.\n"
            "Write a professional 3-4 sentence email body applying for the given role.\n\n"
            "Rules:\n"
            "- Do NOT include a Subject line.\n"
            "- Do NOT write 'Hi Hiring Team' or 'Hi Recruiter' — the greeting will be added separately.\n"
            "- Begin directly with the first sentence of the body (e.g. 'I came across your listing...').\n"
            "- Mention the specific job title and company in the first sentence.\n"
            "- In 1-2 sentences, highlight how the candidate's core skills directly address the job needs.\n"
            "- End with a brief call to action (e.g. 'I would love to connect.').\n"
            "- Do NOT add a closing or signature (e.g. do NOT write 'Best regards' or '[Your Name]').\n"
            "- Keep it under 80 words. Do not write a cover letter.\n\n"
            f"Candidate Core Skills: {my_skills}\n"
            f"Job Title: {title}\n"
            f"Company: {company}\n"
            f"Job Description:\n{truncated_body}"
        )

        try:
            client = self._get_client()
            import time
            time.sleep(1.5)  # Prevent burst rate limit on Groq
            response = client.chat.completions.create(
                model=self.MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=350,   # 150 truncated mid-sentence on the current reasoning model
                temperature=0.3,
                reasoning_effort="low",
            )
            self.__class__._GLOBAL_CALL_COUNT += 1
            email_text = response.choices[0].message.content.strip()
            self.__class__._GLOBAL_CACHE[cache_key] = email_text
            return email_text
        except Exception as e:
            self.log(f"[AIExtractor] ❌ Email Generation Warning: {e}")
            return ""
