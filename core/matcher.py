"""
Dice Auto Apply Bot - Resume Matching Engine
============================================

This module contains the `ResumeMatcher`, which evaluates a job description 
against multiple candidate resumes and ranks them mathematically. 

Key Algorithms & Features:
--------------------------
1. TF-IDF Inspired Keyword Scoring:
   - Evaluates Term Frequency (TF) using logarithmic scaling: `math.log2(1 + count)`.
   - Distinguishes between "Unique/Priority" keywords (3.0x multiplier) and 
     "General" keywords (1.0x multiplier), mitigating keyword stuffing.
   - Robust NLP regex building enables tolerance for plurals, and safely jumps 
     HTML newlines/spaces.

2. Job Title Affinity Score (Jaccard + Subset Overlap):
   - Compares the `Job Title` against the `Resume Profile Name` by tokenizing 
     and stripping generic stop words (e.g., 'senior', 'lead', 'the').
   - Handles role aliasing/stemming (e.g., 'developer' -> 'engineer', 'dev' -> 'engineer').
   - Calculates overlapping terms via mathematical Jaccard logic and Subset percentages
     to handle cases where the Job Title is very long.

3. Name-Boost Tiebreaker System:
   - EXACT: If Jaccard overlap >= 0.55, the profile gets a massive boost (9999.0 points),
     guaranteeing it wins over general keyword matching.
   - HIGH: Tiebreaker bonus up to 80% of median keyword score.
   - LOW: Tiebreaker bonus up to 20% of median keyword score.
   - OFF: Pure mathematical keyword frequencies.

4. Must-Have Keyword Penalty (NEW):
   - Each profile can optionally declare a `must_have` list in settings.json.
   - If ANY must_have keyword is absent from the job description, that profile
     receives a -9999 penalty, preventing it from winning over more relevant resumes.

5. Keyword Coverage Score (NEW):
   - Measures what % of the job's detected skill terms a profile's keyword list covers.
   - Blended with TF-IDF score: 25% coverage + 75% TF-IDF (prevents broad-keyword spam).

6. Confidence Percentage (NEW):
   - Each result includes a `confidence_pct` (0-100%) for human-readable match quality.

7. Advanced Vector RAG Integrations:
   - Semantic Matching (via fastembed ONNX) adds a dynamic conceptual bonus
     scaled by JD length (short JDs get up to 50% weight, long JDs get 15% min weight).
   - A Learning Engine checks for previously applied and accepted job descriptions with
     Jaccard-based similarity weighting (up to +15, replaces flat +5).
   - Groq AI tiebreaker: when top-2 profiles score within 20%, Groq LLM adjudicates
     with a structured fit_score (up to +20 bonus points).
"""
import re
import math
import functools


class ResumeMatcher:
    # Valid boost mode strings
    BOOST_MODES = ("exact", "high", "low", "off")

    def __init__(self, profiles, semantic_matcher=None, learning_engine=None,
                 groq_scorer=None):
        """
        profiles: list of dictionaries configuring the user's resumes
        semantic_matcher: (optional) SemanticResumeMatcher instance
        learning_engine: (optional) LearningEngine instance
        groq_scorer: (optional) GroqResumeScorer instance for LLM tiebreaking
        """
        self.profiles = profiles
        self.semantic_matcher = semantic_matcher
        self.learning_engine = learning_engine
        self.groq_scorer = groq_scorer
        # Pre-compile must_have patterns once so _check_must_have() doesn't recompile per job
        self._must_have_cache = {
            p.get('id'): [
                (kw, self.build_keyword_pattern(str(kw).strip()))
                for kw in p.get('must_have', []) if str(kw).strip()
            ]
            for p in profiles
        }
        # Cache for compiled single-pass scanner chunks keyed on frozenset of keywords
        self._scanner_cache: dict = {}

    @staticmethod
    @functools.lru_cache(maxsize=512)
    def build_keyword_pattern(kw):
        """
        Builds a robust NLP regex pattern for a given keyword with synonym expansion.
        - Escapes special characters
        - Converts spaces to \\s+ to cleanly jump HTML newlines and extra spaces
        - Adds an optional 's?' for grammatical plural tolerance
        - Automatically expands 60+ common AI/Data/Cloud synonyms
        """
        kw_clean = str(kw).strip().lower()
        if not kw_clean:
            return None

        # Helper to format a single term
        def _fmt(term):
            esc = re.escape(term).replace(r'\ ', r'\s+').replace(' ', r'\s+')
            if term[-1].isalpha() and not term.endswith('s'):
                esc += r's?'
            return esc

        # ── Expanded synonym map (60+ aliases) ─────────────────────────────
        synonyms = {
            # ── GenAI & LLM ──────────────────────────────────────────────
            "gen ai":             ["genai", "generative ai", "gen-ai",
                                   "generative artificial intelligence"],
            "generative ai":      ["genai", "gen ai", "gen-ai"],
            "llm":                ["llms", "large language model", "large language models",
                                   "foundation model", "foundation models"],
            "fine-tuning":        ["fine tuning", "finetuning", "lora", "qlora",
                                   "parameter efficient fine-tuning", "peft"],
            "prompt engineering": ["system prompt", "prompt design", "chain of thought",
                                   "few-shot prompting", "zero-shot prompting"],
            "rag":                ["retrieval augmented generation", "retrieval-augmented",
                                   "retrieval augmented", "rag pipeline", "rag pipelines",
                                   "graph-rag", "graph rag", "graphrag"],
            "agentic ai":         ["ai agent", "ai agents", "agentic workflow",
                                   "agentic llm", "agentic framework", "autonomous agent",
                                   "agentic orchestration", "agent-based"],
            "vector db":          ["vector database", "vector store", "embedding store",
                                   "vector search", "vector index", "vector databases"],

            # ── Agent Frameworks (Graph AI era) ───────────────────────────────────
            "langgraph":          ["lang graph", "lang-graph"],
            "crewai":             ["crew ai", "crew-ai"],
            "multi-agent":        ["multi agent", "multiagent", "multi-agent system",
                                   "multi-agent framework", "agent collaboration"],
            "agent framework":    ["agentic framework", "orchestration framework",
                                   "ai orchestration framework"],
            "autonomous workflow": ["autonomous agent workflow", "agentic workflow",
                                    "self-healing workflow"],
            "ai orchestration":   ["llm orchestration", "model orchestration",
                                   "agent orchestration", "workflow orchestration"],

            # ── Graph Databases ──────────────────────────────────────────────────────
            "neo4j":              ["neo 4j", "graph database", "graph db",
                                   "property graph", "knowledge graph database"],
            "cypher":             ["cypher query", "cypher query language",
                                   "neo4j cypher", "graph query language"],
            "knowledge graph":    ["knowledge graphs", "kg", "ontology graph",
                                   "entity graph", "semantic graph"],
            "graph rag":          ["graph-rag", "graphrag", "graph retrieval augmented",
                                   "graph augmented generation"],

            # ── MLOps Variants ──────────────────────────────────────────────────────
            "llmops":             ["llm ops", "llm operations", "llm monitoring",
                                   "llm evaluation", "llm deployment"],

            # ── AI/ML Frameworks ─────────────────────────────────────────
            "langchain":          ["lang chain", "lang-chain"],
            "hugging face":       ["huggingface", "hf transformers"],
            "mlops":              ["ml operations", "model ops", "mlops platform",
                                   "machine learning operations"],
            "mlflow":             ["ml flow", "ml-flow"],
            "scikit-learn":       ["sklearn", "scikit learn"],

            # ── Cloud Platforms ───────────────────────────────────────────
            "gcp":                ["google cloud platform", "google cloud"],
            "aws":                ["amazon web services"],
            "azure openai":       ["azure openai service", "aoai", "azure open ai"],
            "azure":              ["microsoft azure"],
            "aws bedrock":        ["amazon bedrock", "bedrock"],
            "sage maker":         ["sagemaker", "aws sagemaker", "amazon sagemaker"],
            "vertex ai":          ["google vertex ai", "gcp vertex"],

            # ── Data & ETL ────────────────────────────────────────────────
            "etl":                ["elt", "data pipeline", "data pipelines",
                                   "extract transform load"],
            "dbt":                ["data build tool"],
            "dlt":                ["delta live tables"],
            "adls":               ["azure data lake", "data lake storage", "adls gen2"],
            "snow flake":         ["snowflake", "snow-flake"],
            "databricks":         ["unified analytics platform", "lakehouse platform"],
            "spark":              ["apache spark", "pyspark", "apache spark/pyspark"],
            "kafka":              ["apache kafka", "confluent kafka", "event streaming"],
            "data lakehouse":     ["lakehouse architecture", "delta lakehouse",
                                   "lakehouse"],
            "delta lake":         ["delta tables", "delta format"],
            "unity catalog":      ["uc catalog", "databricks unity catalog"],

            # ── DevOps & Infra ────────────────────────────────────────────
            "kubernetes":         ["k8s", "kube"],
            "ci/cd":              ["continuous integration", "continuous delivery",
                                   "devops pipeline", "cicd", "ci cd"],
            "github actions":     ["gh actions", "github ci"],
            "terraform":          ["tf infra", "infrastructure as code", "iac"],
            "docker":             ["containerization", "container"],
            "openshift":          ["red hat openshift", "ocp"],

            # ── Databases ─────────────────────────────────────────────────
            "postgresql":         ["postgres", "pg database"],
            "mongodb":            ["mongo db", "mongo"],
            "sql server":         ["mssql", "microsoft sql server", "ms sql"],

            # ── BI & Visualization ────────────────────────────────────────
            "power bi":           ["powerbi", "power-bi"],
            "tableau":            ["tableau desktop", "tableau server"],

            # ── AI Copilots & Tools ───────────────────────────────────────
            "copilot":            ["github copilot", "microsoft copilot", "ai copilot"],
            "cursor":             ["cursor ide", "cursor ai"],

            # ── NLP & Classic ML ─────────────────────────────────────────
            "nlp":                ["natural language processing"],
            "ml":                 ["machine learning"],
            "ai":                 ["artificial intelligence"],
            "deep learning":      ["neural network", "neural networks", "dl"],
        }

        terms = [kw_clean] + synonyms.get(kw_clean, [])
        patterns = [_fmt(t) for t in terms]
        combined = "|".join(patterns)

        return r'(?<![a-z0-9])(?:' + combined + r')(?![a-z0-9])'

    @staticmethod
    def _name_affinity_score(profile_name: str, job_title: str) -> float:
        """
        Computes a 0.0-1.0 affinity score.

        Supports slash-separated multi-role profile names:
          e.g. "Agentic AI Engineer / Agentic AI Developer"
          → scored against the job title as TWO separate roles; best score wins.

        Also handles role variants (stemming) and subset matching
        (e.g. 'Azure Data Engineer' matches 'Senior Principal Azure Data Engineering Lead').
        """
        def tokenize(s):
            # Comprehensive role-related stopwords to ignore for name matching
            stop = {
                'a', 'an', 'the', 'and', 'or', 'of', 'in', 'for', 'to', 'with',
                'sr', 'jr', 'senior', 'junior', 'lead', 'leader', 'principal',
                'staff', 'contract', 'contractor', 'remote', 'hybrid', 'onsite',
                'expert', 'specialist', 'professional', 'associate', 'hiring',
                'global', 'international', 'location', 'preferred', 'part', 'time', 'full'
            }
            # Preserving symbols like +, #, . (for C++, C#, .NET)
            raw_tokens = re.split(r'[^a-z0-9\+\#\.]+', s.lower())

            # Simple stemming/aliasing for common roles
            variants = {
                'engineering': 'engineer',
                'developer':   'engineer',
                'dev':         'engineer',
                'analytics':   'analyst',
                'architecture': 'architect',
                'ml':           'ai',
                'aiml':         'ai',
                'scientific':   'scientist',
                'agentic':      'agentic',   # keep — important distinguisher
            }

            tokens = set()
            for t in raw_tokens:
                t = t.strip('.')
                if t and t not in stop:
                    tokens.add(variants.get(t, t))
            return tokens - {''}

        def _score_single(name_part: str, job_title: str) -> float:
            """Score one role name variant against the job title."""
            name_tokens  = tokenize(name_part)
            title_tokens = tokenize(job_title)

            if not name_tokens or not title_tokens:
                return 0.0

            intersection = name_tokens & title_tokens
            union        = name_tokens | title_tokens

            jaccard = len(intersection) / (len(union) or 1)
            subset  = len(intersection) / len(name_tokens)

            if len(name_tokens) <= 1:
                return (jaccard + subset) / 2

            return max(jaccard, subset)

        # ── Multi-role support: split on '/' and score each variant ──────
        name_variants = [v.strip() for v in profile_name.split('/') if v.strip()]
        if not name_variants:
            return 0.0

        # Return the best affinity score across all role variants
        return max(_score_single(variant, job_title) for variant in name_variants)

    def _check_must_have(self, profile: dict, text: str) -> tuple[bool, list]:
        """
        Checks if all must_have keywords for a profile are present in the job text.
        Uses pre-compiled patterns from self._must_have_cache when available.

        Returns:
            (all_present: bool, missing_list: list of missing keywords)

        If the profile has no 'must_have' list, returns (True, []) — no penalty.
        """
        profile_id = profile.get('id')
        compiled = getattr(self, '_must_have_cache', {}).get(profile_id)

        if compiled is not None:
            missing = [kw for kw, pat in compiled if pat and not re.search(pat, text)]
            return (len(missing) == 0), missing

        # Fallback for profiles not in cache (e.g. added at runtime)
        must_have_raw = profile.get("must_have")
        if not must_have_raw or not isinstance(must_have_raw, list):
            return True, []

        missing = []
        for kw in must_have_raw:
            kw_str = str(kw).strip()
            if not kw_str:
                continue
            pattern = ResumeMatcher.build_keyword_pattern(kw_str)
            if pattern and not re.search(pattern, text):
                missing.append(kw_str)

        return (len(missing) == 0), missing

    @staticmethod
    def _coverage_score(profile_keywords: list, tf_map: dict) -> float:
        """
        Computes keyword coverage: what fraction of detected job-skill terms
        the profile's keyword list actually covers.

        coverage = matched_profile_kws / total_kws_found_in_job

        Returns a value 0.0 – 1.0.
        """
        if not tf_map or not profile_keywords:
            return 0.0

        total_job_kws = len(tf_map)
        matched = sum(1 for kw in profile_keywords if kw.lower() in tf_map)
        return matched / total_job_kws

    def score_profiles(self, text, job_title: str = "", name_boost_mode: str = "off",
                       job_id: str = None):
        """
        Takes raw job description text and returns ranked profiles.
        Uses TF weighted scoring with logarithmic scaling to prevent keyword stuffing bias.

        name_boost_mode controls how the profile-name affinity feature works:
          "exact"  - If a profile name matches the job title with >= 0.60 Jaccard
                     affinity it is returned as the automatic winner, skipping all
                     other candidates.  Best for very specific role names.
          "high"   - A strong tiebreaker bonus (up to 80% of median keyword score).
                     The name-matched profile will win unless another profile
                     scored dramatically better on keywords.
          "low"    - A gentle nudge bonus (up to 20% of median keyword score).
                     Keyword quality still dominates; name match is a light hint.
          "off"    - Feature disabled entirely; pure keyword scoring.

        job_id: (optional) Used as a cache key for Groq scorer to avoid duplicate calls.

        self.semantic_matcher: (Optional) If provided, adds a semantic similarity boost.
        self.groq_scorer: (Optional) If provided, performs LLM tiebreaking when top-2
                          profiles score within 20% of each other.
        """
        text_lower = text.lower()

        # 1. Harvest all distinct keywords into a universal set for optimized scanning
        all_kws = set()
        for p in self.profiles:
            uni_kws = p.get('unique_keywords')
            if isinstance(uni_kws, list):
                for k in uni_kws:
                    if str(k).strip():
                        all_kws.add(str(k).strip().lower())

            gen_kws = p.get('keywords')
            if isinstance(gen_kws, list):
                for k in gen_kws:
                    if str(k).strip():
                        all_kws.add(str(k).strip().lower())

        # ── Size-bias correction (NEW) ──────────────────────────────────
        # A profile with a much bigger keyword list has more chances to
        # rack up raw matches, giving it a structural scoring advantage
        # over a smaller, more curated profile regardless of true fit.
        # Normalize every profile's keyword/coverage score toward the
        # median list size across the current resume set so match VOLUME
        # doesn't beat match QUALITY. Clamped so a tiny or huge outlier
        # profile can't swing too far in either direction.
        _kw_counts = []
        for p in self.profiles:
            _u = p.get('unique_keywords')
            _g = p.get('keywords')
            _n = (len(_u) if isinstance(_u, list) else 0) + (len(_g) if isinstance(_g, list) else 0)
            _kw_counts.append(max(1, _n))
        _kw_counts.sort()
        _median_kw_count = _kw_counts[len(_kw_counts) // 2] if _kw_counts else 1

        # 2. Count exact Term Frequencies (TF) — single-pass combined regex (much faster)
        kw_list = sorted(
            [(kw, self.build_keyword_pattern(kw)) for kw in all_kws],
            key=lambda x: x[0]
        )
        kw_list = [(kw, pat) for kw, pat in kw_list if pat]

        cache_key = frozenset(kw for kw, _ in kw_list)
        if cache_key not in self._scanner_cache:
            # Split into chunks of 90 to stay under Python re's 100-group limit
            CHUNK = 90
            chunks = []
            for i in range(0, len(kw_list), CHUNK):
                chunk = kw_list[i:i + CHUNK]
                compiled = re.compile(
                    '|'.join(f'(?P<g{j}>{pat})' for j, (_, pat) in enumerate(chunk))
                )
                mapping = {j: kw for j, (kw, _) in enumerate(chunk)}
                chunks.append((compiled, mapping))
            self._scanner_cache[cache_key] = chunks

        tf_map = {}
        for compiled, kw_by_idx in self._scanner_cache[cache_key]:
            for m in compiled.finditer(text_lower):
                idx = int(m.lastgroup[1:])
                kw = kw_by_idx[idx]
                tf_map[kw] = tf_map.get(kw, 0) + 1

        # 3. Mathematically evaluate each profile
        results = []
        for profile in self.profiles:
            name      = profile.get('name', 'Unknown')
            file_path = profile.get('file_path')

            uni_raw = profile.get('unique_keywords')
            uni_kws = [str(k).strip().lower() for k in uni_raw if str(k).strip()] if isinstance(uni_raw, list) else []

            gen_raw = profile.get('keywords')
            gen_kws = [str(k).strip().lower() for k in gen_raw if str(k).strip()] if isinstance(gen_raw, list) else []

            # Prevent double-dipping: keyword in both lists belongs to Unique only
            uni_set = set(uni_kws)
            gen_kws = [kw for kw in gen_kws if kw not in uni_set]

            used_uni = set(kw for kw in uni_kws if kw in tf_map)
            used_gen = set(kw for kw in gen_kws if kw in tf_map)

            # Algorithmic Weights (TF-IDF style)
            uni_score   = sum(3.0 * math.log2(1 + tf_map[kw]) for kw in used_uni)
            gen_score   = sum(1.0 * math.log2(1 + tf_map[kw]) for kw in used_gen)
            raw_kw_score = uni_score + gen_score

            # ── Coverage Score (NEW) ──────────────────────────────────────
            all_profile_kws = uni_kws + gen_kws
            coverage        = self._coverage_score(all_profile_kws, tf_map)
            raw_coverage_points = coverage * 10.0   # scale: max ~10 bonus points

            # Apply size-bias correction: scale toward the median profile size,
            # clamped to [0.5x, 2.0x] so outliers correct without overshooting.
            profile_kw_count = max(1, len(all_profile_kws))
            size_norm = max(0.5, min(2.0, _median_kw_count / profile_kw_count))
            kw_score         = raw_kw_score * size_norm
            coverage_points  = raw_coverage_points * size_norm

            # Blend: 75% TF-IDF + 25% coverage
            total_score = (kw_score * 0.75) + (coverage_points * 0.25)

            # ── Must-Have Penalty (NEW) ───────────────────────────────────
            all_present, missing_must = self._check_must_have(profile, text_lower)
            must_have_penalty = 0.0
            if not all_present:
                must_have_penalty = -9999.0
                total_score += must_have_penalty
                print(f"  [MUST-HAVE PENALTY] '{name}' missing required skills: "
                      f"{missing_must} — disqualified for this job.")

            # Check name affinity (Moved early)
            affinity = 0.0
            if job_title:
                affinity = round(self._name_affinity_score(name, job_title), 3)

            # Mode selection
            m_mode = profile.get('boost_mode', name_boost_mode) or name_boost_mode
            m_mode = m_mode.strip().lower()

            # Skip profile ONLY if zero keywords AND it doesn't have a high Name Match in "EXACT" mode
            is_good_name_match = (m_mode == "exact" and affinity >= 0.55)
            if not used_uni and not used_gen and not is_good_name_match and all_present:
                continue

            results.append({
                'name':               name,
                'file_path':          file_path,
                'score':              round(total_score, 2),
                'kw_score':           round(kw_score, 2),
                'uni_score':          round(uni_score, 2),
                'gen_score':          round(gen_score, 2),
                'coverage':           round(coverage * 100, 1),   # % for display
                'matched_uni':        used_uni,
                'matched_gen':        used_gen,
                'missing_must_have':  missing_must,
                'must_have_penalty':  must_have_penalty,
                'name_affinity':      affinity,
                'name_boost':         0.0,
                'semantic_score':     0.0,
                'learning_boost':     0.0,
                'groq_score':         0.0,
                'groq_missing':       [],
                'groq_recommendation': '',
                'confidence_pct':     0.0,
                'boost_mode':         m_mode,
                'profile_boost_mode': m_mode,
                'id':                 profile.get('id')
            })

        if not results:
            return results

        # ── 4. Name-affinity boost (final processing) ──────────────────────
        if job_title:
            sorted_scores  = sorted(r['score'] for r in results)
            median_score   = sorted_scores[len(sorted_scores) // 2] if sorted_scores else 1.0

            for r in results:
                # Per-profile mode, falling back to the caller's default
                mode = r.get('profile_boost_mode', name_boost_mode) or name_boost_mode
                mode = mode.strip().lower()
                if mode not in self.BOOST_MODES:
                    mode = "high"
                r['boost_mode'] = mode

                if mode == "off" or not job_title:
                    continue

                if mode == "exact":
                    EXACT_THRESHOLD = 0.55
                    if r['name_affinity'] >= EXACT_THRESHOLD:
                        r['name_boost'] = 9999.0
                        r['score']      = round(r['score'] + 9999.0, 2)
                        print(f"  [EXACT MATCH] '{r['name']}' auto-selected "
                              f"(affinity={r['name_affinity']:.2f}) for job: '{job_title}'")
                else:
                    BOOST_CAP = {"high": 0.80, "low": 0.20}
                    cap_pct   = BOOST_CAP.get(mode, 0.40)
                    MAX_BOOST = median_score * cap_pct
                    bonus     = round(r['name_affinity'] * MAX_BOOST, 2)
                    r['name_boost'] = bonus
                    r['score']      = round(r['score'] + bonus, 2)

        # ── 5. Semantic & Learning Feedback (Vector RAG Logic) ────────────────
        # Dynamic semantic weight: short JDs get higher weight, long JDs rely more on keywords
        word_count  = len(text.split())
        sem_weight  = max(0.15, min(0.50, 300 / max(word_count, 1) * 0.5))

        if self.semantic_matcher and hasattr(self.semantic_matcher, 'model') and self.semantic_matcher.model:
            try:
                sem_results = self.semantic_matcher.score_job(job_title, text)
                sem_map = {r['profile_id']: r['semantic_score'] for r in sem_results}

                for r in results:
                    p_id = r.get('id')
                    s_score = sem_map.get(p_id, 0.0)
                    r['semantic_score'] = s_score

                    # Dynamic semantic bonus (was flat 30%, now scales with JD length)
                    s_bonus = round(s_score * sem_weight, 2)
                    r['score'] = round(r['score'] + s_bonus, 2)
            except Exception as e:
                # Silently fail and use keyword-only matching if AI layer errors
                print(f"Warning: Semantic Matching failed, falling back to keywords: {e}")

        if self.learning_engine:
            for r in results:
                p_id = r.get('id')
                # Use smarter similarity-weighted boost if available, else fallback
                if hasattr(self.learning_engine, 'get_similarity_boost'):
                    try:
                        l_bonus = self.learning_engine.get_similarity_boost(p_id, job_title)
                    except Exception:
                        past_jobs = self.learning_engine.get_past_successes(p_id)
                        l_bonus = 5.0 if past_jobs else 0.0
                else:
                    past_jobs = self.learning_engine.get_past_successes(p_id)
                    l_bonus = 5.0 if past_jobs else 0.0

                if l_bonus > 0:
                    r['learning_boost'] = round(l_bonus, 2)
                    r['score'] = round(r['score'] + l_bonus, 2)

        # ── 6. Groq AI Tiebreaker (LLM adjudication for close calls) ─────────
        if self.groq_scorer and len(results) >= 2:
            # Only compare profiles that are NOT penalized
            valid_results = [r for r in results if r['must_have_penalty'] == 0]
            if len(valid_results) >= 2:
                valid_results.sort(key=lambda x: x['score'], reverse=True)
                top_score    = valid_results[0]['score']
                second_score = valid_results[1]['score']

                # Only fire Groq when scores are genuinely close (within 5%)
                score_gap_pct = abs(top_score - second_score) / max(abs(top_score), 1.0)
                if score_gap_pct <= 0.05:
                    try:
                        for r in valid_results[:2]:   # Groq scores only the top 2 contenders
                            p = next((p for p in self.profiles if p.get('id') == r.get('id')), None)
                            if not p:
                                continue

                            all_kws_list = (p.get('unique_keywords') or []) + (p.get('keywords') or [])
                            groq_result = self.groq_scorer.score_fit(
                                job_title=job_title,
                                job_description=text[:2000],
                                profile_name=r['name'],
                                profile_keywords=all_kws_list,
                                job_id=job_id
                            )

                            g_score = groq_result.get('fit_score', 0)
                            r['groq_score']          = g_score
                            r['groq_missing']        = groq_result.get('missing_skills', [])
                            r['groq_recommendation'] = groq_result.get('recommendation', '')

                            # Groq bonus: up to +20 points (fit_score is 0-100)
                            groq_bonus = round(g_score * 0.20, 2)
                            r['score'] = round(r['score'] + groq_bonus, 2)

                        print(f"  [GROQ TIEBREAK] Scores close ({score_gap_pct:.1%} gap) — "
                              f"Groq adjudicated. Winner: '{valid_results[0]['name']}'")
                    except Exception as e:
                        print(f"Warning: Groq tiebreaker failed, using keyword ranking: {e}")

        # Rank by total mathematical score descending (re-sort after all boosts)
        results.sort(key=lambda x: x['score'], reverse=True)

        # ── 7. Confidence Percentage (NEW) ────────────────────────────────────
        # Compute after final sort so we use the winning score as the reference
        valid_scores = [r['score'] for r in results if r['score'] > 0]
        max_score    = max(valid_scores) if valid_scores else 1.0

        for r in results:
            raw_conf = (r['score'] / max_score) * 100 if max_score > 0 else 0.0
            r['confidence_pct'] = round(max(0.0, min(100.0, raw_conf)), 1)

        return results
