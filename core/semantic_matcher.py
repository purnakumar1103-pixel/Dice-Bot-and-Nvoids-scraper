"""
Semantic Resume Matcher
=======================

Uses fastembed (ONNX runtime) for dense-vector semantic matching.
Replaces sentence-transformers / PyTorch — 4-10x faster inference,
~2 GB less RAM, no GPU required.

Two-level embedding cache:
  Level 1 — in-memory LRU (bounded at 512 entries per session)
  Level 2 — disk cache under data/embedding_cache/ (survives restarts)

Cache key: SHA-256 of the full input text.
"""

import os
import re
import hashlib
import numpy as np
from core.file_utils import extract_text_from_file

# Matches a resume date range like "Nov 2024 – Present" or "Oct 2023 – Nov 2024".
# Used purely as a structural anchor to find where each role/employer block
# starts — no date values are parsed or compared, since resumes are written
# newest-first by convention, so document order alone gives recency order.
_DATE_RANGE_RE = re.compile(
    r'([A-Z][a-z]{2,8}\.?\s+\d{4})\s*[-–—]\s*(Present|[A-Z][a-z]{2,8}\.?\s+\d{4})'
)

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "embedding_cache")

# Map legacy sentence-transformers model names to fastembed equivalents
_MODEL_ALIASES = {
    "all-MiniLM-L6-v2":             "sentence-transformers/all-MiniLM-L6-v2",
    "all-mpnet-base-v2":             "BAAI/bge-base-en-v1.5",
    "paraphrase-MiniLM-L6-v2":      "sentence-transformers/paraphrase-MiniLM-L6-v2",
}


class SemanticResumeMatcher:
    """
    Dense-vector semantic matcher backed by fastembed ONNX inference.

    Args:
        profiles   (list): Profile dicts from user configuration.
        model_name (str):  FastEmbed model name.  Default is the ONNX-optimized
                           all-MiniLM-L6-v2 (same quality as the PyTorch version,
                           ~4x faster on CPU, ~80 MB download).
    """

    def __init__(self, profiles, model_name="all-MiniLM-L6-v2"):
        self.profiles = profiles
        # Resolve legacy names to fastembed model IDs
        self.model_name = _MODEL_ALIASES.get(model_name, model_name)
        self.model = None
        self.profile_embeddings: dict = {}
        self._mem_cache: dict = {}
        os.makedirs(_CACHE_DIR, exist_ok=True)
        self._initialize_model()
        self._index_profiles()

    # ── Model loading ─────────────────────────────────────────────────────────

    def _initialize_model(self):
        try:
            from fastembed import TextEmbedding
            print(f"Loading fastembed model: {self.model_name} (ONNX / CPU)...")
            self.model = TextEmbedding(model_name=self.model_name)
            print("Fastembed model loaded.")
        except Exception as e:
            print(f"Error loading fastembed model: {e}")

    # ── Embedding + cache ─────────────────────────────────────────────────────

    def _raw_embed(self, text: str) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Embedding model is not loaded.")
        return next(self.model.embed([text]))

    def _cache_path(self, key: str) -> str:
        return os.path.join(_CACHE_DIR, f"{key}.npy")

    def _embed_cached(self, text: str) -> np.ndarray:
        """
        Two-level cached embed: memory → disk → compute.
        Key: SHA-256 of the FULL text. Hashing only a short prefix caused
        real collisions — resumes built from the same template share an
        identical opening (name/title/"PROFESSIONAL SUMMARY..."), so a
        prefix-only hash made multiple genuinely-different resumes share
        one cached embedding vector (i.e. score using someone else's content).
        """
        key = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

        # Level 1: in-memory
        if key in self._mem_cache:
            return self._mem_cache[key]

        # Level 2: disk
        disk_path = self._cache_path(key)
        if os.path.exists(disk_path):
            try:
                vec = np.load(disk_path)
                self._mem_cache_set(key, vec)
                return vec
            except Exception:
                pass

        # Compute and persist
        vec = self._raw_embed(text)
        try:
            np.save(disk_path, vec)
        except Exception:
            pass
        self._mem_cache_set(key, vec)
        return vec

    def _mem_cache_set(self, key: str, vec: np.ndarray):
        """Insert into memory cache; evict oldest entry if over 512."""
        if len(self._mem_cache) >= 512:
            self._mem_cache.pop(next(iter(self._mem_cache)))
        self._mem_cache[key] = vec

    # ── Profile indexing ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_recent_experience(resume_text: str, n: int = 2,
                                    back_chars: int = 100, cap_chars: int = 2500) -> str:
        """Returns the text of the N most recent employer/role blocks from a resume.

        Earlier approach embedded the raw resume file text, but the embedding
        model only reads roughly its first ~150 words (verified empirically),
        and every resume variant shares the same long template opening
        ("PROFESSIONAL SUMMARY..."), so that gave near-identical vectors
        regardless of actual specialization. A later fix embedded the curated
        keyword list instead, which fixed the collision but only compares
        flat skill tags, not real project narrative.

        This extracts the actual text of the 2 most recent roles instead —
        the richest, most specific, most current signal of what a resume
        variant is really positioned for. Anchored on each role's date range
        (e.g. "Nov 2024 – Present") purely as a structural marker to find
        block boundaries; no date values are parsed or compared, since resumes
        are written newest-first by convention, so document order alone
        already gives recency order. Tolerant of layout differences (company
        name on its own line vs. sharing the date's line) since each block is
        a character window anchored on the date match, not a fixed line offset.
        """
        idx = resume_text.upper().find("PROFESSIONAL EXPERIENCE")
        if idx == -1:
            idx = resume_text.upper().find("WORK EXPERIENCE")
        if idx == -1:
            idx = 0
        exp_text = resume_text[idx:]

        matches = list(_DATE_RANGE_RE.finditer(exp_text))
        if not matches:
            return exp_text[:cap_chars].strip()

        blocks = []
        for i, m in enumerate(matches[:n]):
            prev_end = matches[i - 1].end() if i > 0 else 0
            start = max(prev_end, m.start() - back_chars)
            end   = matches[i + 1].start() if i + 1 < len(matches) else min(len(exp_text), m.end() + cap_chars)
            blocks.append(exp_text[start:end].strip())
        return "\n\n".join(blocks)

    def _index_profiles(self):
        """Pre-calculates embeddings for all configured resume profiles,
        using each resume's 2 most recent employer/role blocks as the
        embedding source (see _extract_recent_experience)."""
        if not self.model:
            return
        for p in self.profiles:
            profile_id = p.get("id")
            file_path  = p.get("file_path")
            if profile_id is None or not file_path or not os.path.exists(file_path):
                continue

            name = p.get("name", "")
            print(f"Indexing profile: {name} (recent 2 roles)...")
            resume_text = extract_text_from_file(file_path)
            index_text  = self._extract_recent_experience(resume_text)

            if index_text.strip():
                self.profile_embeddings[profile_id] = self._embed_cached(index_text)
            else:
                print(f"Warning: No indexable content for profile '{name}'")

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _encode_query(self, job_query: str) -> np.ndarray:
        return self._embed_cached(job_query)

    def score_job(self, job_title: str, job_description: str) -> list:
        """
        Scores all indexed profiles against a job.
        Returns list of {profile_id, semantic_score}.
        """
        if not self.model or not self.profile_embeddings:
            return []
        job_query = f"Job Title: {job_title}\n\nDescription: {job_description}"
        query_vec = self._encode_query(job_query)
        results = []
        for profile_id, prof_vec in self.profile_embeddings.items():
            # Vectors from both fastembed and sentence-transformers are L2-normalised
            # so cosine similarity == dot product.
            score = float(np.dot(query_vec, prof_vec))
            results.append({
                "profile_id":     profile_id,
                "semantic_score": round(score * 100, 2),
            })
        results.sort(key=lambda x: x["semantic_score"], reverse=True)
        return results

    def delete_profile(self, profile_id):
        if profile_id in self.profile_embeddings:
            del self.profile_embeddings[profile_id]
            print(f"Removed semantic embedding for profile {profile_id}")
