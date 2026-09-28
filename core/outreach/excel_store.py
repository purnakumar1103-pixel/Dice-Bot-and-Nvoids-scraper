import pandas as pd
import os
import datetime
import threading

COLUMNS = [
    "Date", "Posted Date", "Job Title", "Company", "Location", "Keywords", "Recruiter Name", "Recruiter Email",
    "Phone", "Status", "Resume Used", "Email Sent?",
    "Draft/Sent", "CC", "BCC", "Source", "Dedup Hash", "Job URL", "Description"
]

class OutreachExcelStore:
    """
    Thread-safe Excel store with in-memory buffer.
    - Accumulates records in RAM; flushes to disk atomically.
    - avoids the O(N²) read-write-per-record anti-pattern.
    - Uses a lock so concurrent threads never corrupt the file.
    """

    def __init__(self, filepath="data/outreach_jobs.xlsx"):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.filepath = filepath
        self._lock = threading.Lock()

        # In-memory dedup set — populated from disk on first access
        self._known_hashes: set = set()
        self._hashes_loaded = False

        # Write buffer
        self._pending: list[dict] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_file(self):
        """Create the xlsx if it doesn't exist yet."""
        if not os.path.exists(self.filepath):
            pd.DataFrame(columns=COLUMNS).to_excel(self.filepath, index=False)

    def _load_hashes(self):
        """Load all existing Dedup Hash values into memory (once)."""
        if self._hashes_loaded:
            return
        self._ensure_file()
        try:
            df = pd.read_excel(self.filepath, usecols=["Dedup Hash"], engine="openpyxl")
            self._known_hashes = set(df["Dedup Hash"].dropna().astype(str).tolist())
        except Exception:
            self._known_hashes = set()
        self._hashes_loaded = True

    def _save_dataframe_safely(self, df, filepath):
        """
        Saves a pandas DataFrame to an Excel file, handling PermissionError if open.
        If locked, retries a few times, then saves to a backup file so no data is lost.
        """
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                df.to_excel(filepath, index=False, engine="openpyxl")
                return True
            except PermissionError:
                print(f"[ExcelStore] PermissionError: File {filepath} is locked (probably open in Excel). Retrying in 1s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(1)
            except Exception as e:
                print(f"[ExcelStore] Unexpected write error: {e}")
                raise e

        # If we reached here, retries failed. Save to a backup file to prevent data loss.
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_name = os.path.dirname(filepath) or "data"
        base_name = os.path.basename(filepath)
        name_parts = os.path.splitext(base_name)
        backup_filename = f"{name_parts[0]}_backup_{timestamp}{name_parts[1]}"
        backup_path = os.path.join(dir_name, backup_filename)
        
        print(f"[ExcelStore] WARNING: Retries exhausted. Saving backup copy to {backup_path}")
        df.to_excel(backup_path, index=False, engine="openpyxl")
        return False

    def _flush_to_disk(self):
        """Write the buffer to Excel atomically under the lock using fast openpyxl append."""
        if not self._pending:
            return
        self._ensure_file()
        try:
            import openpyxl
            wb = openpyxl.load_workbook(self.filepath)
            ws = wb.active

            # Append each record directly without re-parsing entire sheet DataFrame
            for record in self._pending:
                row = [str(record.get(col, "") or "") for col in COLUMNS]
                ws.append(row)

            wb.save(self.filepath)
            wb.close()
            self._pending.clear()
        except Exception as e:
            print(f"[ExcelStore] Fast append note: {e}, using DataFrame fallback...")
            try:
                try:
                    df_existing = pd.read_excel(self.filepath, engine="openpyxl")
                except Exception:
                    df_existing = pd.DataFrame(columns=COLUMNS)

                df_new = pd.DataFrame(self._pending)
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                for col in COLUMNS:
                    if col not in df_combined.columns:
                        df_combined[col] = ""
                df_combined = df_combined[COLUMNS]
                self._save_dataframe_safely(df_combined, self.filepath)
                self._pending.clear()
            except Exception as e2:
                print(f"[ExcelStore] Flush error: {e2}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_record(self, record: dict, auto_flush: bool = True):
        """
        Buffer a record and flush to disk efficiently.
        Keeps in-memory hash index updated instantly for zero-latency dedup checks.
        """
        with self._lock:
            self._load_hashes()
            record = dict(record)  # don't mutate caller's dict
            record["Date"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Normalise key names
            record.setdefault("Job URL", record.pop("URL", ""))
            record.setdefault("Source", "Nvoids")
            self._pending.append(record)
            
            # Update in-memory set so next is_in_excel() is instant (O(1))
            h = str(record.get("Dedup Hash", ""))
            if h:
                self._known_hashes.add(h)

            # Flush to disk every 10 items or when explicitly requested to eliminate O(N) disk I/O bottlenecks
            if auto_flush and len(self._pending) >= 10:
                self._flush_to_disk()

    def flush(self):
        """Force flush buffered records to disk instantly."""
        with self._lock:
            self._flush_to_disk()

    def is_in_excel(self, dedup_hash: str) -> bool:
        """O(1) duplicate check using in-memory set."""
        with self._lock:
            self._load_hashes()
            return str(dedup_hash) in self._known_hashes

    def update_record_status(self, dedup_hash: str, new_status: str, email_sent: str):
        """
        Update a single row's Status and Email Sent? fields.
        Reads once, patches, writes once.
        """
        with self._lock:
            if not os.path.exists(self.filepath):
                return
            try:
                df = pd.read_excel(self.filepath, engine="openpyxl")
                if "Dedup Hash" not in df.columns:
                    return
                mask = df["Dedup Hash"].astype(str) == str(dedup_hash)
                if mask.any():
                    df.loc[mask, "Status"] = new_status
                    df.loc[mask, "Email Sent?"] = email_sent
                    self._save_dataframe_safely(df, self.filepath)
            except Exception as e:
                print(f"[ExcelStore] update_record_status error: {e}")
