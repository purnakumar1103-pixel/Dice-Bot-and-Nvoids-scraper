"""
Continual Learning Engine
=========================

This module acts as the persistent memory storage for the bot. It utilizes a 
local SQLite database to track all successful job applications. This historical 
data is then fed back into the `ResumeMatcher` as a 'learning boost', allowing 
the platform to recognize job profiles/characteristics it has historically 
succeeded at and giving them priority.

Upgraded (v2): The learning boost is now similarity-weighted via Jaccard comparison 
of the current job title against past success job titles (max +15 pts, was flat +5).
"""

import re
import sqlite3
import os
import json
from datetime import datetime, timedelta
import functools

class LearningEngine:
    """
    Manages the local `learning_v3.db` SQLite memory.
    
    Database Schema:
    - `successful_apps`:
        - `id` (INTEGER, Primary Key)
        - `profile_id` (INTEGER): Foreign key tying back to settings profiles.
        - `job_title` (TEXT)
        - `job_description` (TEXT): The full text of the job description applied for.
        - `applied_at` (DATETIME)
        - `source` (TEXT): Whether it was applied to via 'auto' bot sweep or 'manual' user training.
    """
    def __init__(self, db_path="data/learning_v3.db"):
        self.db_path = db_path
        self._ensure_dir()
        self._conn_args = {"check_same_thread": False}
        self._conn = sqlite3.connect(self.db_path, **self._conn_args)
        self._init_db()

    def _ensure_dir(self):
        directory = os.path.dirname(self.db_path)
        if not os.path.exists(directory):
            os.makedirs(directory)

    def _init_db(self):
        conn = self._conn
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        cursor = conn.cursor()

        # Create table for successful applications
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS successful_apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                job_title TEXT,
                job_description TEXT,
                applied_at DATETIME,
                source TEXT DEFAULT 'manual'
            )
        ''')
        
        # Add source column to existing databases if needed
        try:
            cursor.execute('ALTER TABLE successful_apps ADD COLUMN source TEXT DEFAULT "manual"')
        except sqlite3.OperationalError:
            pass # Already exists
            
        # Table for "rejected" or manually skipped jobs (optional but good for 'negative' training)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS skipped_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_title TEXT,
                job_description TEXT,
                reason TEXT
            )
        ''')

        # ── Persistent email dedup across sessions ──────────────────────
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS contacted_emails (
                email       TEXT PRIMARY KEY,
                job_title   TEXT,
                contacted_at DATETIME,
                source      TEXT DEFAULT 'nvoids'
            )
        ''')

        # ── Dead/removed job listing URLs (never re-open these) ─────────
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dead_listings (
                url     TEXT PRIMARY KEY,
                seen_at DATETIME
            )
        ''')

        # ── Performance indexes ──────────────────────────────────────────
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_profile_id ON successful_apps(profile_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_applied_at ON successful_apps(applied_at)')

        conn.commit()

    def record_success(self, profile_id, job_title, job_description, source='auto'):
        """Records a successful application to the DB."""
        try:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT INTO successful_apps (profile_id, job_title, job_description, applied_at, source)
                VALUES (?, ?, ?, ?, ?)
            ''', (profile_id, job_title, job_description, datetime.now().isoformat(), source))
            self._conn.commit()
            # Invalidate cached title list so next boost call sees the new entry
            self._cached_past_titles.cache_clear()
        except Exception as e:
            print(f"FAILED to record success in AI Memory: {e}")

    def get_past_successes(self, profile_id=None):
        """Retrieves list of past successful jobs for a specific profile (or all)."""
        cursor = self._conn.cursor()
        if profile_id:
            cursor.execute('SELECT job_title, job_description FROM successful_apps WHERE profile_id = ?', (profile_id,))
        else:
            cursor.execute('SELECT job_title, job_description FROM successful_apps')
        return cursor.fetchall()

    @functools.lru_cache(maxsize=512)
    def _cached_past_titles(self, profile_id):
        """Cached fetch of past job titles for a profile (cache lives for the session)."""
        rows = self.get_past_successes(profile_id)
        return tuple(r[0] for r in rows if r[0])

    def get_similarity_boost(self, profile_id, current_job_title: str,
                             max_boost: float = 15.0) -> float:
        """
        Computes a Jaccard-similarity-weighted learning boost (0.0 – max_boost).

        Instead of a flat +5, this method:
          1. Fetches all past success job titles for this profile.
          2. Tokenizes each past title and the current job title.
          3. Computes Jaccard similarity between current title and each past title.
          4. Returns `avg_jaccard * max_boost` (capped), so profiles with many
             closely-related past successes earn a proportionally bigger boost.

        Args:
            profile_id:        The profile ID to look up in the DB.
            current_job_title: The job title being evaluated right now.
            max_boost:         Maximum bonus points (default: 15.0).

        Returns:
            float: Boost in range [0.0, max_boost].
        """
        if not current_job_title or not profile_id:
            return 0.0

        # Session-level boost cache: avoids re-computing the same title repeatedly
        _boost_cache = getattr(self, '_boost_cache', None)
        if _boost_cache is None:
            self._boost_cache = {}
            _boost_cache = self._boost_cache
        cache_key = (profile_id, current_job_title.lower().strip())
        if cache_key in _boost_cache:
            return _boost_cache[cache_key]

        past_titles = self._cached_past_titles(profile_id)
        if not past_titles:
            _boost_cache[cache_key] = 0.0
            return 0.0

        _stop = frozenset({'a', 'an', 'the', 'and', 'or', 'in', 'for', 'to', 'with',
                           'sr', 'jr', 'senior', 'junior', 'lead', 'principal', 'staff',
                           'remote', 'hybrid', 'contract', 'ii', 'iii', 'iv'})

        def _tokenize(title: str) -> frozenset:
            tokens = frozenset(re.split(r'[^a-z0-9]+', title.lower()))
            return tokens - _stop - frozenset({''})

        current_tokens = _tokenize(current_job_title)
        if not current_tokens:
            result = round(min(5.0, max_boost), 2)
            _boost_cache[cache_key] = result
            return result

        similarities = []
        for past_title in past_titles:
            past_tokens = _tokenize(past_title)
            if not past_tokens:
                continue
            intersection = current_tokens & past_tokens
            union        = current_tokens | past_tokens
            jaccard      = len(intersection) / len(union) if union else 0.0
            similarities.append(jaccard)

        if not similarities:
            _boost_cache[cache_key] = 0.0
            return 0.0

        avg_jaccard = sum(similarities) / len(similarities)
        boost       = round(min(avg_jaccard * max_boost, max_boost), 2)
        _boost_cache[cache_key] = boost
        return boost

    def get_stats(self):
        """
        Returns a dict of profile_id: { 'manual': count, 'auto': count }
        """
        cursor = self._conn.cursor()
        cursor.execute('''
            SELECT profile_id, source, COUNT(*)
            FROM successful_apps
            GROUP BY profile_id, source
        ''')
        rows = cursor.fetchall()

        stats = {}
        for pid, source, count in rows:
            if pid not in stats:
                stats[pid] = {'manual': 0, 'auto': 0}
            s_key = 'auto' if source == 'auto' else 'manual'
            stats[pid][s_key] += count
        return stats

    def close(self):
        """Explicitly close the persistent SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def reset_memory(self):
        """Deletes the entire history for a fresh start."""
        try:
            self._conn.close()
        except Exception:
            pass
        removed = False
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(self.db_path + suffix):
                        os.remove(self.db_path + suffix)
                removed = True
            except Exception:
                pass
        self._conn = sqlite3.connect(self.db_path, **self._conn_args)
        if not removed:
            try:
                cursor = self._conn.cursor()
                for tbl in ('successful_apps', 'skipped_jobs', 'contacted_emails', 'dead_listings'):
                    cursor.execute(f'DELETE FROM {tbl}')
                self._conn.commit()
            except Exception as e:
                print(f"Error clearing tables in reset_memory: {e}")
        self._init_db()
        if hasattr(self, '_boost_cache'):
            self._boost_cache.clear()
        self._cached_past_titles.cache_clear()
        print("AI Memory reset.")

    def clear_contacted_emails(self):
        """Wipes all rows from contacted_emails so recruiter outreach history is reset."""
        try:
            cursor = self._conn.cursor()
            cursor.execute('DELETE FROM contacted_emails')
            self._conn.commit()
            print("Contacted emails history cleared.")
        except Exception as e:
            print(f"FAILED to clear contacted emails: {e}")

    def delete_profile_history(self, profile_id):
        """Deletes all training data associated with a specific profile."""
        try:
            cursor = self._conn.cursor()
            cursor.execute('DELETE FROM successful_apps WHERE profile_id = ?', (profile_id,))
            self._conn.commit()
            self._cached_past_titles.cache_clear()
            if hasattr(self, '_boost_cache'):
                self._boost_cache = {k: v for k, v in self._boost_cache.items() if k[0] != profile_id}
            print(f"Deleted training data for profile {profile_id}")
        except Exception as e:
            print(f"FAILED to delete training data for profile {profile_id}: {e}")

    # ── Persistent cross-session email dedup ────────────────────────────

    def is_email_contacted(self, email: str, within_days: float | None = None) -> bool:
        """
        Returns True if this recruiter email was contacted recently.

        within_days=None  → True if EVER contacted (legacy permanent block).
        within_days=N     → True only if contacted in the last N days, so
                            recruiters become re-contactable after the window
                            (vendors repost the same roles every few days).
        """
        if not email:
            return False
        try:
            cursor = self._conn.cursor()
            if within_days is None:
                cursor.execute('SELECT 1 FROM contacted_emails WHERE email = ? LIMIT 1',
                               (email.lower().strip(),))
            else:
                cutoff = (datetime.now() - timedelta(days=within_days)).isoformat()
                cursor.execute(
                    'SELECT 1 FROM contacted_emails WHERE email = ? AND contacted_at > ? LIMIT 1',
                    (email.lower().strip(), cutoff)
                )
            return cursor.fetchone() is not None
        except Exception:
            return False

    def mark_email_contacted(self, email: str, job_title: str = "", source: str = "nvoids"):
        """Records (or refreshes) a recruiter contact so the recency window restarts."""
        if not email:
            return
        try:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT INTO contacted_emails (email, job_title, contacted_at, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    job_title    = excluded.job_title,
                    contacted_at = excluded.contacted_at,
                    source       = excluded.source
            ''', (email.lower().strip(), job_title, datetime.now().isoformat(), source))
            self._conn.commit()
        except Exception as e:
            print(f"FAILED to mark email as contacted: {e}")

    def is_dead_url(self, url: str) -> bool:
        """True if this listing URL was previously found dead/removed."""
        if not url:
            return False
        try:
            cursor = self._conn.cursor()
            cursor.execute('SELECT 1 FROM dead_listings WHERE url = ? LIMIT 1', (url.strip(),))
            return cursor.fetchone() is not None
        except Exception:
            return False

    def mark_dead_url(self, url: str):
        """Remember a removed listing so no future session wastes a page load on it."""
        if not url:
            return
        try:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO dead_listings (url, seen_at) VALUES (?, ?)
            ''', (url.strip(), datetime.now().isoformat()))
            self._conn.commit()
        except Exception:
            pass

    def get_email_stats(self) -> dict:
        """Returns total count of contacted emails, optionally broken down by source."""
        try:
            cursor = self._conn.cursor()
            cursor.execute('SELECT source, COUNT(*) FROM contacted_emails GROUP BY source')
            rows = cursor.fetchall()
            total = sum(c for _, c in rows)
            return {"total": total, "by_source": {s: c for s, c in rows}}
        except Exception:
            return {"total": 0, "by_source": {}}

if __name__ == "__main__":
    le = LearningEngine()
    le.record_success(1, "Data Engineer", "Spark, Python, AWS")
    print(le.get_past_successes(1))
