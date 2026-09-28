"""
Outreach Deduplication Engine  –  v2  (3-layer smart dedup)
============================================================
Layer 0  –  URL-exact hash (UNCHANGED):
    hash(email | company | title | location | url)
    Catches: exact same Nvoids URL re-scraped in a later run.

Layer 1  –  Content fingerprint (NEW):
    hash(email | norm_title | desc_snippet[:400])
    URL-INDEPENDENT — catches same JD reposted with a fresh URL
    the next day, or multiple times in the same day.

Layer 2  –  Cooldown window (NEW):
    Blocks the same (recruiter_email, normalized_title) pair for
    a configurable number of hours (default 48).  Even if the JD
    text changed slightly, we won't email the same vendor for the
    same role again until the cooldown expires.

Database: data/outreach_dedup.db  (SQLite, WAL mode)
Tables
  sent_jobs            – Layer 0  (pre-existing, unchanged)
  content_fingerprints – Layer 1  (new)
  recruiter_cooldowns  – Layer 2  (new)
"""

import re
import sqlite3
import hashlib
import os
from datetime import datetime, timezone, timedelta


class DedupEngine:
    def __init__(self, db_path: str = "data/outreach_dedup.db", cooldown_hours: int = 48,
                 content_dup_days: float = 7):
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.db_path = db_path
        self.cooldown_hours = cooldown_hours
        self.content_dup_days = max(0.5, float(content_dup_days or 7))
        # Persistent connection — avoids per-call open/close overhead.
        # check_same_thread=False is safe because all write paths are short
        # serialised operations; WAL mode handles concurrent readers.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    # ── Schema setup ──────────────────────────────────────────────────────────

    def _init_db(self):
        conn = self._conn
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        c = conn.cursor()

        # ── Layer 0: URL-exact dedup (pre-existing table, kept as-is) ──
        c.execute('''
            CREATE TABLE IF NOT EXISTS sent_jobs (
                hash_key       TEXT PRIMARY KEY,
                job_title      TEXT,
                company_name   TEXT,
                recruiter_email TEXT,
                location       TEXT,
                job_url        TEXT,
                timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Migrate older DBs missing new columns
        for col in ("location TEXT", "job_url TEXT"):
            try:
                c.execute(f"ALTER TABLE sent_jobs ADD COLUMN {col}")
            except Exception:
                pass

        # ── Layer 1: Content fingerprint (NEW) ──────────────────────────
        c.execute('''
            CREATE TABLE IF NOT EXISTS content_fingerprints (
                content_hash    TEXT PRIMARY KEY,
                recruiter_email TEXT,
                norm_title      TEXT,
                snippet         TEXT,
                sent_at         DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ── Layer 2: Recruiter cooldown (NEW) ───────────────────────────
        c.execute('''
            CREATE TABLE IF NOT EXISTS recruiter_cooldowns (
                recruiter_email TEXT NOT NULL,
                norm_title      TEXT NOT NULL,
                sent_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (recruiter_email, norm_title)
            )
        ''')

        conn.commit()

    def close(self):
        """Explicitly close the persistent connection (optional — GC handles it too)."""
        try:
            self._conn.close()
        except Exception:
            pass

    def wipe_database(self) -> bool:
        """Wipe all tables (sent_jobs, content_fingerprints, recruiter_cooldowns) and VACUUM."""
        try:
            self._conn.execute("DELETE FROM sent_jobs")
            self._conn.execute("DELETE FROM content_fingerprints")
            self._conn.execute("DELETE FROM recruiter_cooldowns")
            self._conn.commit()
            try:
                self._conn.execute("VACUUM")
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"[DedupEngine] Error wiping database: {e}")
            return False

    # ── Normalization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_title(title: str) -> str:
        """
        Produce a compact, noise-free title for dedup comparison.
        Strategy: Nvoids always appends location/work-mode AFTER a separator
        ('|', '-', '–', or comma).  So we split on the first such separator and
        discard everything that follows.  Then strip known noise words.

        Examples
        --------
        "Data Engineer – Austin, TX (Hybrid) || W2 Only"  →  "data engineer"
        "AWS Data Engineer | Remote | Direct Client"       →  "aws data engineer"
        "Senior Data Engineer Hybrid Remote"               →  "senior data engineer"
        "Data Engineer - Austin TX"                        →  "data engineer"
        """
        t = str(title or "").lower()
        # Strip experience requirement noise
        t = re.sub(r'\b\d+\+\s*(?:years?|yrs?|yr)?\s*(?:of)?\s*(?:exp(?:erience)?)?\b', '', t)
        t = re.sub(r'\b\d+\s*[\+\-]\s*(?:years?|yrs?|yr)\s*(?:of)?\s*(?:exp(?:erience)?)?\b', '', t)
        t = re.sub(r'\b\d+\s+(?:years?|yrs?|yr)\s+(?:of\s+)?exp(?:erience)?\b', '', t)
        t = re.sub(r'\b\d+\+\b', '', t)
        # Step 1: split on FIRST occurrence of any Nvoids delimiter or comma-space
        # and keep only the part before it (the actual role name)
        t = re.split(r'\s*[\|–]\s*|\s+-\s+|\s*,\s+', t)[0]
        # Step 2: strip work-mode / contract / visa noise
        noise = (
            r'\b(remote|hybrid|onsite|on[\s-]site|c2c|w2|local|urgent|'
            r'direct\s+client|contract|fulltime|full[\s-]time|part[\s-]time|'
            r'need\s+local|visa|greencard|citizen|only)\b'
        )
        t = re.sub(noise, ' ', t, flags=re.IGNORECASE)
        # Step 3: strip punctuation and collapse whitespace
        t = re.sub(r'[^\w\s]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    @staticmethod
    def _normalize_snippet(description: str, length: int = 400) -> str:
        """Return a normalized, fixed-length snippet of the job description."""
        snippet = str(description or "")[:length * 3]
        snippet = re.sub(r'\s+', ' ', snippet).strip().lower()
        return snippet[:length]

    # ── Layer 0: URL-exact hash (pre-existing, unchanged) ────────────────────

    def _generate_hash(self, recruiter_email: str, company_name: str,
                       job_title: str, location: str = "", job_url: str = "") -> str:
        """SHA-256 of email|company|title|location|url  (unchanged from v1)."""
        email   = str(recruiter_email or "").strip().lower()
        company = str(company_name    or "").strip().lower()
        title   = str(job_title       or "").strip().lower()
        loc     = str(location        or "").strip().lower()
        url     = str(job_url         or "").strip().lower()
        raw     = f"{email}|{company}|{title}|{loc}|{url}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_duplicate(self, recruiter_email: str, company_name: str,
                     job_title: str, location: str = "", job_url: str = "") -> bool:
        """Layer 0 check — exact URL-hash match (unchanged behaviour)."""
        hash_key = self._generate_hash(recruiter_email, company_name, job_title, location, job_url)
        row = self._conn.execute(
            "SELECT 1 FROM sent_jobs WHERE hash_key = ?", (hash_key,)
        ).fetchone()
        return row is not None

    def log_sent_job(self, recruiter_email: str, company_name: str,
                     job_title: str, location: str = "", job_url: str = ""):
        """Layer 0 — log URL-hash into sent_jobs (unchanged behaviour)."""
        hash_key = self._generate_hash(recruiter_email, company_name, job_title, location, job_url)
        try:
            self._conn.execute('''
                INSERT INTO sent_jobs
                    (hash_key, job_title, company_name, recruiter_email, location, job_url)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (hash_key, job_title, company_name, recruiter_email, location, job_url))
            self._conn.commit()
        except sqlite3.IntegrityError:
            pass  # already exists — safe to ignore

    # ── Layer 1: Content fingerprint ─────────────────────────────────────────

    def _content_hash(self, recruiter_email: str, job_title: str,
                      description: str) -> str:
        """
        SHA-256 of email | normalized_title | description_snippet.
        URL-independent: same JD reposted with a new URL yields the same hash.
        """
        email   = str(recruiter_email or "").strip().lower()
        n_title = self._normalize_title(job_title)
        snippet = self._normalize_snippet(description)
        raw     = f"{email}|{n_title}|{snippet}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_content_duplicate(self, recruiter_email: str, job_title: str,
                             description: str) -> bool:
        """
        Layer 1 check — returns True if we emailed this (email, JD-content) pair
        within the last `content_dup_days` days, regardless of URL changes.
        Older fingerprints expire so reposted roles become contactable again.
        """
        try:
            content_hash = self._content_hash(recruiter_email, job_title, description)
            cutoff = (datetime.now(timezone.utc) -
                      timedelta(days=self.content_dup_days)).strftime("%Y-%m-%d %H:%M:%S")
            row = self._conn.execute(
                "SELECT 1 FROM content_fingerprints WHERE content_hash = ? AND sent_at > ?",
                (content_hash, cutoff)
            ).fetchone()
            return row is not None
        except Exception:
            return False  # Fail open — don't block on DB error

    def log_content_sent(self, recruiter_email: str, job_title: str, description: str):
        """
        Layer 1+2 — log content fingerprint AND upsert the cooldown record.
        Call this AFTER a successful send/draft.
        """
        try:
            email    = str(recruiter_email or "").strip().lower()
            n_title  = self._normalize_title(job_title)
            snippet  = self._normalize_snippet(description)
            c_hash   = self._content_hash(recruiter_email, job_title, description)
            now_utc  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            # Layer 1 — content fingerprint (refresh sent_at on re-send)
            self._conn.execute('''
                INSERT INTO content_fingerprints
                    (content_hash, recruiter_email, norm_title, snippet, sent_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(content_hash)
                DO UPDATE SET sent_at = excluded.sent_at
            ''', (c_hash, email, n_title, snippet, now_utc))

            # Layer 2 — cooldown upsert (update timestamp if exists)
            self._conn.execute('''
                INSERT INTO recruiter_cooldowns (recruiter_email, norm_title, sent_at)
                VALUES (?, ?, ?)
                ON CONFLICT(recruiter_email, norm_title)
                DO UPDATE SET sent_at = excluded.sent_at
            ''', (email, n_title, now_utc))

            self._conn.commit()
        except Exception:
            pass  # Non-critical — don't crash the pipeline

    # ── Layer 2: Cooldown window ──────────────────────────────────────────────

    def is_in_cooldown(self, recruiter_email: str, job_title: str,
                       window_hours: int | None = None) -> bool:
        """
        Layer 2 check — returns True if we emailed this (email, normalized_title)
        pair within the cooldown window.
        """
        try:
            hours   = window_hours if window_hours is not None else self.cooldown_hours
            email   = str(recruiter_email or "").strip().lower()
            n_title = self._normalize_title(job_title)
            cutoff  = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

            row = self._conn.execute('''
                SELECT sent_at FROM recruiter_cooldowns
                WHERE recruiter_email = ? AND norm_title = ? AND sent_at > ?
            ''', (email, n_title, cutoff)).fetchone()
            return row is not None
        except Exception:
            return False  # Fail open

    def get_cooldown_status(self, recruiter_email: str | None = None) -> list[dict]:
        """
        Return a list of active cooldown records for UI display.
        If recruiter_email is given, filter to that email only.
        Each dict has: recruiter_email, norm_title, sent_at, hours_remaining.
        """
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(hours=self.cooldown_hours)).strftime("%Y-%m-%d %H:%M:%S")

            if recruiter_email:
                email = str(recruiter_email).strip().lower()
                rows = self._conn.execute('''
                    SELECT recruiter_email, norm_title, sent_at
                    FROM recruiter_cooldowns
                    WHERE recruiter_email = ? AND sent_at > ?
                    ORDER BY sent_at DESC
                ''', (email, cutoff)).fetchall()
            else:
                rows = self._conn.execute('''
                    SELECT recruiter_email, norm_title, sent_at
                    FROM recruiter_cooldowns
                    WHERE sent_at > ?
                    ORDER BY sent_at DESC
                    LIMIT 50
                ''', (cutoff,)).fetchall()

            result = []
            for email_val, norm_title, sent_at_str in rows:
                try:
                    sent_at = datetime.strptime(sent_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    hours_since = (now - sent_at).total_seconds() / 3600
                    hours_remaining = max(0.0, self.cooldown_hours - hours_since)
                    result.append({
                        "recruiter_email":  email_val,
                        "norm_title":       norm_title,
                        "sent_at":          sent_at_str,
                        "hours_since":      round(hours_since, 1),
                        "hours_remaining":  round(hours_remaining, 1),
                    })
                except Exception:
                    continue
            return result
        except Exception:
            return []

    def clear_cooldown(self, recruiter_email: str, norm_title: str | None = None):
        """
        Remove cooldown and content-fingerprint records for a recruiter email.
        If norm_title is provided, only clear that specific role.
        """
        try:
            email = str(recruiter_email or "").strip().lower()
            if norm_title:
                self._conn.execute(
                    "DELETE FROM recruiter_cooldowns WHERE recruiter_email = ? AND norm_title = ?",
                    (email, norm_title)
                )
                self._conn.execute(
                    "DELETE FROM content_fingerprints WHERE recruiter_email = ? AND norm_title = ?",
                    (email, norm_title)
                )
            else:
                self._conn.execute(
                    "DELETE FROM recruiter_cooldowns WHERE recruiter_email = ?", (email,)
                )
                self._conn.execute(
                    "DELETE FROM content_fingerprints WHERE recruiter_email = ?", (email,)
                )
            self._conn.commit()
        except Exception:
            pass

    def clear_all_cooldowns(self) -> int:
        """
        Wipe every record from recruiter_cooldowns and content_fingerprints
        (Layers 1 & 2) while leaving the Layer-0 sent_jobs history intact.
        Returns the number of cooldown rows deleted.
        """
        try:
            deleted = self._conn.execute("DELETE FROM recruiter_cooldowns").rowcount
            self._conn.execute("DELETE FROM content_fingerprints")
            self._conn.commit()
            return deleted
        except Exception:
            return 0
