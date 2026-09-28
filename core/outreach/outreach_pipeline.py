"""
OutreachPipeline – Fixed version.

Bugs fixed vs previous version:
1. process_pending_emails: update_record_status was called per-row which
   caused O(N²) excel reads/writes. Now we batch all updates and do one
   final write.
2. recruiter_email 'nan' string (from pandas) is now normalised to "".
3. Email extraction regex: tightened to avoid matching image paths.
4. resume_used path lookup: case-insensitive fallback so mismatched casing
   doesn't leave resume_path empty.
5. Fallback when no profiles are configured — clear error, not silent crash.
"""

import re
import os
import queue
import threading
from core.outreach.dedup_engine import DedupEngine
from core.outreach.excel_store import OutreachExcelStore
from core.outreach.email_engine import EmailEngine
from core.outreach.resume_picker import ResumePicker

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg')

# Per-instance lock is created in __init__; this sentinel stops a stale worker thread (#22)
_STOP_SENTINEL = object()


def _clean_str(val) -> str:
    """Convert pandas scalar to clean string; turn NaN/None → empty."""
    s = str(val).strip()
    return "" if s.lower() == "nan" else s


class OutreachPipeline:
    def __init__(self, config: dict, profiles: list):
        self.config = config
        self.profiles = profiles
        cooldown_hours = int(config.get("cooldown_hours", 48))
        content_dup_days = float(config.get("content_dup_days", 7))
        self.dedup = DedupEngine(cooldown_hours=cooldown_hours,
                                 content_dup_days=content_dup_days)
        self.excel = OutreachExcelStore()
        self.email_engine = EmailEngine(self.config)

        self.target_resume_name = self.config.get("target_resume", "Auto-Match (AI)")
        self.use_ai_email = self.config.get("use_ai_email", False)
        groq_key = self.config.get("groq_api_key", "")
        if self.use_ai_email and groq_key:
            from core.outreach.ai_extractor import AIExtractor
            self.ai_extractor = AIExtractor(groq_key, log_callback=print)
        else:
            self.ai_extractor = None


        if self.target_resume_name == "Auto-Match (AI)":
            if profiles:
                print("[Pipeline] Loading AI resume-picker (this may take ~30s first time)…")
                self.picker = ResumePicker(self.profiles)
            else:
                print("[Pipeline] WARNING: No resume profiles configured. Cannot auto-match.")
                self.picker = None
        else:
            print(f"[Pipeline] Fixed resume: '{self.target_resume_name}' — bypassing AI.")
            self.picker = None
            
        self.stop_flag = False
        self.pause_flag = False
        self.skip_flag = False

        # Lock protecting counter mutations and Excel I/O across threads (#17 #18)
        self._lock = threading.Lock()

        # ── Daily email cap enforcement ──────────────────────────────────
        # Count how many emails we've already sent/drafted TODAY from Excel
        self.daily_cap = int(self.config.get("daily_cap", 50))
        self._today_sent = self._count_today_sent()
        print(f"[Pipeline] Daily cap: {self.daily_cap} | Already sent today: {self._today_sent}")

        # ── Per-cycle send budget ────────────────────────────────────────
        # Spreads the daily cap across continuous-loop cycles instead of
        # burning it all in the first morning sweep. 0 disables the limit.
        self.cycle_cap = int(self.config.get("cycle_cap", 15))
        self._cycle_sent = 0

        # Start background email worker; stop any previously running instance (#22)
        self.email_queue = queue.Queue()
        self.email_thread = threading.Thread(target=self._email_worker, daemon=True)
        self.email_thread.start()

    def reset_runtime_state(self):
        """Reset stop/pause/skip flags and ensure the email worker thread is running on reuse."""
        import queue
        import threading
        self.stop_flag = False
        self.pause_flag = False
        self.skip_flag = False
        if not hasattr(self, 'email_thread') or not self.email_thread.is_alive():
            self.email_queue = queue.Queue()
            self.email_thread = threading.Thread(target=self._email_worker, daemon=True)
            self.email_thread.start()

    def stop_worker(self):
        """Cleanly stop the background email thread before re-instantiating (#22)."""
        self.email_queue.put(_STOP_SENTINEL)

    def _email_worker(self):
        """Processes emails in the background so scraping can run in parallel."""
        import time as _time
        while True:
            while getattr(self, 'pause_flag', False) and not getattr(self, 'stop_flag', False):
                _time.sleep(1)

            job_data = self.email_queue.get()
            # Sentinel / None / stop_flag all cleanly exit the worker (#22)
            if job_data is _STOP_SENTINEL or job_data is None or getattr(self, 'stop_flag', False):
                self.email_queue.task_done()
                break

            raw_job, resume_path, body, subject, recruiter_email, company, title = job_data

            if getattr(self, 'skip_flag', False):
                with self._lock:
                    self.skip_flag = False
                raw_job["Status"]      = "Skipped (User)"
                raw_job["Email Sent?"] = "No"
                if hasattr(self.email_engine, 'log'):
                    self.email_engine.log(f"[Pipeline] ⏭ Skipped email drafting for: {title}")
                with self._lock:
                    self.excel.append_record(raw_job)
                self.email_queue.task_done()
                continue

            try:
                send_status = self.email_engine.send_email(raw_job, resume_path, body, subject)

                if send_status in ("Sent", "Draft", "Draft (Not Sent via SMTP)"):
                    raw_job["Status"]      = send_status
                    raw_job["Email Sent?"] = "Yes" if send_status == "Sent" else "Drafted"
                    self.dedup.log_sent_job(
                        recruiter_email, company, title,
                        raw_job.get("Location", ""),
                        raw_job.get("URL", "")
                    )
                    self.dedup.log_content_sent(
                        recruiter_email, title,
                        raw_job.get("Description", "")
                    )
                    # Increment counters ONLY after confirmed send - not before (#15)
                    with self._lock:
                        self._today_sent += 1
                        self._cycle_sent += 1
                    if hasattr(self.email_engine, 'log'):
                        self.email_engine.log(f"[Pipeline] ✅ {send_status}: {title}")
                else:
                    raw_job["Status"]      = f"Error: {send_status}"
                    raw_job["Email Sent?"] = "No"
                    if hasattr(self.email_engine, 'log'):
                        self.email_engine.log(f"[Pipeline] ❌ Failed: {title} → {send_status}")

            except Exception as e:
                raw_job["Status"] = f"Error: {e}"
                raw_job["Email Sent?"] = "No"
                if hasattr(self.email_engine, 'log'):
                    self.email_engine.log(f"[Pipeline] ❌ Exception: {e}")

            # Save to Excel under lock so process_pending_emails cannot race (#18)
            with self._lock:
                self.excel.append_record(raw_job)
            self.email_queue.task_done()
    def start_new_cycle(self):
        """Reset the per-cycle send budget (called by the scraper at each loop cycle)."""
        if self._cycle_sent:
            print(f"[Pipeline] New cycle - send budget reset (last cycle: {self._cycle_sent}).")
        self._cycle_sent = 0

    def _count_today_sent(self) -> int:
        """Count emails already sent/drafted today to enforce the daily cap."""
        from openpyxl import load_workbook
        from datetime import date
        if not os.path.exists(self.excel.filepath):
            return 0
        wb = None
        try:
            wb = load_workbook(self.excel.filepath, read_only=True, data_only=True)
            ws = wb.active
            headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
            if "Date" not in headers or "Email Sent?" not in headers:
                return 0
            date_col  = headers.index("Date")
            sent_col  = headers.index("Email Sent?")
            today_str = date.today().strftime("%Y-%m-%d")
            count = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                date_val = str(row[date_col]) if row[date_col] is not None else ""
                sent_val = str(row[sent_col]) if row[sent_col] is not None else ""
                if date_val.startswith(today_str) and sent_val in ("Yes", "Drafted"):
                    count += 1
            return count
        except Exception:
            return 0
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Single job processor (called during scraping)
    # ------------------------------------------------------------------

    def process_job(self, raw_job: dict, skip_email: bool = False) -> str:
        """
        Process one scraped job through the full pipeline.
        Returns a human-readable status string.
        """
        title   = raw_job.get("Job Title", "").strip()
        company = raw_job.get("Company", "").strip()
        desc    = raw_job.get("Description", "")

        # 1. Extract email from description if scraper didn't supply one
        recruiter_email = raw_job.get("Recruiter Email", "").strip()
        if not recruiter_email:
            emails = [
                e for e in _EMAIL_RE.findall(desc)
                if not any(e.lower().endswith(ext) for ext in _IMAGE_EXTS)
            ]
            if emails:
                recruiter_email = emails[0]
                raw_job["Recruiter Email"] = recruiter_email

        # 2. Dedup — compute hash before any writes
        location = raw_job.get("Location", "").strip()
        job_url  = raw_job.get("URL", "").strip()
        hash_val = self.dedup._generate_hash(recruiter_email, company, title, location, job_url)
        raw_job["Dedup Hash"] = hash_val

        if self.dedup.is_duplicate(recruiter_email, company, title, location, job_url) \
                or self.excel.is_in_excel(hash_val):
            return "Skipped (Duplicate)"

        # ── Layer 1: Content fingerprint (URL-independent) ────────────────
        if self.dedup.is_content_duplicate(recruiter_email, title, desc):
            return "Skipped (Content Duplicate)"

        # ── Layer 2: Cooldown window ──────────────────────────────────────
        if self.dedup.is_in_cooldown(recruiter_email, title):
            return "Skipped (Cooldown)"

        # 3. Resume selection
        best_profile = self._pick_profile(title, desc, raw_job=raw_job)
        raw_job["Resume Used"] = best_profile.get("name", "Default") if best_profile else "None"
        raw_job["Draft/Sent"]  = self.config.get("send_mode", "draft")

        # 4. CC / BCC
        user_cc    = self.config.get("cc_email", "")
        dynamic_cc = raw_job.get("Dynamic CC", "")
        raw_job["CC"]  = ",".join(filter(bool, [user_cc, dynamic_cc]))
        raw_job["BCC"] = self.config.get("bcc_email", "")

        # 5. Scrape-only path (queue for later email)
        if skip_email:
            raw_job["Status"]      = "Pending Sending"
            raw_job["Email Sent?"] = "No"
            # Truncate description for Excel storage (full text kept in Description col but capped)
            if raw_job.get("Description"):
                raw_job["Description"] = raw_job["Description"][:800]
            self.excel.append_record(raw_job)
            return "Pending Sending"

        # ── Daily cap check — read counter under lock (#17) ──────────────
        # Save capped jobs as PENDING, not Skipped: "Skipped" rows are excluded
        # from the pending batch AND their hash blocks re-scraping, so they'd
        # be permanently lost. Pending rows get drafted tomorrow instead.
        with self._lock:
            today_sent  = self._today_sent
            cycle_sent  = self._cycle_sent

        if today_sent >= self.daily_cap:
            raw_job["Status"]      = "Pending (Daily Cap)"
            raw_job["Email Sent?"] = "No"
            if raw_job.get("Description"):
                raw_job["Description"] = raw_job["Description"][:800]
            self.excel.append_record(raw_job)
            print(f"[Pipeline] Daily cap of {self.daily_cap} reached. Queued for tomorrow: {title}")
            return "Pending (Daily Cap)"

        # ── Per-cycle budget check ────────────────────────────────────────
        if self.cycle_cap > 0 and cycle_sent >= self.cycle_cap:
            raw_job["Status"]      = "Pending (Cycle Cap)"
            raw_job["Email Sent?"] = "No"
            if raw_job.get("Description"):
                raw_job["Description"] = raw_job["Description"][:800]
            self.excel.append_record(raw_job)
            print(f"[Pipeline] Cycle budget of {self.cycle_cap} reached. Deferred: {title}")
            return "Pending (Cycle Cap)"

        # 6. Send / draft immediately (via background queue)
        if recruiter_email:
            body, subject = self._build_email(raw_job, title, company)
            resume_path   = best_profile.get("file_path", "") if best_profile else ""
            
            # Truncate description for Excel storage
            if raw_job.get("Description"):
                raw_job["Description"] = raw_job["Description"][:800]

            # Counters are incremented by the worker AFTER confirmed send (#15)
            self.email_queue.put((raw_job, resume_path, body, subject, recruiter_email, company, title))
            return "Queued for Background Draft"
        else:
            raw_job["Status"]      = "Call Pending" if raw_job.get("Phone") else "No Email Found"
            raw_job["Email Sent?"] = "No"
            if raw_job.get("Description"):
                raw_job["Description"] = raw_job["Description"][:800]
            self.excel.append_record(raw_job)
            return raw_job["Status"]

    # ------------------------------------------------------------------
    # Batch email sender (called from "Draft Pending Emails" button)
    # ------------------------------------------------------------------

    def process_pending_emails(self, log_ui=print):
        """
        Read all 'Pending Sending' rows from Excel and draft/send each one.
        Uses a single Excel read + batch status map to avoid O(N²) writes.
        """
        import pandas as pd

        if not os.path.exists(self.excel.filepath):
            log_ui("[Pipeline] No Excel database found. Run 'Scrape & Queue' first.")
            return

        try:
            df = pd.read_excel(self.excel.filepath, engine="openpyxl")
        except Exception as e:
            log_ui(f"[Pipeline] Could not read Excel: {e}")
            return

        if "Email Sent?" not in df.columns:
            df["Email Sent?"] = "No"
        if "Status" not in df.columns:
            df["Status"] = "Pending"

        # Process any job not explicitly skipped, sent, or drafted.
        pending = df[~df["Email Sent?"].isin(["Yes", "Drafted"]) & (~df["Status"].astype(str).str.contains("Skipped", na=False))]
        
        if pending.empty:
            log_ui("[Pipeline] No pending or unapplied rows to process.")
            return

        # ── Sort newest-first: recently posted jobs get emailed first ──────
        # "Posted Date" column is populated by the scraper as "YYYY-MM-DD HH:MM".
        # Rows with a missing/unparseable date are treated as the oldest so they
        # fall to the end of the queue (safe fallback for legacy rows).
        if "Posted Date" in pending.columns:
            pending = pending.copy()
            pending["_sort_dt"] = pd.to_datetime(
                pending["Posted Date"], format="%Y-%m-%d %H:%M", errors="coerce"
            )
            pending = pending.sort_values("_sort_dt", ascending=False, na_position="last")
            pending = pending.drop(columns=["_sort_dt"])
            log_ui(f"[Pipeline] 📅 Pending rows sorted newest-first by Posted Date.")


        log_ui(f"[Pipeline] Found {len(pending)} pending email(s) to process…")

        # Collect updates in memory; apply in one batch write at the end
        updates: dict[str, tuple[str, str]] = {}  # hash → (status, email_sent)
        processed = 0

        for _, row in pending.iterrows():
            while getattr(self, 'pause_flag', False) and not getattr(self, 'stop_flag', False):
                import time
                time.sleep(1)
                
            if getattr(self, 'stop_flag', False):
                log_ui("[Pipeline] Stop requested. Halting email batch early.")
                break

            title           = _clean_str(row.get("Job Title", ""))
            company         = _clean_str(row.get("Company", ""))
            recruiter_email = _clean_str(row.get("Recruiter Email", ""))
            dedup_hash      = _clean_str(row.get("Dedup Hash", ""))

            if getattr(self, 'skip_flag', False):
                self.skip_flag = False
                log_ui(f"[Pipeline] \u23ed Skipped drafting for: {title}")
                updates[dedup_hash] = ("Skipped (User)", "No")
                continue

            if not recruiter_email:
                log_ui(f"[Pipeline] Skipping '{title}' — no recruiter email.")
                # Mark as Skipped (not "No Email Found") so this row is excluded
                # from every future pending run instead of looping forever.
                updates[dedup_hash] = ("Skipped (No Email)", "No")
                continue

            raw_job = {
                "Job Title":      title,
                "Company":        company,
                "Recruiter Name": _clean_str(row.get("Recruiter Name", "")),
                "Recruiter Email": recruiter_email,
                "CC":             _clean_str(row.get("CC", "")),
                "BCC":            _clean_str(row.get("BCC", "")),
                "Keywords":       _clean_str(row.get("Keywords", "")),
                "Location":       _clean_str(row.get("Location", "")),
                "Description":    _clean_str(row.get("Description", "")),
            }

            # Resolve resume path
            resume_name = _clean_str(row.get("Resume Used", ""))
            resume_path = self._resolve_resume_path(resume_name)

            body, subject = self._build_email(raw_job, title, company)

            scraped_dt = _clean_str(row.get("Date", ""))
            posted_dt  = _clean_str(row.get("Posted Date", ""))
            date_info  = f" | 📅 Scraped: {scraped_dt or 'N/A'} | 🕒 Posted: {posted_dt or 'N/A'}"

            phone_num = _clean_str(row.get("Phone", ""))
            if phone_num:
                log_ui("BLINK_UI_PHONE_DETECTED")
                log_ui(f"[Pipeline] Drafting [{processed + 1}/{len(pending)}] → {title} ({company}){date_info} (Phone: {phone_num})")
            else:
                log_ui(f"[Pipeline] Drafting [{processed + 1}/{len(pending)}] → {title} ({company}){date_info}")
            try:
                send_status = self.email_engine.send_email(raw_job, resume_path, body, subject)
            except Exception as e:
                send_status = f"Error: {e}"

            if send_status in ("Sent", "Draft", "Draft (Not Sent via SMTP)"):
                updates[dedup_hash] = (send_status, "Yes" if send_status == "Sent" else "Drafted")
                self.dedup.log_sent_job(
                    recruiter_email, company, title,
                    _clean_str(row.get("Location", "")),
                    _clean_str(row.get("URL", ""))
                )
                # Layer 1+2: log content fingerprint + cooldown
                self.dedup.log_content_sent(
                    recruiter_email, title,
                    _clean_str(row.get("Description", ""))
                )
                log_ui(f"[Pipeline] ✅ {send_status}: {title}")
                processed += 1

            else:
                updates[dedup_hash] = (f"Error: {send_status}", "No")
                log_ui(f"[Pipeline] ❌ Failed: {title} → {send_status}")

        # ── Batch-write all status updates in one Excel round-trip under lock (#18 #21) ──
        if updates:
            # Retry once on write failure so that statuses are not permanently lost (#21)
            for _attempt in range(2):
                try:
                    with self._lock:
                        df2 = pd.read_excel(self.excel.filepath, engine="openpyxl")
                        for h, (st, es) in updates.items():
                            if not h:
                                continue
                            mask = df2["Dedup Hash"].astype(str) == h
                            df2.loc[mask, "Status"]      = st
                            df2.loc[mask, "Email Sent?"] = es
                        df2.to_excel(self.excel.filepath, index=False, engine="openpyxl")
                    break
                except Exception as e:
                    if _attempt == 0:
                        log_ui(f"[Pipeline] Excel write failed, retrying... ({e})")
                        import time as _t; _t.sleep(1)
                    else:
                        log_ui(f"[Pipeline] Warning: could not write batch status updates to Excel after 2 attempts: {e}")

        log_ui(f"[Pipeline] Done. Processed {processed}/{len(pending)} email(s).")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_profile(self, title: str, desc: str, raw_job: dict = None):
        """Return the best matching resume profile dict, or None."""
        if self.target_resume_name != "Auto-Match (AI)":
            # Case-insensitive match first
            target_lower = self.target_resume_name.lower()
            best_p = None
            for p in self.profiles:
                if p.get("name", "").lower() == target_lower:
                    best_p = p
                    break
            if not best_p:
                best_p = self.profiles[0] if self.profiles else None

            # Calculate match details even for fixed resume so the user can see matched skills
            if best_p and raw_job is not None:
                try:
                    from core.matcher import ResumeMatcher
                    temp_matcher = ResumeMatcher([best_p])
                    text_to_score = f"{title}\n{desc}"
                    results = temp_matcher.score_profiles(text_to_score, job_title=title)
                    if results:
                        best_result = results[0]
                        raw_job["Matched Skills Info"] = {
                            "matched_uni": list(best_result.get("matched_uni", [])),
                            "matched_gen": list(best_result.get("matched_gen", [])),
                            "score": best_result.get("score", 0.0),
                            "confidence": best_result.get("confidence_pct", 0.0),
                            "name_affinity": best_result.get("name_affinity", 0.0)
                        }
                except Exception:
                    pass

            return best_p

        if self.picker:
            return self.picker.pick_resume(title, desc, raw_job=raw_job)

        return self.profiles[0] if self.profiles else None

    def _resolve_resume_path(self, resume_name: str) -> str:
        """Find a profile's file path by name (case-insensitive)."""
        if not resume_name:
            return ""
        name_lower = resume_name.lower()
        for p in self.profiles:
            if p.get("name", "").lower() == name_lower:
                return p.get("file_path", "")
        return ""

    def _build_email(self, raw_job: dict, title: str, company: str) -> tuple[str, str]:
        """Return (body, subject) from the configured template."""
        template = self.config.get(
            "template",
            "Hi {recruiter_name},\n\nPlease find my resume attached.\n\nBest regards"
        )
        # Strip any accidental 'Subject: ' prefix that users sometimes put in the template box
        if template.lower().startswith("subject:"):
            first_blank = template.find('\n\n')
            if first_blank != -1:
                template = template[first_blank + 2:].strip()
            else:
                # Fallback: no blank line separator — strip just the Subject: line
                template = template.split('\n', 1)[-1].strip()

        recruiter_name = raw_job.get("Recruiter Name", "") or "Recruiter"
        clean_title    = self._clean_job_title(title)
        keywords       = raw_job.get("Keywords", "") or ""
        location       = raw_job.get("Location", "") or ""

        def _fill(text: str) -> str:
            return (
                text
                .replace("{job_title}", clean_title)
                .replace("{company_name}", company)
                .replace("{recruiter_name}", recruiter_name)
                .replace("{keywords}", keywords)
                .replace("{location}", location)
            )

        body = _fill(template)

        # Clean up body if optional fields were empty
        body = re.sub(r'\s*\(\s*\)', '', body)
        body = re.sub(r'(?i)(?:,\s*)?especially my experience with\s*\.', '.', body)
        body = re.sub(r'(?i)\s+with\s*\.', '.', body)

        # ── AI Dynamic Email Generation (Groq) ─────────────────────────────
        if getattr(self, 'ai_extractor', None) and self.config.get("use_ai_email", False):
            my_skills = self.config.get("my_core_skills", "")
            desc = raw_job.get("Description", "")
            if desc:
                ai_body = self.ai_extractor.generate_email_body(clean_title, company, desc, my_skills)
                if ai_body:
                    sig_match = re.search(r'(?i)(?:best|regards|sincerely|thanks|cheers)[,\s]+(.+)$', template, re.DOTALL)
                    signature = f"\n\nBest,\n{sig_match.group(1).strip()}" if sig_match else "\n\nBest regards,\n[Your Name]"
                    # Strip any closing the AI added anyway
                    ai_body_clean = re.sub(r'(?i)\s*(?:best|regards|sincerely|thanks|cheers).*$', '', ai_body, flags=re.DOTALL)
                    # Strip any greeting the AI added despite instructions
                    ai_body_clean = re.sub(r'(?i)^(hi|hello|dear)\s+[^,\n]+,?\s*\n+', '', ai_body_clean.strip(), flags=re.MULTILINE)
                    body = f"Hi {recruiter_name},\n\n{ai_body_clean.strip()}{signature}"

        # Subject: use configurable subject_template, fallback to a sensible default.
        # NOTE: Default deliberately matches the UI default (outreach_ui.py line 160)
        # so first-time users don't get the spammy "({location}) – {keywords}" suffix.
        subject_template = self.config.get(
            "subject_template",
            "Application for {job_title}"
        )
        subject = _fill(subject_template).strip()
        # Clean up empty parens "()" and trailing "–" when optional fields are absent
        subject = re.sub(r'\s*\(\s*\)', '', subject)         # remove "()"
        subject = re.sub(r'\s*[\-\u2013]\s*$', '', subject)  # remove trailing "–"
        # Safety: if the subject still contains the cleaned title, it's fine.
        # If {job_title} was already replaced by clean_title (which we set above),
        # no further work needed. But double-clean the subject title portion
        # in case an old saved setting used a literal raw title.
        subject = subject.strip()

        return body, subject

    def _clean_job_title(self, title: str) -> str:
        """
        Extract a clean, human-readable job title from noisy Nvoids strings.

        Handles patterns like:
          "Urgent Req: Data Engineer – Austin, TX (Hybrid) || W2 Only || Direct Client"
          "Opportunity: Hiring for Senior ML Engineer | Remote"
          "15+ Years Experience Data Engineer"
          "Senior Data Engineer - 15+ Yrs Exp"
          "15+ positions - AWS Data Engineer"
        """
        if not title:
            return ""

        # ── Step 0: Strip experience & numerical requirement noise ────────
        # e.g., "15+ Years Exp", "15+ Yrs", "10+ Years Experience", "15+", "15+ positions"
        title = re.sub(r'\b\d+\+\s*(?:years?|yrs?|yr)\s*(?:of)?\s*(?:exp(?:erience)?)?\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\b\d+\+\s*(?:positions?|openings?|roles?|vacancies)\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\b\d+\+\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\b\d+\s*[\+\-]\s*(?:years?|yrs?|yr)\s*(?:of)?\s*(?:exp(?:erience)?)?\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\b\d+\s+(?:years?|yrs?|yr)\s+(?:of\s+)?exp(?:erience)?\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\b(?:years?|yrs?|yr)\s+(?:of\s+)?exp(?:erience)?\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\bexp(?:erience)?\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'^\s*[\-\u2013\u2014:,.]+\s*', '', title)
        title = re.sub(r'\s+', ' ', title).strip()

        # ── Step 1: top-level pipe-segment split ─────────────────────────
        # Split on double-pipes first, then single pipes
        parts = re.split(r'\|\||\|', title)

        # Junk segment patterns — if the WHOLE segment matches, skip it
        _JUNK_STARTS = (
            "jd", "urgent", "urgent req", "hot req", "hot req:", "hot",
            "direct client", "c2c", "w2", "need local", "local",
            "remote", "hybrid", "onsite", "on-site", "position", "role",
            "req", "requirement", "requirements",
            "opportunity", "hiring for", "hiring",
            "immediate", "immediate req", "immediate requirement",
            "open position", "openings", "opening",
            "asap", "visa", "gc", "citizen",
            "fulltime", "full time", "part time", "contract",
            "required", "need", "needed", "wanted", "urgent requirement",
            "urgent need", "urgent hiring", "urgently hiring", "actively hiring",
            "actively recruiting", "immediate hiring", "immediate need",
            "looking for", "required urgently", "needed urgently",
            "urgently need", "urgently needed", "needed", "required",
            "as discussed", "please find", "job description for",
            "we are looking for", "we are hiring",
        )

        clean_part = parts[0].strip()
        for p in parts:
            p_clean = p.strip()
            if not p_clean:
                continue
            p_lower = p_clean.lower()
            is_junk = any(
                p_lower == j
                or p_lower.startswith(j + " ")
                or p_lower.startswith(j + ":")
                or p_lower.startswith(j + "-")
                or p_lower.startswith(j + "s ")
                for j in _JUNK_STARTS
            )
            if is_junk:
                continue
            clean_part = p_clean
            break

        # ── Step 2: strip known junk prefixes (loop — compound prefixes like "Opportunity: Hiring for") ──
        _JUNK_PREFIXES = (
            "job opening-", "job opening:", "job opening ",
            "req-", "req:", "urgent-", "urgent:", "jd-", "jd:",
            "role-", "role:", "urgent req:", "hot req:", "hot req-",
            "urgent requirement:", "urgent requirement-", "urgent requirement ",
            "requirement:", "requirement-", "requirement ",
            "requirements:", "requirements-", "requirements ",
            "hot:", "hot-", "hot ",
            "position:", "position-",
            "opening:", "opening-",
            "openings:", "openings-",
            "opportunity:", "opportunity-", "opportunity ",
            "hiring for:", "hiring for ", "hiring:", "hiring ",
            "immediate:", "immediate req:", "immediate opening:",
            "immediate requirement:",
            "need:", "need-", "urgent need of:", "urgent need of ",
            "urgent need for:", "urgent need for ", "urgent need:",
            "urgent need-", "urgent need ", "urgently hiring for:",
            "urgently hiring for ", "urgently hiring:", "urgently hiring ",
            "urgent hiring for:", "urgent hiring for ", "urgent hiring:", "urgent hiring ",
            "actively hiring for:", "actively hiring for ", "actively hiring:", "actively hiring ",
            "immediate hiring for:", "immediate hiring for ", "immediate hiring:", "immediate hiring ",
            "immediate need for:", "immediate need for ", "immediate need of:", "immediate need of ",
            "immediate need:", "immediate need-", "immediate need ", "looking for:", "looking for ",
            "we are hiring for:", "we are hiring for ", "we are hiring:", "we are hiring ",
            "urgent req:", "urgent req ", "urgent requirement for:", "urgent requirement for ",
            "need urgently:", "need urgently ", "needed urgently:", "needed urgently ",
            "immediate opening for:", "immediate opening for ", "immediate opening:", "immediate opening ",
            "job description for:", "job description:", "job description ", "urgent:", "urgent-", "urgent ",
            "urgently:", "urgently-", "urgently ", "needed:", "needed ", "required:", "required ",
            # ── Nvoids-specific noisy prefixes ──────────────────────────────
            "requirement ", "requirement:", "requirement-",
            "as discussed:", "as discussed ", "please find:", "please find ",
            "we are looking for:", "we are looking for ",
            "job title:", "job title ",
            "100% remote -", "100% remote",
        )
        # Loop to handle chained prefixes: "Opportunity: Hiring for Senior ML Eng"
        for _ in range(5):  # max 5 passes to avoid infinite loops
            stripped = False
            for prefix in _JUNK_PREFIXES:
                if clean_part.lower().startswith(prefix):
                    clean_part = clean_part[len(prefix):].strip()
                    stripped = True
                    break  # restart loop with updated string
            if not stripped:
                break

        # If all pipe segments were junk (e.g. "W2 || Immediate: Python Developer")
        # try to rescue the title from the LAST pipe segment after prefix-stripping
        if not clean_part or clean_part.lower() in [j for j in _JUNK_STARTS]:
            for p in reversed(parts):
                p_clean = p.strip()
                if not p_clean:
                    continue
                candidate = p_clean
                for _ in range(5):
                    stripped = False
                    for prefix in _JUNK_PREFIXES:
                        if candidate.lower().startswith(prefix):
                            candidate = candidate[len(prefix):].strip()
                            stripped = True
                            break
                    if not stripped:
                        break
                if len(candidate) >= 4:
                    clean_part = candidate
                    break

        # ── Step 3: split on location/work-mode separators ───────────────
        # Handles: " - ", " – ", "–", "—", " — ", ": ", "(", "["
        # Key fix: include no-space variants of en-dash (–) and em-dash (—)
        sub_parts = re.split(
            r'\s+[\-\u2013\u2014]\s+|[\u2013\u2014]|\s+[\-]\s+|:\s+|\(|\[',
            clean_part
        )
        final_title = sub_parts[0].strip()

        # ── Step 4: strip trailing City, ST or work-mode noise ───────────
        # e.g. "Data Engineer Austin TX" or "ML Engineer Remote"
        final_title = re.sub(
            r',?\s+(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*[A-Z]{2}|Remote|Hybrid|Onsite|On-site)\s*$',
            '', final_title, flags=re.IGNORECASE
        ).strip()

        # ── Step 4b: strip trailing recruiting noise words ────────────────
        # e.g. "Data Engineer Required", "Python Dev - Needed", "ML Eng ASAP"
        _TRAILING_NOISE = re.compile(
            r'[\s,\-\u2013\u2014\(\[\{\)]+'
            r'(?:required|needed|need|wanted|urgent(?:ly)?|immediate(?:ly)?|asap|'
            r'now|today|opening|openings|opportunity|hiring|available|vacancy|vacancies|'
            r'urgent\s+need|immediate\s+need|immediate\s+hiring|actively\s+hiring)\s*$',
            re.IGNORECASE
        )
        final_title = _TRAILING_NOISE.sub('', final_title).strip()

        # Final pass: strip any leftover leading/trailing experience words or punctuation
        final_title = re.sub(r'^\s*[\-\u2013\u2014:,.]+|\s*[\-\u2013\u2014:,.]+$', '', final_title).strip()

        # ── Step 5: fallback + truncation ────────────────────────────────
        if len(final_title) < 4:
            final_title = clean_part.strip()

        if len(final_title) > 55:
            final_title = final_title[:52] + "..."

        return final_title
