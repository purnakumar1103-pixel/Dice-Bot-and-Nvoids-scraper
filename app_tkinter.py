# dice_auto_apply/app_tkinter.py
"""
Dice Auto Apply Bot - Main GUI Application
==========================================

This module contains the primary Tkinter graphical user interface and the core orchestration 
logic for the Dice Auto Apply Bot.

Key Integrated Features & Architectural Overview:
-------------------------------------------------
1. Automated Job Search & Application:
   - Polls Dice for job URLs and manages automated Selenium interactions to apply to jobs.
   - Includes early-exit mechanisms to prevent 60-second timeouts on empty pages.

2. Advanced Resume Matching Engine (Semantic & Hierarchical):
   - Uses `SemanticResumeMatcher` (TF-IDF inspired) to find the best resume for the job description.
   - Boost modes (exact, high, low, off) let users prioritize specific resumes for distinct job roles.
   
3. Native OS Resume Uploads (Safe PyAutoGUI Integration):
   - Directly bypasses rigid Dice file-upload blocking screens by invoking native OS file dialogs via PyAutoGUI.
   - Fallback mechanisms handle edge case UI changes reliably.

4. Comprehensive Logging & Skip Reasoning:
   - Maintains robust records in `applied_jobs.xlsx`, `not_applied_jobs.xlsx`, and `excluded_jobs.xlsx`.
   - "Skip Reason" is captured intelligently on application failure (e.g., Timeout, Missing fields, Auth Issue).
   
5. Robust Exception Handling & Thread-Safe UI:
   - GUI runs on the main thread, while the Selenium application logic (`job_thread`) runs concurrently.
   - Frequent checks ensure pause and stop responsiveness (Cascading Resume Deletion fixes).
   - Real-time AI learning tracking mechanisms distinguish manual UI events from automatic model runs safely.

6. Profile ID Type-Safety & Path Normalization:
   - Resume profiles have unique IDs that avoid bugs where similarly named profiles clash. 
   - OS file paths are normalized seamlessly between Windows/Mac paths.
"""


import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from utils.ui_components import ToggleSwitch, LabeledToggleSwitch
import threading
import pandas as pd
from datetime import datetime
import time
import logging
import pyautogui
import subprocess

# Try both absolute and relative imports for compatibility
try:
    from core.browser_detector import get_browser_path
    from core.dice_login import login_to_dice, update_dice_credentials, validate_dice_credentials
    from core.main_script import get_web_driver, fetch_jobs_with_requests, apply_to_job_url
    from core.semantic_matcher import SemanticResumeMatcher
    from core.groq_resume_scorer import GroqResumeScorer
except ImportError:
    # Attempt parent-directory relative imports if normal ones fail
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from core.browser_detector import get_browser_path
    from core.dice_login import login_to_dice, update_dice_credentials, validate_dice_credentials
    from core.main_script import get_web_driver, fetch_jobs_with_requests, apply_to_job_url
    from core.semantic_matcher import SemanticResumeMatcher
    from core.groq_resume_scorer import GroqResumeScorer



def fix_imports():
    """Fix imports for both development and packaged environments"""
    import os
    import sys
    
    # Add the parent directory to the path if not already there
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

# Call this at the beginning of your script
fix_imports()



class DiceAutoBotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Dice Auto Apply Bot")

        # ── Adaptive sizing: fit the window to whatever screen it opens on ──
        self.root.update_idletasks()  # ensure winfo_* values are ready
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        # Target 88% of screen, capped at reasonable maximums
        win_w = min(int(sw * 0.88), 1300)
        win_h = min(int(sh * 0.88), 900)
        # Never let window be smaller than a usable minimum
        win_w = max(win_w, 860)
        win_h = max(win_h, 600)

        # Centre the window on the primary screen
        x_pos = max(0, (sw - win_w) // 2)
        y_pos = max(0, (sh - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        self.root.minsize(860, 600)
        self.root.resizable(True, True)

        # ── Unified dark color palette (matches Outreach Bot) ────────────────
        BG       = "#1e2130"   # main app background
        PANEL    = "#252b3b"   # frame / panel background
        CARD_B   = "#1a3a5c"   # blue stat card
        CARD_G   = "#1a3a2a"   # green stat card
        CARD_Y   = "#3a2e10"   # amber stat card
        CARD_R   = "#3a1a1a"   # red stat card
        BORDER   = "#3d4a6a"   # widget borders
        FG       = "#e8eaf6"   # primary text
        FG2      = "#9aa3c2"   # secondary / label text
        ENTRY_BG = "#2d3550"   # entry field bg
        self._theme = dict(
            BG=BG, PANEL=PANEL, BORDER=BORDER, FG=FG, FG2=FG2,
            ENTRY_BG=ENTRY_BG, CARD_B=CARD_B, CARD_G=CARD_G,
            CARD_Y=CARD_Y, CARD_R=CARD_R
        )
        self.root.configure(bg=BG)

        # Override rigid native OS themes with 'clam' so custom padding and fonts reliably work
        style = ttk.Style()
        style.theme_use('clam')

        # ── Rich global dark styles ──────────────────────────────────────────
        BASE_FONT  = ("Segoe UI", 10)
        BOLD_FONT  = ("Segoe UI", 10, "bold")
        SMALL_FONT = ("Segoe UI", 8)

        style.configure(".",               font=BASE_FONT, background=PANEL, foreground=FG)
        style.configure("TFrame",          background=PANEL)
        style.configure("TLabel",          font=BASE_FONT, background=PANEL, foreground=FG, padding=2)
        style.configure("TEntry",          fieldbackground=ENTRY_BG, foreground=FG, insertcolor=FG, padding=5)
        style.configure("TButton",         padding=(8, 4), font=BASE_FONT)
        style.map("TButton",               background=[("active", "#424b6b"), ("disabled", "#2d3550")], foreground=[("disabled", "#9aa3c2")])
        style.configure("TCheckbutton",    background=PANEL, foreground=FG, font=BASE_FONT)
        style.map("TCheckbutton",          background=[("active", PANEL)])
        style.configure("TCombobox",       fieldbackground=ENTRY_BG, foreground=FG, background=PANEL, padding=4)
        style.map("TCombobox",             fieldbackground=[("readonly", ENTRY_BG)], foreground=[("readonly", FG)], background=[("active", "#424b6b")])
        style.configure("TSpinbox",        fieldbackground=ENTRY_BG, foreground=FG, insertcolor=FG)
        style.configure("TLabelframe",     background=PANEL, relief="groove")
        style.configure("TLabelframe.Label", font=BOLD_FONT, background=PANEL, foreground="#7dd3fc")
        style.configure("TNotebook",       background=BG, tabmargins=[2, 4, 2, 0])
        style.configure("TNotebook.Tab",   padding=[18, 9], font=BOLD_FONT, background="#252b3b", foreground=FG2)
        style.map("TNotebook.Tab",         background=[("selected", PANEL)], foreground=[("selected", FG)])
        style.configure("TScrollbar",      background=BORDER, troughcolor=PANEL)
        style.configure("Treeview",        rowheight=28, font=BASE_FONT,
                         background=ENTRY_BG, foreground=FG, fieldbackground=ENTRY_BG)
        style.configure("Treeview.Heading", font=BOLD_FONT, background=PANEL, foreground="#7dd3fc")
        style.map("Treeview",              background=[("selected", "#2563eb")], foreground=[("selected", "white")])
        style.configure("Horizontal.TProgressbar", background="#16a34a", troughcolor=ENTRY_BG)

        # ── Named button styles ──────────────────────────────────────────────
        for name, bg, abg in [
            ("Start",   "#16a34a", "#15803d"),
            ("Stop",    "#dc2626", "#b91c1c"),
            ("Blue",    "#2563eb", "#1d4ed8"),
            ("Amber",   "#d97706", "#b45309"),
            ("Skip",    "#7c3aed", "#6d28d9"),
        ]:
            style.configure(f"{name}.TButton",
                background=bg, foreground="white",
                font=BOLD_FONT, padding=(10, 6), relief="flat")
            style.map(f"{name}.TButton",
                background=[("active", abg), ("disabled", "#374151")])

        # ── Dark Header Banner ───────────────────────────────────────────────
        header = tk.Frame(self.root, bg=BG, height=58)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="🎲  Dice Auto Apply Bot",
                 bg=BG, fg="#93c5fd",
                 font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT, padx=18, pady=12)
        self.status_var = tk.StringVar(value="◉  Idle")
        self.status_lbl = tk.Label(header, textvariable=self.status_var,
                                   bg=BG, fg="#4ade80",
                                   font=("Segoe UI", 11, "bold"))
        self.status_lbl.pack(side=tk.RIGHT, padx=18)
        
        # Set app icon if available
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "resources", "app_icon.png")
            if os.path.exists(icon_path):
                # For Windows
                if sys.platform == 'win32':
                    self.root.iconbitmap(icon_path)
                # For macOS and others that support .png icons
                else:
                    img = tk.PhotoImage(file=icon_path)
                    self.root.iconphoto(True, img)
        except Exception as e:
            pass
        
        # Disable PyAutoGUI failsafe
        pyautogui.FAILSAFE = False
        
        # Configure logging
        self.setup_logging()
        
        # Initialize variables
        self.driver = None
        self.job_thread = None
        self.running = False
        self.is_paused = False
        self.skip_requested = False
        
        # Load configuration if exists
        self.next_id = 1
        
        # Initialize AI components early
        from core.learning_engine import LearningEngine
        self.learning_engine = LearningEngine()
        self.semantic_matcher = None  # Delayed init until config is loaded
        self.groq_scorer      = None  # Groq scorer — init alongside semantic matcher
        self.ai_loading = False
        
        self.load_config()

        # Transparent-mode state (initialise before any tab touches the root)
        self.transparent_var    = tk.BooleanVar(value=False)
        self.transparency_level = tk.DoubleVar(value=0.85)

        # Create the tabs
        main_area = ttk.Frame(self.root, padding=(10, 8))
        main_area.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(main_area)
        self.notebook.pack(fill="both", expand=True)

        # Create tab frames
        self.main_tab        = ttk.Frame(self.notebook, padding=10)
        self.resumes_tab     = ttk.Frame(self.notebook, padding=10)
        self.settings_tab    = ttk.Frame(self.notebook, padding=10)
        self.ai_trainer_tab  = ttk.Frame(self.notebook, padding=10)
        self.groq_scanner_tab= ttk.Frame(self.notebook, padding=10)
        self.logs_tab        = ttk.Frame(self.notebook, padding=10)

        # Add tabs to notebook
        self.notebook.add(self.main_tab,       text="🎲  Run Bot")
        self.notebook.add(self.resumes_tab,    text="📄  Resumes")
        self.notebook.add(self.settings_tab,   text="⚙  Settings")
        self.notebook.add(self.ai_trainer_tab, text="🧠  AI Training")
        self.notebook.add(self.groq_scanner_tab, text="🤖 Groq Scanner")
        self.notebook.add(self.logs_tab,       text="📋  Logs")

        # Set up UI for each tab
        self.setup_main_tab()
        self.setup_resumes_tab()
        self.setup_settings_tab()
        self.setup_ai_trainer_tab()
        self.setup_groq_scanner_tab()
        self.setup_logs_tab()

        # Log that app is started
        self.logger.info("Application started")
        
    def setup_logging(self):
        """Set up logging for the application"""
        # Create logs directory if needed
        logs_dir = os.path.join(os.path.dirname(__file__), "logs")
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
            
        # Create log filename with timestamp
        log_file = os.path.join(logs_dir, f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        
        # Configure logging
        # Use UTF-8 on the stream handler so emoji/unicode in log messages
        # don't crash on Windows terminals that default to cp1252.
        import io
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler(utf8_stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def load_config(self):
        """Load configuration from config file"""
        self.config_dir = os.path.join(os.path.dirname(__file__), "config")
        self.config_file = os.path.join(self.config_dir, "settings.json")
        
        # Default values
        self.search_queries = ["AI ML", "Gen AI", "Agentic AI", "Data Engineer", "Data Analyst", "Machine Learning"]
        self.exclude_keywords = ["Manager", "Director",".net", "SAP","java","w2 only","only w2","no c2c",
        "only on w2","w2 profiles only","tester","f2f"]
        self.include_keywords = ["AI", "Artificial","Inteligence","Machine","Learning", "ML", "Data", "NLP", "ETL",
        "Natural Language Processing","analyst","scientist","senior","cloud", 
        "aws","gcp","Azure","agentic","python","rag","llm"]
        self.headless_mode = False
        self.batch_excel_saves = True
        self.job_limit = 1500
        self.resume_profiles = []
        self.editing_idx = None  # Track index of profile being edited
        self.profile_name_boost_mode = 'off'  # Default: disabled until user enables per-profile
        self.semantic_enabled = True # New feature enabled by default
        self.filter_employment_type = "ALL"
        self.filter_posted_date = "THREE"
        self.filter_work_setting = "ALL"
        self.filter_easy_apply   = False
        self.filter_location     = ""
        self.filter_radius       = "30"
        self.filter_will_sponsor = False
        self.filter_salary_min   = ""
        self.auto_scan_enabled = False
        self.last_auto_scan_timestamp = ""
        self.application_answers = {}

        # Try to load from file if it exists
        import json
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.search_queries = config.get('search_queries', self.search_queries)
                    self.exclude_keywords = config.get('exclude_keywords', self.exclude_keywords)
                    self.include_keywords = config.get('include_keywords', self.include_keywords)
                    self.headless_mode = config.get('headless_mode', self.headless_mode)
                    self.job_limit = config.get('job_application_limit', self.job_limit)
                    self.resume_profiles = config.get('resume_profiles', self.resume_profiles)
                    # Assign unique IDs to profiles for reliable GUI management
                    for p in self.resume_profiles:
                        if 'id' not in p:
                            p['id'] = self.next_id
                        self.next_id = max(self.next_id, p.get('id', 0) + 1)
                    
                    self.profile_name_boost_mode = config.get('profile_name_boost_mode', 'off')
                    self.semantic_enabled = config.get('semantic_enabled', True)
                    self.filter_employment_type = config.get('filter_employment_type', 'ALL')
                    self.filter_posted_date = config.get('filter_posted_date', 'THREE')
                    self.filter_work_setting = config.get('filter_work_setting', 'ALL')
                    self.filter_easy_apply = config.get('filter_easy_apply', False)
                    self.filter_location = config.get('filter_location', '')
                    self.filter_radius = config.get('filter_radius', '30')
                    self.filter_will_sponsor = config.get('filter_will_sponsor', False)
                    self.filter_salary_min = config.get('filter_salary_min', '')
                    self.auto_scan_enabled = config.get('auto_scan_enabled', False)
                    self.last_auto_scan_timestamp = config.get('last_auto_scan_timestamp', '')
                    self.application_answers = config.get('application_answers', {})

                    # ── Universal API Key Manager ───────────────────────────────
                    # Load new list-based keys. Migrate old groq_api_key if needed.
                    self.api_keys_list = config.get('api_keys_list', [])
                    old_groq_key = config.get('groq_api_key', '').strip()
                    if not self.api_keys_list and old_groq_key:
                        # Migrate old comma-separated key(s) to new format
                        for i, k in enumerate([k.strip() for k in old_groq_key.split(',') if k.strip()]):
                            self.api_keys_list.append({
                                'provider': 'Groq',
                                'name': f'Groq Key {i+1}',
                                'key': k
                            })
                    
                    window_geometry = config.get('window_geometry', '')
                    if window_geometry:
                        try:
                            import re
                            m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", window_geometry)
                            if m:
                                w, h = int(m.group(1)), int(m.group(2))
                                x, y = int(m.group(3)), int(m.group(4))
                                screen_w = self.root.winfo_screenwidth()
                                screen_h = self.root.winfo_screenheight()

                                # Clamp window size to fit current screen
                                w = min(w, screen_w)
                                h = min(h, screen_h)
                                w = max(w, 860)
                                h = max(h, 600)

                                # If position would push the window off-screen, re-centre it
                                if (x < -w // 2 or x > screen_w - 40 or
                                        y < -20 or y > screen_h - 40):
                                    x = max(0, (screen_w - w) // 2)
                                    y = max(0, (screen_h - h) // 2)

                                self.root.geometry(f"{w}x{h}+{x}+{y}")
                            else:
                                # Geometry string has no position — just apply the size
                                self.root.geometry(window_geometry)
                        except Exception:
                            pass  # Keep the adaptive default set in __init__
                    
                    # Initialize Semantic Matcher in BACKGROUND if enabled
                    if self.semantic_enabled and self.resume_profiles:
                        self.ai_loading = True
                        if hasattr(self, 'root'):
                            self.root.after(100, lambda: threading.Thread(target=self._init_ai_async, daemon=True).start())
                        else:
                            threading.Thread(target=self._init_ai_async, daemon=True).start()
                    
                    self.logger.info("Configuration loaded successfully")
            except Exception as e:
                self.logger.error(f"Error loading configuration: {e}")
        
    def _init_ai_async(self):
        """Initializes the AI Semantic Matcher and Groq Scorer in the background to avoid GUI freeze"""
        try:
            self.root.after(0, lambda: self.update_status("[AI] Matcher is initializing in background..."))

            # ── Local sentence-transformer model (~80MB, CPU-only) ────────────
            matcher = SemanticResumeMatcher(self.resume_profiles)

            # ── Groq Scorer — use API keys from Universal Key Manager ──────
            groq_scorer = None
            try:
                _keys_list = getattr(self, 'api_keys_list', [])
                _groq_keys = [entry['key'] for entry in _keys_list if entry.get('provider', '').lower() == 'groq' and entry.get('key', '').strip()]
                _gkey = ','.join(_groq_keys)
                if _gkey:
                    groq_scorer = GroqResumeScorer(api_key=_gkey, log_callback=self.logger.info)
                    self.logger.info(f"[Groq] ✅ Resume scorer initialized with {len(_groq_keys)} key(s) (tiebreaker mode).")
                else:
                    self.logger.info("[Groq] ℹ️  No Groq keys found — Groq tiebreaking disabled.")
            except Exception as _ge:
                self.logger.warning(f"[Groq] ⚠️  Could not init scorer: {_ge}")

            def _on_complete():
                self.semantic_matcher = matcher
                self.groq_scorer      = groq_scorer
                self.ai_loading = False
                msg = "[AI] Semantic Matcher + Groq Scorer ready."
                self.logger.info(msg)
                try:
                    self.status_label.config(text=msg)
                except Exception:
                    pass
                try:
                    self.refresh_ai_stats()
                except Exception:
                    pass
                
                # Check for auto scan
                if getattr(self, 'auto_scan_enabled', False):
                    self.root.after(3000, self._run_auto_scan_if_needed)

            self.root.after(0, _on_complete)

        except Exception as e:
            err_str = str(e)
            def _on_error(err_msg=err_str):
                self.ai_loading = False
                safe_msg = f"Background AI Init failed: {err_msg}"
                self.logger.error(safe_msg)
                try:
                    short = err_msg[:50]
                    self.status_label.config(text=f"AI Matcher failed to load: {short}...")
                except Exception:
                    pass

            self.root.after(0, _on_error)

    def _persist_config(self):
        """
        Low-level disk write — uses ONLY the in-memory instance attributes,
        NOT widget .get() calls.  Safe to call from any tab at any time.
        """
        import json
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
            
        # Pull current widget values where they exist, fall back to stored attrs
        def _safe_get(attr, widget_attr=None, default=None):
            if widget_attr and hasattr(self, widget_attr):
                try:
                    val = getattr(self, widget_attr).get()
                    if val is not None:
                        return val
                except Exception:
                    pass
            return getattr(self, attr, default)

        profiles = getattr(self, 'resume_profiles', [])
        config = {
            'search_queries':    [q.strip() for q in _safe_get('search_queries', 'search_query_entry', '').split(',') if q.strip()] if isinstance(_safe_get('search_queries', 'search_query_entry', ''), str) else getattr(self, 'search_queries', []),
            'exclude_keywords':  [k.strip() for k in _safe_get('exclude_keywords', 'exclude_keywords_entry', '').split(',') if k.strip()] if isinstance(_safe_get('exclude_keywords', 'exclude_keywords_entry', ''), str) else getattr(self, 'exclude_keywords', []),
            'include_keywords':  [k.strip() for k in _safe_get('include_keywords', 'include_keywords_entry', '').split(',') if k.strip()] if isinstance(_safe_get('include_keywords', 'include_keywords_entry', ''), str) else getattr(self, 'include_keywords', []),
            'headless_mode':     _safe_get('headless_mode', 'headless_var', False),
            'batch_excel_saves': _safe_get('batch_excel_saves', 'batch_save_var', True),
            'job_application_limit': _safe_get('job_limit', 'job_limit_var', 1500),
            'resume_profiles':   profiles,
            'profile_name_boost_mode': (
                getattr(self, 'name_boost_var', None) and
                self.name_boost_var.get().split('|')[0].strip().lower()
            ) or getattr(self, 'profile_name_boost_mode', 'off'),
            'semantic_enabled': _safe_get('semantic_enabled', 'semantic_var', True),
            'filter_employment_type': _safe_get('filter_employment_type', 'emp_type_var', 'ALL'),
            'filter_posted_date': _safe_get('filter_posted_date', 'posted_date_var', 'THREE'),
            'filter_work_setting': _safe_get('filter_work_setting', 'work_setting_var', 'ALL'),
            'filter_easy_apply': _safe_get('filter_easy_apply', 'easy_apply_var', False),
            'filter_location': _safe_get('filter_location', 'location_entry', ''),
            'filter_radius': _safe_get('filter_radius', 'radius_var', '30'),
            'filter_will_sponsor': _safe_get('filter_will_sponsor', 'will_sponsor_var', False),
            'filter_salary_min': _safe_get('filter_salary_min', 'salary_min_entry', ''),
            'window_geometry': self.root.geometry(),
            'auto_scan_enabled': _safe_get('auto_scan_enabled', 'auto_scan_var', False),
            'last_auto_scan_timestamp': getattr(self, 'last_auto_scan_timestamp', ''),
            'api_keys_list': getattr(self, 'api_keys_list', []),
            'application_answers': getattr(self, 'application_answers', {}),
        }

        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=4)
            self.logger.info(f"Configuration persisted to disk. Profiles count: {len(profiles)}")
        except Exception as e:
            self.logger.error(f"Failed to persist configuration: {e}")

    def save_config(self, silent: bool = False):
        """Save configuration to config file (reads widgets + writes disk)."""
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)

        import json
        try:
            # Read from all widgets (only call this when all tabs are initialised)
            config = {
                'search_queries':    [q.strip() for q in self.search_query_entry.get().split(',') if q.strip()],
                'exclude_keywords':  [k.strip() for k in self.exclude_keywords_entry.get().split(',') if k.strip()],
                'include_keywords':  [k.strip() for k in self.include_keywords_entry.get().split(',') if k.strip()],
                'headless_mode':     self.headless_var.get(),
                'batch_excel_saves': getattr(self, 'batch_save_var', tk.BooleanVar(value=True)).get() if hasattr(self, 'batch_save_var') else getattr(self, 'batch_excel_saves', True),
                'job_application_limit': self.job_limit_var.get(),
                'resume_profiles':   self.resume_profiles,
                'profile_name_boost_mode': self.name_boost_var.get().split('|')[0].strip().lower(),
                'semantic_enabled': self.semantic_var.get(),
                'filter_employment_type': getattr(self, 'emp_type_var', tk.StringVar(value='ALL')).get(),
                'filter_posted_date': getattr(self, 'posted_date_var', tk.StringVar(value='THREE')).get(),
                'filter_work_setting': getattr(self, 'work_setting_var', tk.StringVar(value='ALL')).get(),
                'filter_easy_apply': getattr(self, 'easy_apply_var', tk.BooleanVar(value=False)).get(),
                'filter_location': getattr(self, 'location_entry', None) and self.location_entry.get() or getattr(self, 'filter_location', ''),
                'filter_radius': getattr(self, 'radius_var', tk.StringVar(value='30')).get(),
                'filter_will_sponsor': getattr(self, 'will_sponsor_var', tk.BooleanVar(value=False)).get(),
                'filter_salary_min': getattr(self, 'salary_min_entry', None) and self.salary_min_entry.get() or getattr(self, 'filter_salary_min', ''),
                'window_geometry': self.root.geometry(),
                'auto_scan_enabled': getattr(self, 'auto_scan_var', tk.BooleanVar(value=False)).get(),
                'last_auto_scan_timestamp': getattr(self, 'last_auto_scan_timestamp', ''),
                'api_keys_list': getattr(self, 'api_keys_list', []),
                'application_answers': getattr(self, 'application_answers', {}),
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=4)

            # Persist .env credentials
            username = self.username_entry.get()
            password = self.password_entry.get()
            if username and password:
                update_dice_credentials(username, password)

            if not silent:
                messagebox.showinfo("Settings Saved", "Your settings have been saved successfully.")
            self.logger.info("Settings saved successfully")

        except Exception as e:
            self.logger.error(f"Error saving configuration: {e}")
            # Fallback: write what we can from in-memory state
            try:
                self._persist_config()
                self.logger.info("Settings saved via fallback persist")
            except Exception as e2:
                self.logger.error(f"Fallback persist also failed: {e2}")
                if not silent:
                    messagebox.showerror("Error", f"Could not save settings: {str(e)}")
        
    def calculate_time_estimate(self, jobs_count):
        """Calculate and display estimated completion time based on job count"""
        # Calculate based on historical data or defaults
        # Average time per job is around 10 seconds, but can vary
        avg_job_time = 10  # seconds
        total_seconds = jobs_count * avg_job_time
        
        # Add overhead time for initialization, etc.
        overhead_seconds = 60  # 1 minute overhead
        
        total_seconds += overhead_seconds
        
        # Calculate hours, minutes, seconds
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        # Create time string
        time_str = ""
        if hours > 0:
            time_str += f"{int(hours)} hours "
        if minutes > 0 or hours > 0:
            time_str += f"{int(minutes)} minutes "
        time_str += f"{int(seconds)} seconds"
        
        # Update UI with estimate
        self.update_status(f"Estimated completion time: {time_str}")
        return time_str

    def setup_main_tab(self):
        """Set up the main tab UI — compact, responsive layout."""

        # ── Inputs ───────────────────────────────────────────────────────────
        input_frame = ttk.LabelFrame(self.main_tab, text="🔍  Search & Filter")
        input_frame.pack(fill="x", padx=10, pady=(8, 4))
        input_frame.columnconfigure(1, weight=1)
        input_frame.columnconfigure(3, weight=1)

        ttk.Label(input_frame, text="Job Titles:").grid(row=0, column=0, sticky="w", padx=(8,4), pady=4)
        self.search_query_entry = ttk.Entry(input_frame)
        self.search_query_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=4, pady=4)
        self.search_query_entry.insert(0, ", ".join(self.search_queries))

        ttk.Label(input_frame, text="Exclude:").grid(row=1, column=0, sticky="w", padx=(8,4), pady=4)
        self.exclude_keywords_entry = ttk.Entry(input_frame)
        self.exclude_keywords_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.exclude_keywords_entry.insert(0, ", ".join(self.exclude_keywords))

        ttk.Label(input_frame, text="Include:").grid(row=1, column=2, sticky="w", padx=(12,4), pady=4)
        self.include_keywords_entry = ttk.Entry(input_frame)
        self.include_keywords_entry.grid(row=1, column=3, sticky="ew", padx=4, pady=4)
        self.include_keywords_entry.insert(0, ", ".join(self.include_keywords))

        # ── Filters ──────────────────────────────────────────────────────────
        filter_frame = ttk.Frame(input_frame)
        filter_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 4))
        
        # Row 1: Existing filters
        row1_frame = ttk.Frame(filter_frame)
        row1_frame.pack(side="top", fill="x", pady=(0, 4))
        
        ttk.Label(row1_frame, text="Emp Type:").pack(side="left", padx=(8, 2))
        self.emp_type_var = tk.StringVar(value=self.filter_employment_type)
        ttk.Combobox(row1_frame, textvariable=self.emp_type_var, values=["ALL", "FULLTIME", "CONTRACTS", "PARTTIME", "THIRD_PARTY"], state="readonly", width=12).pack(side="left", padx=(0, 10))

        ttk.Label(row1_frame, text="Date:").pack(side="left", padx=(0, 2))
        self.posted_date_var = tk.StringVar(value=self.filter_posted_date)
        ttk.Combobox(row1_frame, textvariable=self.posted_date_var, values=["ALL", "ONE", "THREE", "SEVEN", "FOURTEEN", "TWENTY_ONE"], state="readonly", width=12).pack(side="left", padx=(0, 10))

        ttk.Label(row1_frame, text="Work:").pack(side="left", padx=(0, 2))
        self.work_setting_var = tk.StringVar(value=self.filter_work_setting)
        ttk.Combobox(row1_frame, textvariable=self.work_setting_var, values=["ALL", "REMOTE", "ONSITE", "HYBRID"], state="readonly", width=10).pack(side="left", padx=(0, 10))

        # Row 2: New filters
        row2_frame = ttk.Frame(filter_frame)
        row2_frame.pack(side="top", fill="x", pady=(0, 2))

        ttk.Label(row2_frame, text="Location:").pack(side="left", padx=(8, 2))
        self.location_entry = ttk.Entry(row2_frame, width=15)
        self.location_entry.pack(side="left", padx=(0, 10))
        self.location_entry.insert(0, self.filter_location)

        ttk.Label(row2_frame, text="Radius (miles):").pack(side="left", padx=(0, 2))
        self.radius_var = tk.StringVar(value=self.filter_radius)
        ttk.Combobox(row2_frame, textvariable=self.radius_var, values=["10", "20", "30", "50", "75", "100"], state="readonly", width=5).pack(side="left", padx=(0, 10))

        ttk.Label(row2_frame, text="Min Salary:").pack(side="left", padx=(0, 2))
        self.salary_min_entry = ttk.Entry(row2_frame, width=10)
        self.salary_min_entry.pack(side="left", padx=(0, 10))
        self.salary_min_entry.insert(0, self.filter_salary_min)

        self.easy_apply_var = tk.BooleanVar(value=self.filter_easy_apply)
        ttk.Checkbutton(row2_frame, text="Easy Apply Only", variable=self.easy_apply_var).pack(side="left", padx=(0, 10))

        self.will_sponsor_var = tk.BooleanVar(value=self.filter_will_sponsor)
        ttk.Checkbutton(row2_frame, text="Will Sponsor Visa", variable=self.will_sponsor_var).pack(side="left", padx=(0, 10))

        # ── Action buttons ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(self.main_tab)
        btn_frame.pack(fill="x", padx=10, pady=4)
        for col in range(4):
            btn_frame.columnconfigure(col, weight=1)

        self.start_button = ttk.Button(
            btn_frame, text="▶  Start Applying", command=self.start_applying, style="Start.TButton"
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=2)

        self.pause_button = ttk.Button(
            btn_frame, text="⏸  Pause", command=self.toggle_pause, state="disabled", style="Amber.TButton"
        )
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        self.stop_button = ttk.Button(
            btn_frame, text="⏹  Stop", command=self.stop_applying, style="Stop.TButton", state="disabled"
        )
        self.stop_button.grid(row=0, column=2, sticky="ew", padx=4, pady=2)

        self.skip_button = ttk.Button(
            btn_frame, text="⏭  Skip Job", command=self.skip_job, style="Skip.TButton", state="disabled"
        )
        self.skip_button.grid(row=0, column=3, sticky="ew", padx=(4, 0), pady=2)

        # ── Progress ─────────────────────────────────────────────────────────
        progress_frame = ttk.LabelFrame(self.main_tab, text="⚡  Progress")
        progress_frame.pack(fill="x", padx=10, pady=4)

        self.status_label = ttk.Label(progress_frame, text="Ready to start.", font=("Segoe UI", 9))
        self.status_label.pack(side="left", fill="x", expand=True)

        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress_bar.pack(fill="x", padx=8, pady=(2, 4))

        # ── Stats row (dark card style) ───────────────────────────────────────
        t = self._theme
        BORDER = t["BORDER"]
        stats_frame = ttk.Frame(self.main_tab)
        stats_frame.pack(fill="x", padx=10, pady=(4, 6))
        for col in range(4):
            stats_frame.columnconfigure(col, weight=1)

        def _dark_card(col, title, init="0", bg="#1a3a5c", fg="#60a5fa", fg2="#93c5fd"):
            f = tk.Frame(stats_frame, bg=bg, bd=0, highlightthickness=1, highlightbackground=BORDER)
            f.grid(row=0, column=col, sticky="ew", padx=5, pady=2)
            tk.Label(f, text=title, bg=bg, fg=fg2, font=("Segoe UI", 8, "bold")).pack(pady=(8, 0))
            lbl = tk.Label(f, text=init, bg=bg, fg=fg, font=("Segoe UI", 20, "bold"))
            lbl.pack(pady=(2, 8))
            return lbl

        self.jobs_found_label     = _dark_card(0, "TOTAL JOBS",  bg=t["CARD_B"], fg="#60a5fa",  fg2="#93c5fd")
        self.jobs_applied_label   = _dark_card(1, "APPLIED",     bg=t["CARD_G"], fg="#4ade80",  fg2="#86efac")
        self.jobs_failed_label    = _dark_card(2, "FAILED",      bg=t["CARD_R"], fg="#f87171",  fg2="#fca5a5")
        self.estimated_time_label = _dark_card(3, "EST. TIME",   bg=t["CARD_Y"], fg="#fbbf24",  fg2="#fcd34d", init="--")

        # ── Data Management & Transparency controls ───────────────────────────
        ctrl_frame = ttk.LabelFrame(self.main_tab, text="🗂  Quick Open & UI Controls")
        ctrl_frame.pack(fill="x", padx=10, pady=(0, 6))
        
        row2 = ttk.Frame(ctrl_frame)
        row2.pack(fill="x", pady=2, padx=4)
        self.continuous_var = tk.BooleanVar(value=getattr(self, 'continuous_mode', False))
        ck_continuous = LabeledToggleSwitch(
            row2,
            text="Continuous Mode",
            variable=self.continuous_var,
            bg=self._theme["PANEL"], fg=self._theme["FG"]
        )
        ck_continuous.pack(side="left", padx=4)

        ctrl_top = ttk.Frame(ctrl_frame)
        ctrl_top.pack(fill="x", pady=(4, 2), padx=4)

        ttk.Button(ctrl_top, text="✔  Applied Jobs",
                   command=lambda: self.open_excel_file("applied_jobs.xlsx"),
                   style="Blue.TButton").pack(side="left", padx=4)
        ttk.Button(ctrl_top, text="✘  Not Applied",
                   command=lambda: self.open_excel_file("not_applied_jobs.xlsx"),
                   style="Amber.TButton").pack(side="left", padx=4)
        ttk.Button(ctrl_top, text="⊘  Excluded Jobs",
                   command=lambda: self.open_excel_file("excluded_jobs.xlsx"),
                   style="Stop.TButton").pack(side="left", padx=4)

        ctrl_bot = ttk.Frame(ctrl_frame)
        ctrl_bot.pack(fill="x", pady=(2, 6), padx=4)

        PANEL = t["PANEL"]
        FG    = t["FG"]
        ck_trans = LabeledToggleSwitch(
            ctrl_bot, text="👁  Transparent Mode (Stay on Top)",
            variable=self.transparent_var,
            command=self._toggle_transparent,
            bg=PANEL, fg=FG
        )
        ck_trans.pack(side="left", padx=(4, 10))

        ttk.Label(ctrl_bot, text="Opacity:").pack(side="left", padx=(20, 5))
        trans_scale = ttk.Scale(
            ctrl_bot, from_=0.1, to_=1.0,
            variable=self.transparency_level,
            orient=tk.HORIZONTAL,
            command=lambda _: self._toggle_transparent(),
            length=150
        )
        trans_scale.pack(side="left", padx=(0, 5))

        # ── Speed controls ───────────────────────────────────────────────────
        ctrl_speed = ttk.Frame(ctrl_frame)
        ctrl_speed.pack(fill="x", pady=(2, 6), padx=4)
        
        ttk.Label(ctrl_speed, text="🚀  Bot Speed:").pack(side="left", padx=(4, 10))
        
        self.speed_var = tk.DoubleVar(value=1.0)
        
        def set_speed(val):
            self.speed_var.set(val)
            try:
                import core.main_script
                import utils.timing
                core.main_script.GLOBAL_SPEED_MULTIPLIER = val
                utils.timing.set_speed_multiplier(val)
                self.logger.info(f"⚡ Bot speed updated to {val}x")
            except Exception as e:
                print(f"Error setting speed: {e}")
            
        ttk.Radiobutton(ctrl_speed, text="0.5x (Relaxed)", variable=self.speed_var, value=0.5, command=lambda: set_speed(0.5)).pack(side="left", padx=5)
        ttk.Radiobutton(ctrl_speed, text="1x (Normal)",  variable=self.speed_var, value=1.0, command=lambda: set_speed(1.0)).pack(side="left", padx=5)
        ttk.Radiobutton(ctrl_speed, text="2x (Fast)",    variable=self.speed_var, value=2.0, command=lambda: set_speed(2.0)).pack(side="left", padx=5)
        ttk.Radiobutton(ctrl_speed, text="3x (Turbo)",   variable=self.speed_var, value=3.0, command=lambda: set_speed(3.0)).pack(side="left", padx=5)

        # ── Live log ─────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self.main_tab, text="📋  Live Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=8, wrap=tk.WORD, font=("Consolas", 8),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white"
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.config(state="disabled")

        self.log_handler = LogTextHandler(self.log_text)
        self.log_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        self.log_handler.setFormatter(formatter)
        self.logger.addHandler(self.log_handler)

    def _toggle_transparent(self, *_):
        """Toggle always-on-top transparent mode and apply the opacity slider value."""
        if self.transparent_var.get():
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", self.transparency_level.get())
        else:
            self.root.attributes("-topmost", False)
            self.root.attributes("-alpha", 1.0)

    def open_excel_file(self, filename):
        """Open an Excel file using the system default application"""
        try:
            if not os.path.exists(filename):
                if filename == "excluded_jobs.xlsx":
                    # Create the file if it doesn't exist
                    df = pd.DataFrame(columns=["Job Title", "Job URL", "Company", "Location", "Employment Type", "Posted Date", "Exclusion Reason"])
                    df.to_excel(filename, index=False)
                    self.logger.info(f"Created new {filename} file")
                else:
                    messagebox.showinfo("File Not Found", f"The file {filename} does not exist yet.")
                    return
                    
            # Open the file with the default system application
            if sys.platform == "win32":
                os.startfile(filename)
            elif sys.platform == "darwin":  # macOS
                subprocess.run(["open", filename])
            else:  # Linux
                subprocess.run(["xdg-open", filename])
                
            self.logger.info(f"Opened {filename}")
        except Exception as e:
            self.logger.error(f"Error opening {filename}: {e}")
            messagebox.showerror("Error", f"Could not open {filename}: {str(e)}")

    def setup_resumes_tab(self):
        """Set up the resumes tab UI with flexible paned layout"""
        style = ttk.Style()
        style.configure("Treeview", rowheight=30)

        # Main Paned window (Vertical split)
        self.resume_paned = ttk.Panedwindow(self.resumes_tab, orient="vertical")
        self.resume_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # Top Pane: Treeview Container
        tree_container = ttk.LabelFrame(self.resume_paned, text="Resume Profiles")
        self.resume_paned.add(tree_container, weight=3)

        tree_frame = ttk.Frame(tree_container)
        tree_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # --- Search Bar Implementation ---
        search_frame = ttk.Frame(tree_frame)
        search_frame.pack(fill="x", padx=5, pady=2)
        
        ttk.Label(search_frame, text="🔍 Search:").pack(side="left", padx=(0, 5))
        self.resume_search_var = tk.StringVar()
        self.resume_search_entry = ttk.Entry(search_frame, textvariable=self.resume_search_var)
        self.resume_search_entry.pack(side="left", fill="x", expand=True)
        
        def _on_search_key(event):
            self.refresh_resume_list(self.resume_search_var.get())
        self.resume_search_entry.bind("<KeyRelease>", _on_search_key)

        tree_scroll_y = ttk.Scrollbar(tree_frame)
        tree_scroll_y.pack(side="right", fill="y")
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient="horizontal")
        tree_scroll_x.pack(side="bottom", fill="x")

        columns = ("#", "ID", "Name", "Boost", "Unique Keywords", "General Keywords", "File Path")
        self.resume_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set
        )

        self.resume_tree.heading("#",               text="#")
        self.resume_tree.heading("Name",             text="Profile Name")
        self.resume_tree.heading("Boost",            text="Boost Mode")
        self.resume_tree.heading("Unique Keywords",  text="Unique/Priority")
        self.resume_tree.heading("General Keywords", text="General Skills")
        self.resume_tree.heading("File Path",        text="Local Path")

        self.resume_tree.column("#",               width=35,  minwidth=35,  stretch=tk.NO,  anchor="center")
        self.resume_tree.column("ID",              width=0,   minwidth=0,   stretch=tk.NO) # Hidden ID
        self.resume_tree.column("Name",             width=160, minwidth=120, stretch=tk.YES, anchor="w")
        self.resume_tree.column("Boost",            width=80,  minwidth=70,  stretch=tk.NO,  anchor="center")
        self.resume_tree.column("Unique Keywords",  width=300, minwidth=200, stretch=tk.YES, anchor="w")
        self.resume_tree.column("General Keywords", width=300, minwidth=200, stretch=tk.YES, anchor="w")
        self.resume_tree.column("File Path",        width=300, minwidth=200, stretch=tk.YES, anchor="w")

        self.resume_tree.tag_configure("boost_exact", background="#fff3cd", foreground="#7d5a00")
        self.resume_tree.tag_configure("boost_high",  background="#d4edda", foreground="#155724")
        self.resume_tree.tag_configure("boost_low",   background="#d1ecf1", foreground="#0c5460")
        self.resume_tree.tag_configure("boost_off",   background="#f8f9fa", foreground="#6c757d")

        self.resume_tree.pack(side="left", fill="both", expand=True)
        tree_scroll_y.config(command=self.resume_tree.yview)
        tree_scroll_x.config(command=self.resume_tree.xview)
        self.resume_tree.bind("<<TreeviewSelect>>", self.on_resume_select)

        self.profile_count_label = ttk.Label(tree_container, text="Total Profiles: 0")
        self.profile_count_label.pack(anchor="e", padx=10, pady=(0, 2))

        # Bottom Pane: Form Container
        self.form_container = ttk.LabelFrame(self.resume_paned, text="Edit Profile Details")
        self.resume_paned.add(self.form_container, weight=1)

        form_frame = ttk.Frame(self.form_container)
        form_frame.pack(fill="x", padx=10, pady=5)
        form_frame.columnconfigure(1, weight=1)
        form_frame.columnconfigure(3, weight=1)

        # Row 0: Name and Boost
        ttk.Label(form_frame, text="Profile Name:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.resume_name_entry = ttk.Entry(form_frame)
        self.resume_name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=2)

        ttk.Label(form_frame, text="Boost Level:").grid(row=0, column=2, sticky="w", padx=(20, 2), pady=2)
        self.profile_boost_var = tk.StringVar(value="off")
        PROFILE_BOOST_OPTIONS  = ["exact", "high", "low", "off"]
        self.profile_boost_combo = ttk.Combobox(
            form_frame, textvariable=self.profile_boost_var,
            values=PROFILE_BOOST_OPTIONS, state="readonly", width=10
        )
        self.profile_boost_combo.grid(row=0, column=3, sticky="w", padx=2, pady=2)

        self.boost_color_label = tk.Label(
            form_frame, text="  OFF  ", bg="#f8f9fa", fg="#6c757d",
            font=("Segoe UI", 8, "bold"), relief="solid", bd=1
        )
        self.boost_color_label.grid(row=0, column=4, padx=8, pady=2)

        def _on_boost_change(e=None):
            m = self.profile_boost_var.get()
            bg={"exact":"#fff3cd", "high":"#d4edda", "low":"#d1ecf1", "off":"#f8f9fa"}
            fg={"exact":"#7d5a00", "high":"#155724", "low":"#0c5460", "off":"#6c757d"}
            self.boost_color_label.config(text=f"  {m.upper()}  ", bg=bg.get(m,"#f8f9fa"), fg=fg.get(m,"#333"))
        self.profile_boost_combo.bind("<<ComboboxSelected>>", _on_boost_change)

        # Row 1: Unique Keywords
        ttk.Label(form_frame, text="Priority Skills:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.resume_unique_keywords_entry = ttk.Entry(form_frame)
        self.resume_unique_keywords_entry.grid(row=1, column=1, columnspan=4, sticky="ew", padx=2, pady=2)

        # Row 2: General Keywords
        ttk.Label(form_frame, text="General Skills:").grid(row=2, column=0, sticky="w", padx=2, pady=2)
        self.resume_keywords_entry = ttk.Entry(form_frame)
        self.resume_keywords_entry.grid(row=2, column=1, columnspan=4, sticky="ew", padx=2, pady=2)

        # Row 3: File Path
        ttk.Label(form_frame, text="Resume File:").grid(row=3, column=0, sticky="w", padx=2, pady=2)
        self.resume_path_entry = ttk.Entry(form_frame)
        self.resume_path_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=2, pady=2)
        ttk.Button(form_frame, text="📁 Browse", command=self.browse_resume_file).grid(row=3, column=3, padx=5, pady=2)
        self.auto_fill_btn = ttk.Button(form_frame, text="✨ Auto-Fill (AI)", command=self._start_auto_fill_profile)
        self.auto_fill_btn.grid(row=3, column=4, padx=5, pady=2)

        # Actions
        action_outer = ttk.Frame(self.form_container)
        action_outer.pack(fill="x", padx=10, pady=(5, 10))
        
        self.add_update_btn = ttk.Button(action_outer, text="✚ Add New Profile", command=self.add_resume_profile, style="Start.TButton")
        self.add_update_btn.pack(side="left", padx=5)

        ttk.Button(action_outer, text="↺ Clear / New", command=self.clear_resume_form).pack(side="left", padx=5)
        ttk.Button(action_outer, text="🗑 Delete Selected", command=self.delete_resume_profile).pack(side="left", padx=5)
        self.import_folder_btn = ttk.Button(action_outer, text="📂 Import Folder", command=self.browse_import_folder)
        self.import_folder_btn.pack(side="left", padx=5)

        ttk.Button(action_outer, text="⚙ Test Matcher", command=self.open_test_simulator).pack(side="right", padx=5)

        self.refresh_resume_list()

    def refresh_resume_list(self, search_query=""):
        for item in self.resume_tree.get_children():
            self.resume_tree.delete(item)

        TAG_MAP = {"exact": "boost_exact", "high": "boost_high", "low": "boost_low", "off": "boost_off"}

        profiles = getattr(self, "resume_profiles", [])
        
        # Always sort alphabetically by name (in-place)
        profiles.sort(key=lambda x: str(x.get("name", "")).lower())
        
        query = str(search_query).strip().lower()
        
        display_count = 0
        for i, profile in enumerate(profiles, start=1):
            name            = str(profile.get("name", ""))
            uni_kws         = ", ".join(profile.get("unique_keywords", []))
            gen_kws         = ", ".join(profile.get("keywords", []))
            file_path       = str(profile.get("file_path", ""))
            
            # Filtering logic
            if query:
                match_found = (
                    query in name.lower() or
                    query in uni_kws.lower() or
                    query in gen_kws.lower() or
                    query in file_path.lower()
                )
                if not match_found:
                    continue

            display_count += 1
            mode = profile.get("boost_mode", "high")
            tag  = TAG_MAP.get(mode, "boost_high")
            self.resume_tree.insert("", "end", tags=(tag,), values=(
                str(display_count),
                str(profile.get("id", "")),
                name,
                mode.upper(),
                uni_kws,
                gen_kws,
                file_path
            ))

        if hasattr(self, 'profile_count_label'):
            if query:
                self.profile_count_label.config(text=f"Showing: {display_count} / {len(profiles)}")
            else:
                self.profile_count_label.config(text=f"Total Profiles: {len(profiles)}")

    def clear_resume_form(self):
        """Reset the profile form and selection state"""
        self.resume_tree.selection_remove(self.resume_tree.selection())
        self.editing_idx = None
        self.editing_id  = None
        self.resume_name_entry.delete(0, tk.END)
        self.resume_unique_keywords_entry.delete(0, tk.END)
        self.resume_keywords_entry.delete(0, tk.END)
        self.resume_path_entry.delete(0, tk.END)
        self.profile_boost_var.set("off")
        self.boost_color_label.config(text="  OFF  ", bg="#f8f9fa", fg="#6c757d")
        if hasattr(self, 'add_update_btn'):
            self.add_update_btn.config(text="✚ Add New Profile")

        self.sync_trainer_profiles()

    def on_resume_select(self, event):
        selected = self.resume_tree.selection()
        if not selected:
            return

        item_id = selected[0]
        item    = self.resume_tree.item(item_id)
        values  = item.get("values", [])
        
        # Use the hidden ID to find the correct profile in the internal list
        try:
            profile_id = int(values[1])
        except (ValueError, IndexError):
            self.logger.error("Could not determine ID of selected profile")
            return
            
        profiles = getattr(self, "resume_profiles", [])
        self.editing_idx = None
        self.editing_id  = profile_id
        
        for idx, p in enumerate(profiles):
            if int(p.get("id", -1)) == profile_id:
                self.editing_idx = idx
                break
        
        if self.editing_idx is None:
            self.logger.warning(f"UI: Could not find original profile for ID: {profile_id}")
            return
            
        p = profiles[self.editing_idx]

        if len(values) >= 6:
            self.resume_name_entry.delete(0, tk.END)
            # Use columns indices correctly (ID was inserted at [1], so Name is at [2])
            self.resume_name_entry.insert(0, str(values[2]))
            
            if hasattr(self, 'add_update_btn'):
                self.add_update_btn.config(text="💾 Update Selected Profile")

            # Restore boost mode dropdown
            mode = str(values[3]).strip().lower()
            if mode in ("exact", "high", "low", "off"):
                self.profile_boost_var.set(mode)
                bg={"exact":"#fff3cd", "high":"#d4edda", "low":"#d1ecf1", "off":"#f8f9fa"}
                fg={"exact":"#7d5a00", "high":"#155724", "low":"#0c5460", "off":"#6c757d"}
                self.boost_color_label.config(text=f"  {mode.upper()}  ", bg=bg.get(mode,"#f8f9fa"), fg=fg.get(mode,"#333"))

            self.resume_unique_keywords_entry.delete(0, tk.END)
            val_uni = str(values[4]) if values[4] != "None" else ""
            self.resume_unique_keywords_entry.insert(0, val_uni)

            self.resume_keywords_entry.delete(0, tk.END)
            val_gen = str(values[5]) if values[5] != "None" else ""
            self.resume_keywords_entry.insert(0, val_gen)

            self.resume_path_entry.delete(0, tk.END)
            self.resume_path_entry.insert(0, str(values[6]))

    def browse_resume_file(self):
        import pathlib
        file_path = filedialog.askopenfilename(filetypes=[("PDF/Word Documents", "*.pdf *.docx *.doc")])
        if file_path:
            # Normalize to OS-native separators safely
            normalized_path = str(pathlib.Path(file_path).resolve())
            
            # Check if this exact file path is already used in an existing profile
            if hasattr(self, "resume_profiles"):
                for p in self.resume_profiles:
                    if p.get("file_path", "") == normalized_path:
                        messagebox.showwarning("File Already Exists", f"This resume file is already used by the profile '{p.get('name')}'.\nPlease select a different resume.")
                        return

            self.resume_path_entry.delete(0, tk.END)
            self.resume_path_entry.insert(0, normalized_path)
            # Scroll to the end of the entry so users can see the filename
            self.resume_path_entry.xview_moveto(1)

    def add_resume_profile(self):
        name          = self.resume_name_entry.get().strip()
        unique_kws_str= self.resume_unique_keywords_entry.get().strip()
        keywords_str  = self.resume_keywords_entry.get().strip()
        file_path     = self.resume_path_entry.get().strip()
        boost_mode    = self.profile_boost_var.get().strip().lower()

        if not name or not file_path:
            messagebox.showwarning("Missing Fields", "Please provide a Name and a File Path.")
            return
        if not os.path.exists(file_path):
            messagebox.showwarning("File Not Found", f"The file could not be found: {file_path}")
            return

        unique_keywords = [k.strip() for k in unique_kws_str.split(",") if k.strip()]
        keywords        = [k.strip() for k in keywords_str.split(",")  if k.strip()]

        if not hasattr(self, "resume_profiles"):
            self.resume_profiles = []

        # If we are editing, update the profile at that index
        target_profile = None
        if hasattr(self, 'editing_id') and self.editing_id is not None:
            for idx, p in enumerate(self.resume_profiles):
                # Use strict integer comparison to avoid type mismatches
                if int(p.get('id', -1)) == int(self.editing_id):
                    target_profile = p
                    self.editing_idx = idx # Keep sync
                    break
        
        if target_profile:
            # Check if updated name conflicts with ANOTHER profile
            for p in self.resume_profiles:
                if p.get("name") == name and p.get("id") != self.editing_id:
                    messagebox.showwarning("Duplicate Name", f"A DIFFERENT profile named '{name}' already exists. Select it to update.")
                    return

            target_profile["name"]            = name
            target_profile["unique_keywords"] = unique_keywords
            target_profile["keywords"]        = keywords
            target_profile["file_path"]       = file_path
            target_profile["boost_mode"]      = boost_mode
            self.logger.info(f"Updated profile ID {self.editing_id}: {name}")
        else:
            # Check for duplicate names if adding new
            for p in self.resume_profiles:
                if p.get("name") == name:
                    messagebox.showwarning("Duplicate Name", f"A profile named '{name}' already exists. Select it to update.")
                    return

            self.resume_profiles.append({
                "id":              self.next_id,
                "name":            name,
                "unique_keywords": unique_keywords,
                "keywords":        keywords,
                "file_path":       file_path,
                "boost_mode":      boost_mode,
            })
            self.logger.info(f"Added new profile (ID {self.next_id}): {name}")
            self.next_id += 1

        self._persist_config()
        # Keep current search filter after update
        self.refresh_resume_list(self.resume_search_var.get() if hasattr(self, 'resume_search_var') else "")
        self.clear_resume_form()

    def _start_auto_fill_profile(self):
        import os, threading, json
        file_path = self.resume_path_entry.get().strip()
        if not file_path or not os.path.exists(file_path):
            messagebox.showwarning("File Not Found", "Please browse and select a valid resume file first.")
            return
        
        # Verify Groq key exists from outreach_settings
        gkey = ""
        try:
            oc_path = os.path.join(os.path.dirname(__file__), "config", "outreach_settings.json")
            if os.path.exists(oc_path):
                with open(oc_path, "r") as f:
                    oc = json.load(f)
                gkey = oc.get("groq_api_key", "").strip()
        except Exception:
            pass

        if not gkey:
            messagebox.showwarning("Missing API Key", "Please configure your Groq API key in the Outreach Bot Config tab first.")
            return

        self.add_update_btn.config(state=tk.DISABLED)
        self.auto_fill_btn.config(state=tk.DISABLED, text="✨ Extracting.  ")
        self.logger.info("Starting AI auto-fill... please wait.")
        
        # Start animation
        self._auto_fill_animating = True
        self._animate_auto_fill_btn(0)
        
        t = threading.Thread(target=self._auto_fill_worker, args=(gkey, file_path), daemon=True)
        t.start()

    def _animate_auto_fill_btn(self, frame_idx):
        if getattr(self, "_auto_fill_animating", False):
            frames = ["✨ Extracting.  ", "✨ Extracting.. ", "✨ Extracting..."]
            self.auto_fill_btn.config(text=frames[frame_idx % len(frames)])
            self.root.after(400, self._animate_auto_fill_btn, frame_idx + 1)

    def _auto_fill_worker(self, gkey, file_path):
        try:
            from core.file_utils import extract_text_from_file
            from core.groq_resume_scorer import GroqResumeScorer
            
            text = extract_text_from_file(file_path)
            if not text:
                self.root.after(0, lambda: messagebox.showwarning("Extraction Failed", "Could not extract text from the file."))
                return

            profile_data = GroqResumeScorer.auto_extract_profile(gkey, text, self.logger.error)
            
            if profile_data:
                self.root.after(0, self._apply_auto_fill, profile_data)
            else:
                self.root.after(0, lambda: messagebox.showwarning("AI Failed", "Could not extract profile from the file using AI."))
        except Exception as e:
            self.logger.error(f"Auto-fill error: {e}")
            self.root.after(0, lambda err_msg=str(e): messagebox.showwarning("Error", f"Auto-fill error: {err_msg}"))
        finally:
            self._auto_fill_animating = False
            self.root.after(0, lambda: self.add_update_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.auto_fill_btn.config(state=tk.NORMAL, text="✨ Auto-Fill (AI)"))

    def _apply_auto_fill(self, profile_data):
        if profile_data.get("name"):
            self.resume_name_entry.delete(0, tk.END)
            self.resume_name_entry.insert(0, profile_data["name"])
        
        if profile_data.get("unique_keywords"):
            self.resume_unique_keywords_entry.delete(0, tk.END)
            self.resume_unique_keywords_entry.insert(0, ", ".join(profile_data["unique_keywords"]))
            
        if profile_data.get("keywords"):
            self.resume_keywords_entry.delete(0, tk.END)
            self.resume_keywords_entry.insert(0, ", ".join(profile_data["keywords"]))
            
        self.logger.info("AI Auto-fill complete! Please review and click 'Add New Profile'.")

    def browse_import_folder(self):
        """Bulk-import every resume file (pdf/docx/doc) found in a folder — including
        subfolders — as new resume profiles, skipping files already registered."""
        import pathlib
        import json

        default_dir = None
        for candidate in ("~/Desktop/R Tech/Resumes", "~/Desktop/RTech/Resumes", "~/Desktop"):
            expanded = os.path.expanduser(candidate)
            if os.path.isdir(expanded):
                default_dir = expanded
                break

        folder = filedialog.askdirectory(
            initialdir=default_dir or os.path.expanduser("~"),
            title="Select folder containing resumes (subfolders are scanned too)"
        )
        if not folder:
            return

        existing_paths = {
            str(pathlib.Path(p.get("file_path", "")).resolve())
            for p in getattr(self, "resume_profiles", [])
            if p.get("file_path")
        }

        VALID_EXT = (".pdf", ".docx", ".doc")
        found_files = []
        for root_dir, _dirs, files in os.walk(folder):
            for fname in files:
                if fname.startswith("~$") or fname.startswith("."):
                    continue
                if not fname.lower().endswith(VALID_EXT):
                    continue
                full_path = str(pathlib.Path(os.path.join(root_dir, fname)).resolve())
                if full_path in existing_paths:
                    continue
                found_files.append(full_path)

        if not found_files:
            messagebox.showinfo("Import Folder", "No new resume files found in that folder (everything is already imported).")
            return

        gkey = ""
        try:
            oc_path = os.path.join(os.path.dirname(__file__), "config", "outreach_settings.json")
            if os.path.exists(oc_path):
                with open(oc_path, "r") as f:
                    gkey = json.load(f).get("groq_api_key", "").strip()
        except Exception:
            pass

        if gkey:
            proceed_msg = (f"Found {len(found_files)} new resume(s).\n\n"
                           "Each will be added as a profile and analyzed with Groq AI "
                           "to auto-fill its name and skill keywords.\n\nContinue?")
        else:
            proceed_msg = (f"Found {len(found_files)} new resume(s).\n\n"
                           "No Groq API key is configured, so profiles will be added with "
                           "just a name (from the filename) and no keywords. Use "
                           "'Scan All Resumes' afterward (Outreach tab) to auto-fill keywords.\n\nContinue?")

        if not messagebox.askyesno("Import Folder", proceed_msg):
            return

        self.import_folder_btn.config(state=tk.DISABLED, text="📂 Importing...")
        self.add_update_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._import_folder_worker, args=(found_files, gkey), daemon=True).start()

    def _import_folder_worker(self, file_paths, gkey):
        import pathlib
        from core.file_utils import extract_text_from_file

        scorer = None
        if gkey:
            try:
                from core.groq_resume_scorer import GroqResumeScorer
                scorer = GroqResumeScorer(api_key=gkey, log_callback=self.logger.error)
            except Exception as e:
                self.logger.error(f"[Import Folder] Could not init Groq scorer: {e}")

        added, skipped = 0, 0
        existing_names = {str(p.get("name", "")).strip().lower() for p in self.resume_profiles}

        for idx, file_path in enumerate(file_paths):
            display_name = os.path.basename(file_path)
            self.logger.info(f"[Import Folder] ({idx + 1}/{len(file_paths)}) Processing: {display_name}")

            name = pathlib.Path(file_path).stem.replace("_", " ").replace("-", " ").strip()
            unique_keywords, keywords = [], []

            try:
                text = extract_text_from_file(file_path)
                if scorer and text:
                    profile_data = GroqResumeScorer.auto_extract_profile(gkey, text, self.logger.error)
                    if profile_data:
                        name = profile_data.get("name", name) or name
                        unique_keywords = profile_data.get("unique_keywords", []) or []
                        keywords = profile_data.get("keywords", []) or []
            except Exception as e:
                self.logger.error(f"[Import Folder] Failed to analyze {display_name}: {e}")

            base_name = name
            suffix = 2
            while name.strip().lower() in existing_names:
                name = f"{base_name} ({suffix})"
                suffix += 1
            existing_names.add(name.strip().lower())

            self.resume_profiles.append({
                "id":              self.next_id,
                "name":            name,
                "unique_keywords": unique_keywords,
                "keywords":        keywords,
                "file_path":       file_path,
                "boost_mode":      "off",
            })
            self.next_id += 1
            added += 1

        self.root.after(0, self._persist_config)

        def _done():
            self.import_folder_btn.config(state=tk.NORMAL, text="📂 Import Folder")
            self.add_update_btn.config(state=tk.NORMAL)
            self.refresh_resume_list(self.resume_search_var.get() if hasattr(self, 'resume_search_var') else "")
            msg = f"Imported {added} new resume profile(s)."
            if not gkey:
                msg += "\n\nTip: set your Groq API key, then use 'Scan All Resumes' (Outreach tab) to auto-fill keywords for the new profiles."
            messagebox.showinfo("Import Folder Complete", msg)
        self.root.after(0, _done)

    def delete_resume_profile(self):
        selected = self.resume_tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a resume profile to delete.")
            return
            
        item = self.resume_tree.item(selected[0])
        try:
            # We identify the profile by ID for deletion
            profile_id = int(item.get("values", [])[1])
            profile_name = str(item.get("values", [])[2])
        except (ValueError, IndexError):
            self.logger.error("Could not determine ID/name of profile to delete")
            return
        
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete profile '{profile_name}'?"):
            profiles = getattr(self, "resume_profiles", [])
            target_idx = None
            for idx, p in enumerate(profiles):
                if p.get("id") == profile_id:
                    target_idx = idx
                    break
                if str(p.get("name", "")) == profile_name:
                    target_idx = idx
                    break
            
            if target_idx is not None:
                deleted_profile = profiles.pop(target_idx)
                self.logger.info(f"Deleted resume profile: {deleted_profile.get('name', 'Unknown')}")
                
                # Delete from AI Training data
                try:
                    if hasattr(self, 'learning_engine') and self.learning_engine:
                        self.learning_engine.delete_profile_history(profile_id)
                except Exception as e:
                    self.logger.error(f"Error removing AI training data: {e}")
                    
                # Delete from Semantic Matcher
                if getattr(self, 'semantic_enabled', False) and getattr(self, 'semantic_matcher', None):
                    try:
                        self.semantic_matcher.delete_profile(profile_id)
                    except Exception as e:
                        self.logger.error(f"Error removing semantic profile: {e}")
                
                self._persist_config()
                self.refresh_resume_list(self.resume_search_var.get())
                self.clear_resume_form()
                
                if hasattr(self, 'refresh_ai_stats'):
                    self.refresh_ai_stats()
            else:
                self.logger.error(f"Delete failed: Profile '{profile_name}' not found in internal list.")
                messagebox.showerror("Error", "Could not delete profile: it was not found in the master list.")

    def open_test_simulator(self):
        """Open a window to test which profile matches a given job description text based on configured keywords."""
        test_window = tk.Toplevel(self.root)
        test_window.title("Test Profile Matching Simulator")
        test_window.geometry("780x700")

        t = self._theme
        BG     = t["BG"]
        PANEL  = t["PANEL"]
        BORDER = t["BORDER"]
        FG     = t["FG"]
        FG2    = t["FG2"]
        ENTRY  = t["ENTRY_BG"]
        test_window.configure(bg=BG)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = tk.Frame(test_window, bg=BG)
        hdr.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(hdr, text="Resume Match Simulator",
                 bg=BG, fg="#93c5fd", font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Label(hdr, text="Paste a job description to see which resume wins",
                 bg=BG, fg=FG2, font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))

        # ── Result panel (initially hidden content) ───────────────────────────
        result_frame = tk.Frame(test_window, bg=PANEL, bd=0,
                                highlightthickness=1, highlightbackground=BORDER)
        result_frame.pack(fill="x", padx=12, pady=4)

        # Winner row
        winner_row = tk.Frame(result_frame, bg=PANEL)
        winner_row.pack(fill="x", padx=10, pady=(8, 2))

        winner_icon = tk.Label(winner_row, text="⭐", bg=PANEL,
                               font=("Segoe UI", 16))
        winner_icon.pack(side="left", padx=(0, 8))

        winner_name_lbl = tk.Label(winner_row, text="[ Awaiting test... ]",
                                   bg=PANEL, fg="#4ade80",
                                   font=("Segoe UI", 12, "bold"), anchor="w")
        winner_name_lbl.pack(side="left", fill="x", expand=True)

        conf_pct_lbl = tk.Label(winner_row, text="",
                                bg=PANEL, fg="#fbbf24",
                                font=("Segoe UI", 13, "bold"))
        conf_pct_lbl.pack(side="right", padx=8)

        # Confidence bar
        conf_bar_frame = tk.Frame(result_frame, bg=PANEL)
        conf_bar_frame.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(conf_bar_frame, text="Confidence:", bg=PANEL, fg=FG2,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))
        conf_bar_bg = tk.Frame(conf_bar_frame, bg="#374151", height=10)
        conf_bar_bg.pack(side="left", fill="x", expand=True)
        conf_bar_fill = tk.Frame(conf_bar_bg, bg="#16a34a", height=10, width=0)
        conf_bar_fill.place(x=0, y=0, relheight=1.0)

        # Stat cards row
        stats_row = tk.Frame(result_frame, bg=PANEL)
        stats_row.pack(fill="x", padx=10, pady=4)

        def _mini_card(parent, label, val_text="—", bg="#1a2a4a", fg="#60a5fa"):
            f = tk.Frame(parent, bg=bg, bd=0, highlightthickness=1,
                         highlightbackground=BORDER)
            f.pack(side="left", padx=3, pady=2, ipadx=8, ipady=4)
            tk.Label(f, text=label, bg=bg, fg=FG2,
                     font=("Segoe UI", 7, "bold")).pack()
            v = tk.Label(f, text=val_text, bg=bg, fg=fg,
                         font=("Segoe UI", 11, "bold"))
            v.pack()
            return v

        cov_lbl    = _mini_card(stats_row, "COVERAGE",  bg="#1a2a2a", fg="#34d399")
        sem_lbl    = _mini_card(stats_row, "SEMANTIC",  bg="#1a1a3a", fg="#818cf8")
        groq_lbl   = _mini_card(stats_row, "GROQ AI",   bg="#2a1a3a", fg="#c084fc")
        uni_lbl    = _mini_card(stats_row, "UNIQUE KW", bg="#1a2a1a", fg="#4ade80")
        gen_lbl    = _mini_card(stats_row, "GEN KW",    bg="#2a2a1a", fg="#fbbf24")
        learn_lbl  = _mini_card(stats_row, "LEARNED",   bg="#2a1a1a", fg="#f87171")

        # Must-have warning banner (hidden by default)
        must_warn = tk.Label(result_frame, text="", bg="#7f1d1d", fg="#fca5a5",
                             font=("Segoe UI", 9, "bold"), anchor="w", justify="left")

        # Gap Check Banner (hidden by default)
        gap_warn_frame = tk.Frame(result_frame, bg="#312e81")
        gap_warn_lbl = tk.Label(gap_warn_frame, text="", bg="#312e81", fg="#c7d2fe",
                                font=("Segoe UI", 9, "bold"), anchor="w", justify="left")
        gap_warn_lbl.pack(side="left", padx=10, pady=4, fill="x", expand=True)
        gap_add_btn = ttk.Button(gap_warn_frame, text="+ Add All to Profile", style="Start.TButton")
        # gap_warn_frame is packed when gaps are found.

        # All profiles ranked table
        table_frame = tk.Frame(result_frame, bg=PANEL)
        table_frame.pack(fill="x", padx=10, pady=(4, 8))
        tk.Label(table_frame, text="All Profiles Ranked:", bg=PANEL, fg=FG2,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        # Scrollable treeview for ranked list
        tree_frame = tk.Frame(table_frame, bg=PANEL)
        tree_frame.pack(fill="x")
        ranked_tree = ttk.Treeview(
            tree_frame,
            columns=("rank", "profile", "score", "conf", "cov", "must"),
            show="headings", height=5
        )
        ranked_tree.heading("rank",    text="#")
        ranked_tree.heading("profile", text="Profile")
        ranked_tree.heading("score",   text="Score")
        ranked_tree.heading("conf",    text="Confidence")
        ranked_tree.heading("cov",     text="Coverage")
        ranked_tree.heading("must",    text="Must-Have")
        ranked_tree.column("rank",    width=30,  anchor="center")
        ranked_tree.column("profile", width=200, anchor="w")
        ranked_tree.column("score",   width=70,  anchor="center")
        ranked_tree.column("conf",    width=80,  anchor="center")
        ranked_tree.column("cov",     width=70,  anchor="center")
        ranked_tree.column("must",    width=90,  anchor="center")
        ranked_tree_sb = ttk.Scrollbar(tree_frame, orient="vertical",
                                       command=ranked_tree.yview)
        ranked_tree.configure(yscrollcommand=ranked_tree_sb.set)
        ranked_tree.pack(side="left", fill="x", expand=True)
        ranked_tree_sb.pack(side="right", fill="y")

        # ── result_label kept for backward compat but hidden ──────────────────
        result_label = tk.Label(test_window, text="", bg=BG)  # hidden placeholder

        def run_test():
            import re
            try:
                # Clear highlights and ranked table
                job_desc_text.tag_remove("match_unique",   "1.0", tk.END)
                job_desc_text.tag_remove("match_general",  "1.0", tk.END)
                job_desc_text.tag_config("match_unique",
                    background="#16a34a", foreground="white",
                    font=("Consolas", 9, "bold"))
                job_desc_text.tag_config("match_general",
                    background="#d97706", foreground="white")
                for row in ranked_tree.get_children():
                    ranked_tree.delete(row)
                must_warn.pack_forget()
                gap_warn_frame.pack_forget()

                text = job_desc_text.get("1.0", tk.END).lower()
                if not text.strip():
                    winner_name_lbl.config(text="Please paste some job description text.",
                                           fg="#f87171")
                    return

                profiles = getattr(self, "resume_profiles", [])
                if not profiles:
                    winner_name_lbl.config(text="No profiles configured in settings.",
                                           fg="#f87171")
                    return

                from core.matcher import ResumeMatcher
                matcher = ResumeMatcher(
                    profiles,
                    semantic_matcher=self.semantic_matcher if self.semantic_enabled else None,
                    learning_engine=self.learning_engine,
                    groq_scorer=getattr(self, 'groq_scorer', None)
                )
                ranked_results = matcher.score_profiles(text)

                if not ranked_results:
                    winner_name_lbl.config(text="No keywords matched any profile.",
                                           fg="#f87171")
                    return

                best_match   = ranked_results[0]
                profile_name = best_match['name']
                tot          = best_match['score']
                u_sc         = best_match.get('uni_score', 0)
                g_sc         = best_match.get('gen_score', 0)
                sem_sc       = best_match.get('semantic_score', 0)
                learn_sc     = best_match.get('learning_boost', 0)
                conf_pct     = best_match.get('confidence_pct', 0.0)
                groq_sc      = best_match.get('groq_score', 0)
                coverage     = best_match.get('coverage', 0.0)
                missing      = best_match.get('missing_must_have', [])
                groq_rec     = best_match.get('groq_recommendation', '')

                # ── Update winner panel ─────────────────────────────────────────────
                winner_name_lbl.config(
                    text=f"⭐  {profile_name}   (Score: {tot})",
                    fg="#4ade80"
                )
                conf_pct_lbl.config(text=f"{conf_pct}% match")

                # Confidence bar fill
                conf_bar_bg.update_idletasks()
                bar_w = conf_bar_bg.winfo_width()
                fill_w = max(4, int(bar_w * conf_pct / 100))
                bar_color = (
                    "#16a34a" if conf_pct >= 70 else
                    "#d97706" if conf_pct >= 40 else
                    "#dc2626"
                )
                conf_bar_fill.config(bg=bar_color, width=fill_w)
                conf_bar_fill.place(x=0, y=0, relheight=1.0, width=fill_w)

                # Stat cards
                cov_lbl.config(text=f"{coverage}%")
                sem_lbl.config(text=f"{sem_sc}%" if sem_sc else "—")
                groq_lbl.config(text=str(groq_sc) if groq_sc else "—")
                uni_lbl.config(text=str(round(u_sc, 1)))
                gen_lbl.config(text=str(round(g_sc, 1)))
                learn_lbl.config(text=f"+{learn_sc}" if learn_sc else "—")

                # Must-have warning
                if missing:
                    must_warn.config(
                        text=f"  ⚠  Must-have skills MISSING from winner:  {', '.join(missing)}"
                    )
                    must_warn.pack(fill="x", padx=10, pady=(2, 4))

                # Groq recommendation
                if groq_rec:
                    winner_name_lbl.config(
                        text=f"⭐  {profile_name}   (Score: {tot})\n    ↳ Groq: {groq_rec}"
                    )

                # ── Populate ranked table ─────────────────────────────────────────────
                for i, r in enumerate(ranked_results):
                    miss = r.get('missing_must_have', [])
                    must_status = "OK" if not miss else f"Missing {len(miss)}"
                    row_tag = "winner" if i == 0 else ("disqualified" if r.get('must_have_penalty', 0) < 0 else "")
                    ranked_tree.insert(
                        "", "end",
                        values=(
                            i + 1,
                            r['name'],
                            r['score'],
                            f"{r.get('confidence_pct', 0)}%",
                            f"{r.get('coverage', 0)}%",
                            must_status
                        ),
                        tags=(row_tag,)
                    )

                ranked_tree.tag_configure("winner",       background="#14532d", foreground="#4ade80")
                ranked_tree.tag_configure("disqualified", background="#450a0a", foreground="#f87171")

                # ── Keyword highlights ────────────────────────────────────────────────────
                best_used_uni = best_match['matched_uni']
                best_used_gen = best_match['matched_gen']

                for word in best_used_uni:
                    pattern = matcher.build_keyword_pattern(word)
                    if pattern:
                        for match in re.finditer(pattern, text):
                            job_desc_text.tag_add("match_unique",
                                                  f"1.0+{match.start()}c",
                                                  f"1.0+{match.end()}c")

                for word in best_used_gen:
                    pattern = matcher.build_keyword_pattern(word)
                    if pattern:
                        for match in re.finditer(pattern, text):
                            job_desc_text.tag_add("match_general",
                                                  f"1.0+{match.start()}c",
                                                  f"1.0+{match.end()}c")

                # ── Run lightweight Gap Check ───────────────────────────────────────
                import threading
                def _run_gap_check():
                    try:
                        from core.resume_keyword_scanner import ResumeKeywordScanner
                        scanner = ResumeKeywordScanner(getattr(self, 'groq_scorer', None))
                        res_kws = scanner.scan_resume(best_match['file_path'])
                        jd_kws = scanner.extract_jd_keywords(text)
                        
                        prof_dict = next((p for p in profiles if p.get('name') == profile_name), None)
                        if prof_dict:
                            gaps = scanner.find_gaps(res_kws, jd_kws, prof_dict)
                            self.root.after(0, lambda: _show_test_gaps(gaps, prof_dict))
                    except Exception as e:
                        print(f"Gap check failed: {e}")
                        
                def _show_test_gaps(gaps, prof_dict):
                    all_gaps = gaps["unique"] + gaps["general"]
                    if not all_gaps: return
                    
                    gap_warn_lbl.config(
                        text=f"💡 JD has {len(all_gaps)} keywords your resume contains but profile is missing: {', '.join(all_gaps)}"
                    )
                    
                    def _add_test_gaps():
                        if "unique_keywords" not in prof_dict: prof_dict["unique_keywords"] = []
                        if "keywords" not in prof_dict: prof_dict["keywords"] = []
                        prof_dict["unique_keywords"].extend(gaps["unique"])
                        prof_dict["keywords"].extend(gaps["general"])
                        self.save_config()
                        gap_warn_frame.pack_forget()
                        run_test() # re-run to show improved score
                        
                    gap_add_btn.config(command=_add_test_gaps)
                    gap_add_btn.pack(side="right", padx=10, pady=4)
                    gap_warn_frame.pack(fill="x", padx=10, pady=(2, 4), before=table_frame)

                threading.Thread(target=_run_gap_check, daemon=True).start()

            except Exception as e:
                winner_name_lbl.config(text=f"Error: {str(e)}", fg="#f87171")
                print(f"Test Simulator Error: {e}")

            test_window.update_idletasks()

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = tk.Frame(test_window, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=4)
        ttk.Button(btn_row, text="Run Match Test",
                   command=run_test, style="Start.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Clear",
                   command=lambda: [
                       job_desc_text.delete("1.0", tk.END),
                       winner_name_lbl.config(text="[ Awaiting test... ]", fg="#4ade80"),
                       conf_pct_lbl.config(text=""),
                       conf_bar_fill.place(x=0, y=0, relheight=1.0, width=0),
                       [cov_lbl.config(text="—"), sem_lbl.config(text="—"),
                        groq_lbl.config(text="—"), uni_lbl.config(text="—"),
                        gen_lbl.config(text="—"), learn_lbl.config(text="—")],
                       [ranked_tree.delete(r) for r in ranked_tree.get_children()],
                       must_warn.pack_forget(),
                       gap_warn_frame.pack_forget()
                   ],
                   style="Stop.TButton").pack(side="left")
        tk.Label(btn_row,
                 text="Green = Unique KW  |  Orange = General KW  |  Red row = Disqualified (must_have)",
                 bg=BG, fg=FG2, font=("Segoe UI", 8)).pack(side="right", padx=8)

        # ── Job description input area ────────────────────────────────────────
        tk.Label(test_window, text="Job Description:",
                 bg=BG, fg=FG2, font=("Segoe UI", 9)).pack(padx=12, anchor="w")
        job_desc_text = tk.Text(
            test_window, height=12, wrap=tk.WORD,
            bg=ENTRY, fg=FG, insertbackground=FG,
            font=("Consolas", 9), relief="flat",
            highlightthickness=1, highlightbackground=BORDER
        )
        job_desc_text.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        job_desc_text.focus_set()
        
    def setup_settings_tab(self):
        """Set up the settings tab UI — wrapped in a scrollable canvas so nothing gets clipped."""
        # ── Scrollable container ─────────────────────────────────────────────
        canvas = tk.Canvas(self.settings_tab, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.settings_tab, orient="vertical", command=canvas.yview)
        self.settings_scroll_frame = ttk.Frame(canvas)

        self.settings_scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.settings_scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Allow mouse-wheel scrolling on Windows
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        parent = self.settings_scroll_frame
        
        top_frame = ttk.LabelFrame(parent, text="App Configuration")
        top_frame.pack(fill="x", padx=15, pady=10)
        self.headless_var = tk.BooleanVar(value=getattr(self, 'headless_mode', False))
        ck_headless = LabeledToggleSwitch(
            top_frame,
            text="Headless Mode",
            variable=self.headless_var,
            command=self.save_config,
            bg=self._theme["PANEL"], fg=self._theme["FG"]
        )
        ck_headless.pack(side="left", padx=10, pady=8)

        self.batch_save_var = tk.BooleanVar(value=getattr(self, 'batch_excel_saves', True))
        ck_batch = LabeledToggleSwitch(
            top_frame,
            text="Batch Excel Saves",
            variable=self.batch_save_var,
            command=self.save_config,
            bg=self._theme["PANEL"], fg=self._theme["FG"]
        )
        ck_batch.pack(side="left", padx=10, pady=8)

        # ── Group 1: Dice.com Login ──────────────────────────────────────────
        login_frame = ttk.LabelFrame(parent, text="Dice Account Credentials")
        login_frame.pack(fill="x", padx=15, pady=10)
        login_frame.columnconfigure(1, weight=1)

        ttk.Label(login_frame, text="Username:").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.username_entry = ttk.Entry(login_frame)
        self.username_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=8)

        ttk.Label(login_frame, text="Password:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        self.password_entry = ttk.Entry(login_frame, show="*")
        self.password_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=8)

        self.test_login_button = ttk.Button(login_frame, text="✔ Test Login Connection", command=self.test_login)
        self.test_login_button.grid(row=2, column=0, columnspan=2, pady=15)
        # ── Group 2: Automation Controls ─────────────────────────────────────
        settings_frame = ttk.LabelFrame(parent, text="Automation Behavior")
        settings_frame.pack(fill="x", padx=15, pady=10)
        settings_frame.columnconfigure(1, weight=1)

        # Job limit
        ttk.Label(settings_frame, text="Stop at Job Limit:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        self.job_limit_var = tk.IntVar(value=self.job_limit)
        ttk.Spinbox(
            settings_frame, from_=1, to=10000, width=8, textvariable=self.job_limit_var
        ).grid(row=1, column=1, sticky="w", padx=10, pady=8)
        
        # Semantic AI Toggle
        self.semantic_var = tk.BooleanVar(value=self.semantic_enabled)
        LabeledToggleSwitch(
            settings_frame,
            text="Enable AI Semantic Matching (Understanding meanings/concepts)",
            variable=self.semantic_var,
            bg=self._theme["PANEL"], fg=self._theme["FG"]
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=8)

        # AI Memory Reset
        def _reset_ai():
            if messagebox.askyesno("Reset AI Memory", "This will delete all learned context from your past applications. Proceed?"):
                self.learning_engine.reset_memory()
                messagebox.showinfo("Success", "AI Memory has been wiped.")
                
        ttk.Button(settings_frame, text="🗑 Reset AI Memory", command=_reset_ai).grid(row=3, column=0, sticky="w", padx=10, pady=8)

        # Global Profile Name Boost (Used as a fallback/multiplier)
        ttk.Label(settings_frame, text="Global Name Boost:").grid(row=3, column=0, columnspan=2, sticky="e", padx=(0, 150), pady=8)
        self.name_boost_var = tk.StringVar(value=f"{self.profile_name_boost_mode.upper()} | Default")
        ttk.Combobox(
            settings_frame, 
            textvariable=self.name_boost_var,
            values=["OFF | Keywords Only", "LOW | Nudge Match", "HIGH | Aggressive", "EXACT | Strict Title Match"],
            state="readonly",
            width=20
        ).grid(row=3, column=1, sticky="e", padx=10, pady=8)

        # Help text for Boost (now in Resumes)
        ttk.Label(
            settings_frame,
            text="💡 Tip: Semantic matching is 'Smart'. It knows that 'Data Engineer' is 80% similar\nto 'ETL Engineer' even if keywords don't match exactly.",
            foreground="#666", font=("Segoe UI", 8, "italic")
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 10))

        # ── Group: Application Wizard Answers ───────────────────────────────
        answers_frame = ttk.LabelFrame(parent, text="Application Wizard Answers")
        answers_frame.pack(fill="x", padx=15, pady=10)

        ttk.Label(
            answers_frame,
            text=(
                "All questions the bot will auto-fill during Easy Apply are listed below.\n"
                "Gray = built-in default  |  Green = your custom override  |  "
                "Click any row to edit it.  Pattern matches any question label containing that text."
            ),
            foreground="#888", font=("Segoe UI", 8, "italic")
        ).pack(padx=10, pady=(6, 2), anchor="w")

        # ── Built-in defaults (mirrors main_script.py default_answers) ──────
        _BUILTIN_DEFAULTS = {
            "work authorization status":
                "I am authorized to work in the United States and do not require visa sponsorship now or in the future.",
            "work authorization":
                "I am authorized to work in the United States without sponsorship.",
            "authorized to work":       "Yes",
            "legally authorized":       "Yes",
            "us work authorization":    "Yes",
            "employment eligibility":
                "I am authorized to work in the United States without sponsorship.",
            "sponsorship now or later":
                "N/A - I am a U.S. authorized worker and do not require visa sponsorship.",
            "need visa sponsorship":
                "N/A - I am authorized to work in the US without sponsorship.",
            "visa sponsorship":         "No",
            "require sponsorship":      "No",
            "sponsorship":              "No",
            "relocate at own expense":
                "N/A - I am open to remote work and available immediately.",
            "willing to relocate":      "No",
            "relocation":               "No",
            "qualifications that make you":
                "I bring 8+ years of hands-on data engineering experience with strong expertise in cloud platforms (Azure, AWS), modern data stack tools (Databricks, dbt, Spark, Kafka), and end-to-end pipeline development.",
            "suitable candidate":
                "I have deep expertise in data engineering, cloud infrastructure, and modern ETL/ELT tooling. My background includes designing and delivering production-grade pipelines on Azure and Databricks.",
            "tell us about yourself":
                "I am a data engineer with 8+ years of experience building scalable data pipelines, cloud data platforms, and analytics infrastructure.",
            "why are you a good fit":
                "My skills in data engineering, cloud platforms, and modern tooling align directly with this role.",
            "describe your experience":
                "I have 8+ years of experience in data engineering, including cloud data warehouse design, ETL/ELT pipeline development, and real-time streaming with Kafka.",
            "cover letter":
                "I am excited about this opportunity and confident my background in data engineering makes me an excellent fit.",
            "years of relevant experience":  "8",
            "years of experience":           "8",
            "how many years":                "8",
            "total years":                   "8",
            "salary expectation":  "Open",
            "expected salary":     "Open",
            "rate expectation":    "Open",
            "desired salary":      "Open",
            "compensation":        "Open",
            "work type":           "Contract",
            "employment type":     "Contract",
            "job type":            "Contract",
            "remote":              "Yes",
            "work from home":      "Yes",
            "background check":    "Yes",
            "drug test":           "Yes",
            "overtime":            "Yes",
            "start date":          "Immediate",
            "notice period":       "Immediate",
            "currently employed":  "No",
        }

        # Treeview — 3 columns: pattern, answer (truncated), source
        ans_tree_frame = ttk.Frame(answers_frame)
        ans_tree_frame.pack(fill="x", padx=10, pady=4)

        self._answers_tree = ttk.Treeview(
            ans_tree_frame,
            columns=("pattern", "answer", "source"),
            show="headings",
            height=16,
            selectmode="browse"
        )
        self._answers_tree.heading("pattern", text="Question Pattern  (label contains...)")
        self._answers_tree.heading("answer",  text="Answer  (click row to edit)")
        self._answers_tree.heading("source",  text="Source")
        self._answers_tree.column("pattern", width=280, stretch=True)
        self._answers_tree.column("answer",  width=260, stretch=True)
        self._answers_tree.column("source",  width=80,  stretch=False)
        self._answers_tree.pack(side="left", fill="x", expand=True)

        ans_sb = ttk.Scrollbar(ans_tree_frame, orient="vertical", command=self._answers_tree.yview)
        self._answers_tree.configure(yscrollcommand=ans_sb.set)
        ans_sb.pack(side="right", fill="y")

        # Tag colours
        self._answers_tree.tag_configure("builtin", foreground="#888888")
        self._answers_tree.tag_configure("custom",  foreground="#4ade80")

        def _refresh_answers_tree():
            self._answers_tree.delete(*self._answers_tree.get_children())
            user_answers = getattr(self, 'application_answers', {})
            # Merge: defaults first, then user overrides
            merged = dict(_BUILTIN_DEFAULTS)
            merged.update(user_answers)
            for k, v in sorted(merged.items()):
                is_custom = k in user_answers
                tag = "custom" if is_custom else "builtin"
                source = "Custom" if is_custom else "Default"
                display_v = (v[:60] + "…") if len(v) > 60 else v
                self._answers_tree.insert("", "end", values=(k, display_v, source), tags=(tag,))

        _refresh_answers_tree()

        # ── Edit area ─────────────────────────────────────────────────────────
        edit_outer = ttk.Frame(answers_frame)
        edit_outer.pack(fill="x", padx=10, pady=(6, 2))

        # Left: pattern entry
        left_col = ttk.Frame(edit_outer)
        left_col.pack(side="left", fill="y", padx=(0, 10))
        ttk.Label(left_col, text="Question Pattern:").pack(anchor="w")
        self._ans_pattern_entry = ttk.Entry(left_col, width=32)
        self._ans_pattern_entry.pack(anchor="w", pady=(2, 0))
        ttk.Label(left_col, text="(text that appears in the question label)",
                  font=("Segoe UI", 7, "italic"), foreground="#666").pack(anchor="w")

        # Right: answer text box (multi-line for long answers)
        right_col = ttk.Frame(edit_outer)
        right_col.pack(side="left", fill="both", expand=True)
        ttk.Label(right_col, text="Answer:").pack(anchor="w")
        self._ans_answer_text = tk.Text(
            right_col, height=4, wrap=tk.WORD,
            font=("Segoe UI", 9),
            relief="flat", highlightthickness=1,
            highlightbackground=self._theme.get("BORDER", "#333")
        )
        self._ans_answer_text.pack(fill="both", expand=True, pady=(2, 0))

        def _on_ans_select(event):
            sel = self._answers_tree.selection()
            if not sel:
                return
            pat = self._answers_tree.item(sel[0])["values"][0]
            # Look up the FULL answer (treeview truncates it)
            user_answers = getattr(self, 'application_answers', {})
            full_ans = user_answers.get(pat) or _BUILTIN_DEFAULTS.get(pat, "")
            self._ans_pattern_entry.delete(0, tk.END)
            self._ans_pattern_entry.insert(0, pat)
            self._ans_answer_text.delete("1.0", tk.END)
            self._ans_answer_text.insert("1.0", full_ans)

        self._answers_tree.bind("<<TreeviewSelect>>", _on_ans_select)

        def _save_answer():
            pat = self._ans_pattern_entry.get().strip().lower()
            ans = self._ans_answer_text.get("1.0", tk.END).strip()
            if not pat or not ans:
                messagebox.showwarning("Missing Fields", "Enter both a question pattern and an answer.")
                return
            if not hasattr(self, 'application_answers') or not isinstance(self.application_answers, dict):
                self.application_answers = {}
            self.application_answers[pat] = ans
            self.save_config(silent=True)
            _refresh_answers_tree()
            self._ans_pattern_entry.delete(0, tk.END)
            self._ans_answer_text.delete("1.0", tk.END)

        def _reset_to_default():
            """Remove custom override — reverts row to built-in default."""
            sel = self._answers_tree.selection()
            if not sel:
                return
            pat = self._answers_tree.item(sel[0])["values"][0]
            answers = getattr(self, 'application_answers', {})
            if pat not in answers:
                messagebox.showinfo("Already Default", "This row is already using the built-in default.")
                return
            answers.pop(pat, None)
            self.application_answers = answers
            self.save_config(silent=True)
            _refresh_answers_tree()
            self._ans_pattern_entry.delete(0, tk.END)
            self._ans_answer_text.delete("1.0", tk.END)

        def _add_new_answer():
            """Add a completely new pattern not in defaults."""
            pat = self._ans_pattern_entry.get().strip().lower()
            ans = self._ans_answer_text.get("1.0", tk.END).strip()
            if not pat or not ans:
                messagebox.showwarning("Missing Fields", "Enter a question pattern and an answer.")
                return
            if not hasattr(self, 'application_answers') or not isinstance(self.application_answers, dict):
                self.application_answers = {}
            self.application_answers[pat] = ans
            self.save_config(silent=True)
            _refresh_answers_tree()
            self._ans_pattern_entry.delete(0, tk.END)
            self._ans_answer_text.delete("1.0", tk.END)

        btn_row_ans = ttk.Frame(answers_frame)
        btn_row_ans.pack(fill="x", padx=10, pady=(4, 8))
        ttk.Button(btn_row_ans, text="💾 Save / Override", command=_save_answer,
                   style="Start.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(btn_row_ans, text="↩ Reset to Default", command=_reset_to_default).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row_ans, text="+ Add New Pattern", command=_add_new_answer).pack(side="left")

        # Save button
        self.save_settings_button = ttk.Button(
            parent, text="💾 Save All Settings", command=self.save_config, style="Start.TButton"
        )
        self.save_settings_button.pack(pady=20)

        # ── Group 3: User Guide ──────────────────────────────────────────────
        guide_frame = ttk.LabelFrame(parent, text="Getting Started & Best Practices")
        guide_frame.pack(fill="x", padx=15, pady=10)

        self.guide_text = scrolledtext.ScrolledText(guide_frame, wrap=tk.WORD, height=14, font=("Segoe UI", 9))
        self.guide_text.pack(fill="x", padx=10, pady=10)

        guide_content = """\
Dice Auto-Apply Bot Guide
--------------------------
1. DICE LOGIN: 
   Enter your credentials in this tab and click 'Test Login'. 
   This verifies the bot can access your account correctly.

2. PROFILE SETUP (Resumes Tab):
   Add multiple resume profiles. 
   Set 'Boost Mode' for each profile:
   - EXACT: If name matches job title >= 60%, auto-select this resume.
   - HIGH/LOW: Tie-breaker nudges for similar keyword scores.
   - OFF: Use keyword scoring only.

3. JOB SEARCH (Run Bot Tab):
   - 'Job Titles': Enter what you are looking for (e.g. Data Engineer).
   - 'Exclude': Hard exclude (e.g. 'Senior' if you want Junior roles).
   - 'Include': Must-have keywords (e.g. 'Python, AWS').

4. FILTERS:
   The bot automatically skips roles containing W2 or Full-Time patterns 
   unless a C2C override is found.

5. MONITORING:
   Watch the 'Live Log' on the main tab. Completed applications appear 
   in 'applied_jobs.xlsx' automatically.
"""
        self.guide_text.insert("1.0", guide_content)
        self.guide_text.config(state="disabled")

        # ── Pre-fill login from .env ─────────────────────────────────────────
        from dotenv import load_dotenv
        load_dotenv()
        import os
        username = os.getenv("DICE_USERNAME", "")
        password = os.getenv("DICE_PASSWORD", "")
        if username:
            self.username_entry.insert(0, username)
        if password:
            self.password_entry.insert(0, password)


    def setup_logs_tab(self):
        """Set up the logs tab UI with a professional dark theme."""
        # Top toolbar
        toolbar = ttk.Frame(self.logs_tab)
        toolbar.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Button(toolbar, text="🔄 Refresh Latest Log", command=self.load_log_file).pack(side="left", padx=5)
        ttk.Button(toolbar, text="🗑 Clear View", command=self.clear_logs_view).pack(side="left", padx=5)
        ttk.Button(toolbar, text="📁 Open Log Folder", command=self.open_log_folder).pack(side="right", padx=5)

        # Full log view
        log_frame = ttk.LabelFrame(self.logs_tab, text="Complete Session Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.full_log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Consolas", 9),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white"
        )
        self.full_log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.full_log_text.config(state="disabled")

        # Auto-load on setup
        self.root.after(500, self.load_log_file)
        
    def load_log_file(self):
        """Load and display the latest log file"""
        logs_dir = os.path.join(os.path.dirname(__file__), "logs")
        if not os.path.exists(logs_dir):
            return

        # Find all log files
        log_files = [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.startswith("app_")]
        if not log_files:
            return

        # Get the most recent log file
        latest_log = max(log_files, key=os.path.getmtime)
        try:
            with open(latest_log, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            self.full_log_text.config(state="normal")
            self.full_log_text.delete("1.0", tk.END)
            self.full_log_text.insert("1.0", content)
            self.full_log_text.see(tk.END) # Scroll to bottom
            self.full_log_text.config(state="disabled")
            self.logger.info(f"UI: Loaded log file: {os.path.basename(latest_log)}")
        except Exception as e:
            self.logger.error(f"UI Error loading log file: {e}")

    def clear_logs_view(self):
        """Clears the displayed log text"""
        self.full_log_text.config(state="normal")
        self.full_log_text.delete("1.0", tk.END)
        self.full_log_text.config(state="disabled")

    def _log_match_details(self, profile_name, match_reason):
        """Intelligently formats and logs the resume match metrics and keywords to the log widget."""
        if not profile_name or profile_name == "Default/None":
            self.logger.info("Using default resume (No keywords matched or no matching profiles).")
            return
            
        if match_reason and " | " in match_reason:
            try:
                # Real format from core/main_script.py:
                #   "Score: X (Words: Y) | Matching: [kw1, kw2] | AI: Z% | Name: W%"
                parts = match_reason.split(" | ")
                score_conf_str = parts[0]  # "Score: X (Words: Y)"

                # Extract keywords from the "Matching: [...]" segment
                kw_str = ""
                for p in parts:
                    p = p.strip()
                    if p.startswith("Matching:"):
                        b_start = p.find("[")
                        b_end   = p.rfind("]")
                        if b_start != -1 and b_end != -1 and b_end > b_start:
                            kw_str = p[b_start + 1:b_end]
                        break

                # Log resume name with score/word-count summary
                self.logger.info(f"Matched Resume: '{profile_name}' ({score_conf_str})")

                # Format keywords nicely if they exist
                if kw_str:
                    try:
                        from core.outreach.skill_ranker import _fmt_skill
                        formatted_kws = ", ".join(_fmt_skill(s.strip()) for s in kw_str.split(",") if s.strip())
                        self.logger.info(f"Matched Skills: {formatted_kws}")
                    except Exception:
                        self.logger.info(f"Matched Skills: {kw_str}")
                else:
                    self.logger.info("Matched Skills: None (Name Affinity / exact match boost only)")
                return
            except Exception:
                pass
        
        # Fallback to raw string if parsing fails
        self.logger.info(f"Using Resume: {profile_name} | {match_reason}")

    def open_log_folder(self):
        """Open the logs directory in explorer"""
        logs_dir = os.path.join(os.path.dirname(__file__), "logs")
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
        
        if sys.platform == "win32":
            os.startfile(logs_dir)
        elif sys.platform == "darwin":
            subprocess.run(["open", logs_dir])
        else:
            subprocess.run(["xdg-open", logs_dir])
            
    def test_login(self):
        """Test Dice login credentials"""
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        
        if not username or not password:
            messagebox.showwarning("Missing Credentials", "Please enter both username and password.")
            return
            
        # Disable button during testing
        self.test_login_button.config(state="disabled", text="Testing...")
        self.root.update_idletasks()
        
        def test_login_thread():
            try:
                # Import the validation function
                
                success = validate_dice_credentials(username, password)
                
                # Update UI from the main thread
                self.root.after(0, lambda: self.test_login_complete(success))
                
            except Exception as e:
                self.logger.error(f"Login test error: {str(e)}")
                # Update UI from the main thread
                self.root.after(0, lambda err_msg=str(e): self.test_login_complete(False, err_msg))
                
        # Run the test in a separate thread
        threading.Thread(target=test_login_thread, daemon=True).start()
        
    def test_login_complete(self, success, error_msg=None):
        """Handle login test completion"""
        # Re-enable the button
        self.test_login_button.config(state="normal", text="Test Login")
        
        if success:
            self.logger.info("Login test successful")
            messagebox.showinfo("Login Test", "Login successful!")
        else:
            error = error_msg if error_msg else "Login failed. Please check your credentials."
            self.logger.error(f"Login test failed: {error}")
            messagebox.showerror("Login Test", error)
            
    def start_applying(self):
        """Start the job application process"""
        # Validate inputs
        search_queries = [q.strip() for q in self.search_query_entry.get().split(",") if q.strip()]
        if not search_queries:
            messagebox.showwarning("Missing Input", "Please enter at least one job title to search for.")
            return
            
        # Check for login credentials
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        if not username or not password:
            messagebox.showwarning("Missing Credentials", "Please enter Dice login credentials in the Settings tab.")
            self.notebook.select(1)  # Switch to settings tab
            return
            
        # Get keywords
        exclude_keywords = [k.strip() for k in self.exclude_keywords_entry.get().split(",") if k.strip()]
        include_keywords = [k.strip() for k in self.include_keywords_entry.get().split(",") if k.strip()]
        job_limit = self.job_limit_var.get()
        profile_boost_mode = self.name_boost_var.get().split('|')[0].strip().lower()
        emp_type = self.emp_type_var.get()
        posted_date = self.posted_date_var.get()
        work_setting = self.work_setting_var.get()
        easy_apply = self.easy_apply_var.get()
        location = self.location_entry.get().strip()
        radius = self.radius_var.get()
        will_sponsor = self.will_sponsor_var.get()
        salary_min = self.salary_min_entry.get().strip()

        # Update UI
        self.running = True
        self.is_paused = False
        self.skip_requested = False
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.pause_button.config(state="normal", text="⏸  Pause")
        self.skip_button.config(state="disabled")
        self.progress_bar["value"] = 0
        self.status_label.config(text="Starting...")
        
        # Reset counters
        self.jobs_found_label.config(text="0")
        self.jobs_applied_label.config(text="0")
        self.jobs_failed_label.config(text="0")
        
        # Clear log text
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")
        
        # Run job application process in a separate thread
        self.job_thread = threading.Thread(
            target=self.run_job_application,
            args=(
                search_queries, include_keywords, exclude_keywords, username, password, job_limit, profile_boost_mode,
                emp_type, posted_date, work_setting, easy_apply, location, radius, will_sponsor, salary_min
            ),
            daemon=True
        )
        self.job_thread.start()
        
    def toggle_pause(self):
        """Toggles the pause state of the application loop"""
        if not self.running:
            return
            
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_button.config(text="▶  Resume")
            self.skip_button.config(state="disabled")
            self.update_status("⏸  Application PAUSED. Bot is waiting...")
        else:
            self.pause_button.config(text="⏸  Pause")
            self.skip_button.config(state="normal")
            self.update_status("▶  Application RESUMED.")

    def check_pause(self):
        """
        Blocks while paused.
        Returns 'stop' if the bot was stopped, 'skip' if the user clicked Skip,
        or False if execution should continue normally.
        Callers can do `if check_pause():` as before (truthy for stop/skip),
        but can also distinguish the two cases with == comparison.
        """
        while self.is_paused and self.running and not self.skip_requested:
            time.sleep(0.5)
        if not self.running:
            return "stop"
        if self.skip_requested:
            return "skip"
        return False

    def run_job_application(self, search_queries, include_keywords, exclude_keywords, username, password, job_limit, profile_boost_mode, emp_type, posted_date, work_setting, easy_apply=False, location="", radius="30", will_sponsor=False, salary_min=""):
        """Run the job application process in a background thread"""
        try:
            # Record start time
            start_time = time.time()
            self.logger.info(f"Starting job applications with queries: {search_queries}")
            
            # Initialize web driver
            self.update_status("Initializing web driver...")
            headless = self.headless_var.get()
            driver = get_web_driver(headless=headless)
            self.driver = driver
            
            # Login to Dice
            self.update_status("Logging in to Dice...")
            login_success = login_to_dice(driver, (username, password))
            if not login_success:
                self.update_status("Login failed. Please check your credentials.")
                self.root.after(0, lambda: messagebox.showerror(
                    "Login Failed", 
                    "Could not log in to Dice. Please check your credentials."
                ))
                return
                    
            self.update_status("Login successful. Fetching jobs...")
            
            # Find jobs matching the search queries
            import random
            all_jobs = {}
            excluded_jobs = []  # Track excluded jobs
            
            random.shuffle(search_queries)
            total_queries = len(search_queries)
            
            for i, query in enumerate(search_queries):
                # Check if bot is paused
                while self.is_paused and self.running:
                    time.sleep(1)
                if not self.running:
                    self.update_status("Stopped by user.")
                    return
                    
                self.update_status(f"Searching for '{query}' ({i+1}/{total_queries})...")
                
                # Use the fetch_jobs_with_requests function
                jobs, excluded = fetch_jobs_with_requests(
                    driver, query, include_keywords, exclude_keywords, pause_check=self.check_pause,
                    emp_type=emp_type, posted_date=posted_date, work_setting=work_setting,
                    easy_apply=easy_apply, location=location, radius=radius,
                    will_sponsor=will_sponsor, salary_min=salary_min
                )
                
                # Track counts before adding new jobs
                jobs_before = len(all_jobs)
                
                # Add unique jobs to dictionary
                for job in jobs:
                    if job["Job URL"] not in all_jobs:
                        all_jobs[job["Job URL"]] = job
                
                # Add excluded jobs
                excluded_jobs.extend(excluded)
                
                # Calculate current count
                current_count = len(all_jobs)
                
                # Update the counter after each query, capturing the current count
                count_to_display = current_count
                self.root.after(0, lambda c=count_to_display: self.jobs_found_label.config(text=str(c)))
                
                # Print debug info
                self.logger.info(f"Query '{query}': Found {len(jobs)} total jobs, added {current_count - jobs_before} unique jobs")
                print(f"Query '{query}': Found {len(jobs)} total jobs, added {current_count - jobs_before} unique jobs")
                
                # Move mouse to prevent sleeping
                pyautogui.moveRel(1, 1, duration=0.1)
                pyautogui.moveRel(-1, -1, duration=0.1)
                        
            # Make sure the final count is displayed
            final_count = len(all_jobs)
            self.update_status(f"Found {final_count} unique jobs matching criteria")
            self.root.after(0, lambda c=final_count: self.jobs_found_label.config(text=str(c)))
            
            # Save excluded jobs to Excel
            if excluded_jobs:
                try:
                    excluded_file = "excluded_jobs.xlsx"
                    df_excluded = pd.DataFrame(excluded_jobs)
                    df_excluded.to_excel(excluded_file, index=False)
                    self.logger.info(f"Saved {len(excluded_jobs)} excluded jobs to {excluded_file}")
                except Exception as e:
                    self.logger.error(f"Error saving excluded jobs: {e}")
            
            # Check for already applied jobs
            self.update_status("Checking for already applied jobs...")
            applied_jobs_file = "applied_jobs.xlsx"
            # Normalize to lowercase + strip trailing slash for reliable dedup (Bug 6)
            already_applied: set = set()

            if os.path.exists(applied_jobs_file):
                try:
                    df_applied = pd.read_excel(applied_jobs_file)
                    already_applied = set(
                        url.lower().rstrip('/') for url in df_applied["Job URL"].dropna()
                    )
                    self.update_status(f"Found {len(already_applied)} previously applied jobs to skip")
                except Exception as e:
                    self.logger.error(f"Error reading applied jobs file: {e}")

            # Bug 7: Also skip terminal (non-retryable) failures from previous runs.
            # Transient failures (button timeout, wizard errors) are NOT filtered — those
            # are legitimately worth retrying in the next run. Only permanent terminal
            # outcomes are excluded: already applied, user-skipped, already-retried-once.
            _not_applied_file = "not_applied_jobs.xlsx"
            _TERMINAL_SIGNALS = ("already applied", "skipped by user", "[retry]")
            if os.path.exists(_not_applied_file):
                try:
                    df_not = pd.read_excel(_not_applied_file)
                    if "Job URL" in df_not.columns and "Skip Reason" in df_not.columns:
                        _terminal_mask = df_not["Skip Reason"].astype(str).str.lower().apply(
                            lambda r: any(sig in r for sig in _TERMINAL_SIGNALS)
                        )
                        _terminal_urls = set(
                            url.lower().rstrip('/')
                            for url in df_not.loc[_terminal_mask, "Job URL"].dropna()
                        )
                        already_applied.update(_terminal_urls)
                        if _terminal_urls:
                            self.logger.info(
                                f"Excluding {len(_terminal_urls)} terminally-failed job(s) "
                                f"from previous run(s) (already-applied / user-skipped / retried)."
                            )
                except Exception as _nae:
                    self.logger.error(f"Error reading not_applied_jobs file: {_nae}")

            # Filter out already applied / terminally-failed jobs (case-insensitive URL match)
            jobs_to_apply = [
                job for job in all_jobs.values()
                if job["Job URL"].lower().rstrip('/') not in already_applied
            ]
            self.update_status(f"Applying to {len(jobs_to_apply)} jobs...")

            # Update the Total Jobs count to show the jobs that will be processed
            jobs_to_process_count = len(jobs_to_apply)
            self.root.after(0, lambda c=jobs_to_process_count: self.jobs_found_label.config(text=str(c)))

            # Apply job limit if set
            job_limit = self.job_limit_var.get()
            if job_limit > 0 and len(jobs_to_apply) > job_limit:
                limited_count = job_limit
                self.update_status(f"Limiting to {job_limit} jobs as per settings")
                jobs_to_apply = jobs_to_apply[:job_limit]
                self.root.after(0, lambda c=limited_count: self.jobs_found_label.config(text=str(c)))

            # Calculate initial estimated time (assuming 10 jobs per minute)
            jobs_per_minute = 10.0
            total_jobs = len(jobs_to_apply)
            
            if total_jobs > 0:
                estimated_minutes = total_jobs / jobs_per_minute
                hours = int(estimated_minutes // 60)
                minutes = int(estimated_minutes % 60)

                # Format time string
                initial_estimate = ""
                if hours > 0:
                    initial_estimate += f"{hours} hours "
                if minutes > 0 or hours > 0:
                    initial_estimate += f"{minutes} minutes"
                else:
                    initial_estimate += "less than 1 minute"

                # Update both status and dedicated time label
                self.update_status(f"Estimated completion time: {initial_estimate}")
                self.root.after(0, lambda t=initial_estimate: self.estimated_time_label.config(text=t))
            
            # Enable Skip button now that we are actually applying
            self.root.after(0, lambda: self.skip_button.config(state="normal" if not self.is_paused else "disabled"))

            # Start applying to jobs
            applied_count = 0
            failed_count = 0
            
            # Variables for dynamic time estimation
            job_start_times = []
            job_processing_times = []
            
            # Batch buffers for Excel saves
            batch_applied = []
            batch_not_applied = []
            
            def flush_excel_batches(force=False):
                if not getattr(self, 'batch_excel_saves', True):
                    force = True # if batching is off, always force save
                
                # Flush applied jobs
                if len(batch_applied) >= 10 or (force and batch_applied):
                    try:
                        if os.path.exists("applied_jobs.xlsx"):
                            df_ex = pd.read_excel("applied_jobs.xlsx")
                            df_new = pd.DataFrame(batch_applied)
                            df_combined = pd.concat([df_ex, df_new], ignore_index=True)
                        else:
                            df_combined = pd.DataFrame(batch_applied)
                        self.save_dataframe_to_excel_safely(df_combined, "applied_jobs.xlsx")
                        batch_applied.clear()
                    except Exception as e:
                        self.logger.error(f"Error batch saving applied jobs: {e}")
                        
                # Flush not applied jobs
                if len(batch_not_applied) >= 10 or (force and batch_not_applied):
                    try:
                        if os.path.exists("not_applied_jobs.xlsx"):
                            df_ex = pd.read_excel("not_applied_jobs.xlsx")
                            df_new = pd.DataFrame(batch_not_applied)
                            df_combined = pd.concat([df_ex, df_new], ignore_index=True)
                        else:
                            df_combined = pd.DataFrame(batch_not_applied)
                        self.save_dataframe_to_excel_safely(df_combined, "not_applied_jobs.xlsx")
                        batch_not_applied.clear()
                    except Exception as e:
                        self.logger.error(f"Error batch saving not applied jobs: {e}")

            for i, job in enumerate(jobs_to_apply):
                # Check if bot is paused
                while self.is_paused and self.running:
                    time.sleep(1)
                if not self.running:
                    self.update_status("Stopped by user.")
                    return
                
                # Record job start time for this specific job
                job_start_time = time.time()
                
                # Update progress
                progress = int((i / len(jobs_to_apply)) * 100) if jobs_to_apply else 0
                self.root.after(0, lambda p=progress: self.progress_bar.config(value=p))
                
                # Show job details in status
                job_title = job.get("Job Title", "Unknown")
                self.update_status(f"Applying to: {job_title} ({i+1}/{len(jobs_to_apply)})")

                # Apply to job using your existing function
                try:
                    job_result = apply_to_job_url(
                        driver,
                        job["Job URL"],
                        getattr(self, "resume_profiles", []),
                        job_title=job_title,
                        semantic_matcher=self.semantic_matcher if self.semantic_enabled else None,
                        learning_engine=self.learning_engine,
                        groq_scorer=getattr(self, 'groq_scorer', None),
                        pause_check=self.check_pause,
                        headless_mode=getattr(self, 'headless_mode', False)
                    )
                    applied_status, profile_name, match_reason, skip_reason, job_desc_text, profile_id = job_result
                    
                    # Check if a skip was requested
                    if self.skip_requested:
                        self.logger.info(f"Job application skipped by user: {job_title}")
                        self.skip_requested = False
                        
                        # Close popup tabs safely
                        try:
                            if len(driver.window_handles) > 1:
                                for handle in list(driver.window_handles)[1:]:
                                    try:
                                        driver.switch_to.window(handle)
                                        driver.close()
                                    except Exception:
                                        pass
                                driver.switch_to.window(driver.window_handles[0])
                        except Exception as e:
                            self.logger.error(f"Error restoring window handles after skip: {e}")
                        
                        # Log to not applied jobs Excel file
                        job["Applied"] = False
                        job["Resume Profile"] = profile_name or "Default/None"
                        job["Match Reason"] = match_reason or ""
                        job["Skip Reason"] = "Skipped by user"
                        batch_not_applied.append(job.copy())
                        flush_excel_batches()
                            
                        failed_count += 1
                        self.root.after(0, lambda c=failed_count: self.jobs_failed_label.config(text=str(c)))
                        continue

                    # Check if the driver session is still active/valid
                    is_driver_active = True
                    try:
                        _ = driver.current_url
                    except Exception:
                        is_driver_active = False

                    if not is_driver_active:
                        self.logger.error("WebDriver session lost (browser may have been closed or crashed). Stopping application run.")
                        self.update_status("WebDriver session lost. Stopped.")
                        break
                    
                    job["Resume Profile"] = profile_name
                    job["Match Reason"] = match_reason
                    
                    self._log_match_details(profile_name, match_reason)
                    
                    # Record job completion time and calculate processing time for this job
                    job_end_time = time.time()
                    processing_time = job_end_time - job_start_time
                    
                    # Keep track of job times for estimation
                    job_start_times.append(job_start_time)
                    job_processing_times.append(processing_time)
                    
                    # Calculate dynamic time estimate after a few jobs
                    if i >= 2 and len(jobs_to_apply) > i+1:
                        # Calculate average time per job based on the last few jobs
                        recent_times = job_processing_times[-min(10, len(job_processing_times)):]
                        avg_time_per_job = sum(recent_times) / len(recent_times)
                        
                        # Calculate remaining time
                        remaining_jobs = len(jobs_to_apply) - (i + 1)
                        remaining_seconds = avg_time_per_job * remaining_jobs
                        
                        # Format remaining time string
                        remaining_hours = int(remaining_seconds // 3600)
                        remaining_minutes = int((remaining_seconds % 3600) // 60)
                        remaining_seconds = int(remaining_seconds % 60)
                        
                        time_remaining = ""
                        if remaining_hours > 0:
                            time_remaining += f"{remaining_hours} hours "
                        if remaining_minutes > 0 or remaining_hours > 0:
                            time_remaining += f"{remaining_minutes} minutes "
                        time_remaining += f"{remaining_seconds} seconds"
                        
                        # Update the estimated time label
                        self.root.after(0, lambda t=time_remaining: self.estimated_time_label.config(text=t))
                    
                    if applied_status:
                        applied_count += 1
                        # Update applied count
                        count_to_display = applied_count
                        self.root.after(0, lambda c=count_to_display: 
                            self.jobs_applied_label.config(text=str(c)))
                        
                        # Record success for AI Learning
                        if profile_id and self.learning_engine:
                            try:
                                # Use source='auto' for bot applications
                                self.learning_engine.record_success(profile_id, job_title, job_desc_text, source='auto')
                                # Real-time AI Stats Refresh
                                self.root.after(0, self.refresh_ai_stats)
                            except Exception as e:
                                self.logger.error(f"Failed to record AI learning: {e}")
                        
                        # Save to applied jobs Excel file
                        job["Applied"] = True
                        if skip_reason:
                            job["Application Note"] = skip_reason
                        batch_applied.append(job.copy())
                        flush_excel_batches()
                    else:
                        failed_count += 1
                        # Update failed count
                        count_to_display = failed_count
                        self.root.after(0, lambda c=count_to_display: 
                            self.jobs_failed_label.config(text=str(c)))
                        
                        # Save to not applied jobs Excel file
                        job["Applied"] = False
                        raw_reason = skip_reason or "Unknown failure"
                        job["Skip Reason"] = raw_reason[:500] if len(raw_reason) > 500 else raw_reason
                        batch_not_applied.append(job.copy())
                        flush_excel_batches()
                    
                except Exception as e:
                    self.logger.error(f"Error applying to {job_title}: {e}")
                    failed_count += 1
                    # Update failed count
                    count_to_display = failed_count
                    self.root.after(0, lambda c=count_to_display: 
                        self.jobs_failed_label.config(text=str(c)))
                
                # Move mouse to prevent sleeping
                pyautogui.moveRel(1, 1, duration=0.1)
                pyautogui.moveRel(-1, -1, duration=0.1)
            
            # Flush any remaining jobs in the batch buffers
            flush_excel_batches(force=True)
            # ── Retry pass ────────────────────────────────────────────────────
            # Re-attempt jobs that failed due to transient issues (button timeout,
            # wizard timing out, click failures). Only ONE retry per job.
            RETRYABLE_REASONS = [
                "Apply button not found or timed out",
                "Wizard completed max steps but Submit button was never found",
                "Wizard finished all steps but Submit button was never clicked",
                "Failed to click the Apply button",
                "Wizard error",
                "Exception during application",
            ]
            retry_candidates = [
                job for job in jobs_to_apply
                if not job.get("Applied", False)
                and any(r in job.get("Skip Reason", "") for r in RETRYABLE_REASONS)
            ]

            retry_success_count = 0
            retry_fail_count    = 0

            if retry_candidates and self.running:
                not_applied_file = "not_applied_jobs.xlsx"
                self.update_status(f"Retrying {len(retry_candidates)} failed job(s)...")
                self.logger.info(f"Starting retry pass for {len(retry_candidates)} job(s).")

                for job in retry_candidates:
                    # Check if bot is paused
                    while self.is_paused and self.running:
                        time.sleep(1)
                    
                    if not self.running:
                        break
                        
                    try:
                        candidate_job_title = job.get("Job Title", "Unknown")
                        job_result = apply_to_job_url(
                            driver,
                            job["Job URL"],
                            getattr(self, "resume_profiles", []),
                            job_title=candidate_job_title,
                            semantic_matcher=self.semantic_matcher if self.semantic_enabled else None,
                            learning_engine=self.learning_engine,
                            groq_scorer=getattr(self, 'groq_scorer', None),
                            pause_check=self.check_pause
                        )
                        applied_status, profile_name, match_reason, skip_reason, job_desc_text, profile_id = job_result
                        
                        # Check if a skip was requested
                        if self.skip_requested:
                            self.logger.info(f"Job skipped by user during retry: {candidate_job_title}")
                            self.skip_requested = False
                            
                            # Close popup tabs safely
                            try:
                                if len(driver.window_handles) > 1:
                                    for handle in list(driver.window_handles)[1:]:
                                        try:
                                            driver.switch_to.window(handle)
                                            driver.close()
                                        except Exception:
                                            pass
                                driver.switch_to.window(driver.window_handles[0])
                            except Exception as e:
                                self.logger.error(f"Error restoring window handles after skip: {e}")
                            
                            # Log/update the not_applied row as skipped
                            retry_fail_count += 1
                            job["Skip Reason"] = "Skipped by user"
                            try:
                                EXPECTED_COLS = [
                                    "Job Title", "Job URL", "Company", "Location",
                                    "Employment Type", "Posted Date", "Applied",
                                    "Resume Profile", "Match Reason", "Skip Reason"
                                ]
                                if os.path.exists(not_applied_file):
                                    df_not = pd.read_excel(not_applied_file)
                                    for col in EXPECTED_COLS:
                                        if col not in df_not.columns:
                                            df_not[col] = ""
                                else:
                                    df_not = pd.DataFrame(columns=EXPECTED_COLS)
                                if job["Job URL"] in df_not["Job URL"].values:
                                    df_not.loc[df_not["Job URL"] == job["Job URL"], "Skip Reason"] = "Skipped by user"
                                else:
                                    df_not = pd.concat([df_not, pd.DataFrame([job])], ignore_index=True)
                                self.save_dataframe_to_excel_safely(df_not, not_applied_file)
                            except Exception as xe:
                                self.logger.error(f"Error updating not_applied_jobs on retry skip: {xe}")
                            
                            continue

                        # Check if the driver session is still active/valid
                        is_driver_active = True
                        try:
                            _ = driver.current_url
                        except Exception:
                            is_driver_active = False

                        if not is_driver_active:
                            self.logger.error("WebDriver session lost during retry. Stopping retry loop.")
                            break

                        job["Applied"]        = applied_status
                        job["Resume Profile"] = profile_name
                        job["Match Reason"]   = match_reason
                        
                        self._log_match_details(profile_name, match_reason)

                        if applied_status:
                            retry_success_count += 1
                            applied_count       += 1
                            failed_count        -= 1
                            self.root.after(0, lambda c=applied_count: self.jobs_applied_label.config(text=str(c)))
                            self.root.after(0, lambda c=max(failed_count, 0): self.jobs_failed_label.config(text=str(c)))
                            self.logger.info(f"  ✓ Retry succeeded: {candidate_job_title}")
                            # Write to applied_jobs
                            if skip_reason:
                                job["Application Note"] = skip_reason
                            try:
                                if os.path.exists(applied_jobs_file):
                                    df_existing = pd.read_excel(applied_jobs_file)
                                else:
                                    df_existing = pd.DataFrame(columns=[
                                        "Job Title", "Job URL", "Company", "Location",
                                        "Employment Type", "Posted Date", "Applied",
                                        "Resume Profile", "Match Reason", "Application Note"
                                    ])
                                df_combined = pd.concat([df_existing, pd.DataFrame([job])], ignore_index=True)
                                self.save_dataframe_to_excel_safely(df_combined, applied_jobs_file)
                            except Exception as xe:
                                self.logger.error(f"Error updating applied_jobs on retry: {xe}")
                            # Remove from not_applied_jobs
                            try:
                                df_not = pd.read_excel(not_applied_file)
                                df_not = df_not[df_not["Job URL"] != job["Job URL"]]
                                self.save_dataframe_to_excel_safely(df_not, not_applied_file)
                            except Exception:
                                pass
                        else:
                            retry_fail_count += 1
                            self.logger.info(f"  ✗ Retry failed: {candidate_job_title} | {skip_reason}")
                            job["Skip Reason"] = f"[Retry] {skip_reason[:460]}" if skip_reason else "[Retry] Unknown"
                            # Update the not_applied row in place
                            try:
                                EXPECTED_COLS = [
                                    "Job Title", "Job URL", "Company", "Location",
                                    "Employment Type", "Posted Date", "Applied",
                                    "Resume Profile", "Match Reason", "Skip Reason"
                                ]
                                if os.path.exists(not_applied_file):
                                    df_not = pd.read_excel(not_applied_file)
                                    for col in EXPECTED_COLS:
                                        if col not in df_not.columns:
                                            df_not[col] = ""
                                else:
                                    df_not = pd.DataFrame(columns=EXPECTED_COLS)
                                if job["Job URL"] in df_not["Job URL"].values:
                                    df_not.loc[df_not["Job URL"] == job["Job URL"], "Skip Reason"] = job["Skip Reason"]
                                else:
                                    df_not = pd.concat([df_not, pd.DataFrame([job])], ignore_index=True)
                                self.save_dataframe_to_excel_safely(df_not, not_applied_file)
                            except Exception as xe:
                                self.logger.error(f"Error updating not_applied_jobs on retry: {xe}")
                    except Exception as e:
                        self.logger.error(f"Exception during retry of {candidate_job_title}: {e}")
                        retry_fail_count += 1

                self.logger.info(f"Retry pass done: {retry_success_count} succeeded, {retry_fail_count} still failed.")

            # Compute execution time
            end_time = time.time()
            execution_time = end_time - start_time
            hours, remainder = divmod(execution_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            time_str = f"{int(hours)}h {int(minutes)}m {seconds:.2f}s"
            retry_summary = f"\nRetried {len(retry_candidates)} failed job(s): {retry_success_count} succeeded, {retry_fail_count} still failed." if retry_candidates else ""
            self.update_status(f"Completed! Applied: {applied_count}, Failed: {failed_count}, Time: {time_str}")
            
            # Final progress update
            self.root.after(0, lambda: self.progress_bar.config(value=100))
            # Clear estimated time as we're done
            self.root.after(0, lambda: self.estimated_time_label.config(text="Completed"))
            
            # Save job data to JSON file
            import json
            try:
                job_data = {
                    "Total Jobs Found": len(all_jobs),
                    "Jobs Applied": applied_count,
                    "Jobs Failed": failed_count,
                    "Execution Time": time_str,
                    "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                with open("job_application_summary.json", "w") as f:
                    json.dump(job_data, f, indent=4)
            except Exception as e:
                self.logger.error(f"Error saving job data: {e}")
                
            # Show completion message
            self.root.after(0, lambda rs=retry_summary: messagebox.showinfo(
                "Process Complete", 
                f"Application process completed!\n\n"
                f"Applied to {applied_count} jobs\n"
                f"Failed for {failed_count} jobs\n"
                f"{rs}\n\n"
                f"Total execution time: {time_str}"
            ))
            
        except Exception as e:
            self.logger.error(f"Error in job application process: {e}")
            self.update_status(f"Error: {str(e)}")
            self.root.after(0, lambda err_msg=str(e): messagebox.showerror(
                "Error", 
                f"An error occurred: {err_msg}"
            ))
        finally:
            # Clean up webdriver if it exists
            if getattr(self, 'driver', None) is not None:
                try:
                    self.driver.quit()
                except Exception as qe:
                    self.logger.error(f"Error quitting driver: {qe}")
                self.driver = None
            # Reset UI
            self.reset_ui()

    def stop_applying(self):
        """Stop the job application process"""
        if not self.running:
            return
            
        self.running = False
        self.is_paused = False
        self.stop_button.config(state="disabled")
        self.pause_button.config(state="disabled", text="⏸  Pause")
        self.update_status("Stopping... Please wait.")
        self.logger.info("User requested to stop the application process")
        
        # Instantly close browser to unblock the background thread
        if getattr(self, 'driver', None) is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        
    def skip_job(self):
        """Signal to skip the current job application"""
        if self.running and not self.is_paused:
            self.skip_requested = True
            self.update_status("⏭  Skip requested — skipping current job...")
            self.logger.info("Skip current job requested by user.")

    def save_dataframe_to_excel_safely(self, df, filepath):
        """
        Saves a pandas DataFrame to an Excel file, handling PermissionError if open.
        If locked, retries a few times, then saves to a backup file so no data is lost.
        """
        import os
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                df.to_excel(filepath, index=False)
                return True
            except PermissionError:
                self.logger.warning(f"Excel file {filepath} is locked (probably open in Excel). Attempt {attempt+1}/{max_retries}...")
                time.sleep(2)
            except Exception as e:
                self.logger.error(f"Failed to save Excel file: {e}")
                break
                
        # If all attempts failed, save to a backup file
        try:
            dir_name = os.path.dirname(filepath) or "."
            base_name = os.path.basename(filepath)
            backup_path = os.path.join(dir_name, f"backup_{int(time.time())}_{base_name}")
            df.to_excel(backup_path, index=False)
            self.logger.error(f"Could not overwrite {filepath}. Saved backup to {backup_path} instead.")
            self.root.after(0, lambda: messagebox.showwarning(
                "Excel File Locked",
                f"Could not save to {filepath} because it is currently open in Excel.\n\n"
                f"Your data has been saved to backup file:\n{backup_path}\n\n"
                "Please close the file in Excel and run the bot again."
            ))
            return False
        except Exception as e:
            self.logger.error(f"Failed to save backup Excel file: {e}")
            return False

    def reset_ui(self):
        """Reset UI after job completion or stop"""
        self.running = False
        self.is_paused = False
        self.skip_requested = False
        def _reset():
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.pause_button.config(state="disabled", text="⏸  Pause")
            self.skip_button.config(state="disabled")
        self.root.after(0, _reset)
        
    def setup_ai_trainer_tab(self):
        """Set up the UI for the AI Training tab"""
        container = ttk.Frame(self.ai_trainer_tab, padding="20")
        container.pack(fill="both", expand=True)
        
        # Header
        header_lbl = ttk.Label(
            container, 
            text="🧠 AI Training Center", 
            font=("Segoe UI", 16, "bold"),
            foreground="#2c3e50"
        )
        header_lbl.pack(anchor="w", pady=(0, 10))
        
        desc_lbl = ttk.Label(
            container,
            text="Paste a 'Dream Job' description below to teach the AI what you're looking for.\nThis will boost matching accuracy for your selected profile.",
            font=("Segoe UI", 10),
            foreground="#666"
        )
        desc_lbl.pack(anchor="w", pady=(0, 20))
        
        # Split into Left (Input) and Right (Stats)
        panes = ttk.Frame(container)
        panes.pack(fill="both", expand=True)

        left_pane = ttk.Frame(panes)
        left_pane.pack(side="left", fill="both", expand=True, padx=(0, 20))
        
        # --- Job Description Input ---
        ttk.Label(left_pane, text="Paste Job Description / Requirements:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 5))
        
        t = getattr(self, "_theme", {})
        ENTRY_BG = t.get("ENTRY_BG", "#ffffff")
        FG = t.get("FG", "#000000")
        BORDER = t.get("BORDER", "#cccccc")

        self.trainer_text = tk.Text(left_pane, height=15, font=("Consolas", 10), wrap="word", 
                                    bg=ENTRY_BG, fg=FG, insertbackground=FG,
                                    borderwidth=1, relief="solid", highlightbackground=BORDER)
        self.trainer_text.pack(fill="both", expand=True)
        
        # Controls (Profile select + Train button)
        controls = ttk.Frame(left_pane, padding="10 10 0 0")
        controls.pack(fill="x")
        
        ttk.Label(controls, text="Target Profile:").pack(side="left", padx=(0, 5))
        self.trainer_profile_var = tk.StringVar()
        self.trainer_profile_combo = ttk.Combobox(controls, textvariable=self.trainer_profile_var, state="readonly", width=30)
        self.trainer_profile_combo.pack(side="left", padx=(0, 20))
        
        # Populating profile combo
        self.sync_trainer_profiles()
        def _on_train():
            jd = self.trainer_text.get("1.0", "end-1c").strip()
            p_name = self.trainer_profile_var.get()
            
            if not jd or not p_name:
                messagebox.showwarning("Incomplete", "Please paste a job description and select a profile.")
                return
            
            # Find profile ID
            p_id = next((p.get('id') for p in self.resume_profiles if p.get('name') == p_name), None)
            
            if p_id is None:
                messagebox.showerror("Error", "Could not find profile ID.")
                return
                
            try:
                # Use source='manual' for samples pasted in this tab
                self.learning_engine.record_success(p_id, f"Manual: {p_name}", jd, source='manual')
                self.trainer_text.delete("1.0", tk.END)
                self.refresh_ai_stats()
                messagebox.showinfo("Success", f"AI has successfully learned this manual sample for '{p_name}'!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to train: {e}")
                
        self.train_btn = ttk.Button(controls, text="🚀 Train AI on this Sample", command=_on_train)
        self.train_btn.pack(side="left")

        # --- Self Training Analysis Section ---
        analysis_frame = ttk.LabelFrame(left_pane, text=" Self Training Tools ", padding="10")
        analysis_frame.pack(fill="x", pady=(20, 0))
        
        ttk.Label(analysis_frame, text="Let the AI propose a match for your pasted JD:").pack(anchor="w")
        
        suggest_row = ttk.Frame(analysis_frame)
        suggest_row.pack(fill="x", pady=5)
        
        self.analyze_btn = ttk.Button(suggest_row, text="🔍 Analyze JD", command=self.analyze_jd_for_training)
        self.analyze_btn.pack(side="left", padx=(0, 10))
        
        self.suggestion_var = tk.StringVar(value="✨ Paste a JD and click Analyze...")
        self.suggestion_label = ttk.Label(suggest_row, textvariable=self.suggestion_var, font=("Segoe UI", 10, "italic"), foreground="#0056b3")
        self.suggestion_label.pack(side="left")
        
        self.confirm_frame = ttk.Frame(analysis_frame)
        # Hidden initially
        
        self.approve_btn = ttk.Button(self.confirm_frame, text="✅ Approve match", style="Start.TButton", command=self.approve_suggestion)
        self.approve_btn.pack(side="left", padx=5)
        
        self.reject_btn = ttk.Button(self.confirm_frame, text="❌ Not a fit", command=self.reject_suggestion)
        self.reject_btn.pack(side="left", padx=5)
        
        self.suggested_id = None
        
        # Right Pane: Per-profile Training Cards
        right_pane = ttk.LabelFrame(panes, text=" AI Memory — Per Profile ", padding="10")
        right_pane.pack(side="right", fill="both", ipadx=5)

        ttk.Label(right_pane, text="Training samples recorded per resume.",
                  font=("Segoe UI", 9), foreground="#555").pack(anchor="w", pady=(0, 8))

        # Scrollable canvas for cards
        self._ai_cards_canvas = tk.Canvas(right_pane, width=300, highlightthickness=0, bg="#f0f4f8")
        _vsb = ttk.Scrollbar(right_pane, orient="vertical", command=self._ai_cards_canvas.yview)
        self._ai_cards_canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        self._ai_cards_canvas.pack(side="left", fill="both", expand=True)

        self._ai_cards_frame = ttk.Frame(self._ai_cards_canvas)
        self._ai_cards_window = self._ai_cards_canvas.create_window(
            (0, 0), window=self._ai_cards_frame, anchor="nw"
        )
        self._ai_cards_frame.bind(
            "<Configure>",
            lambda e: self._ai_cards_canvas.configure(
                scrollregion=self._ai_cards_canvas.bbox("all")
            )
        )

        self.refresh_ai_stats()

    def analyze_jd_for_training(self):
        """Analyze the JD in the trainer text box and suggest a profile"""
        jd = self.trainer_text.get("1.0", "end-1c").strip()
        if not jd:
            messagebox.showwarning("Empty", "Please paste a job description first.")
            return
            
        if not self.resume_profiles:
            messagebox.showwarning("No Profiles", "Please add at least one resume profile first.")
            return

        self.suggestion_var.set("✨ AI is thinking...")
        self.confirm_frame.pack_forget()
        
        # We'll use a temporary matcher to score the JD
        from core.matcher import ResumeMatcher
        # If semantic matcher isn't ready, we'll just use keyword matching
        matcher = ResumeMatcher(self.resume_profiles, semantic_matcher=self.semantic_matcher)
        
        # Scoring logic (simplified titles for training focus)
        results = []
        try:
            # We try to find the best match using the matcher's logic
            # For training purposes, we care about the holistic score
            for p in self.resume_profiles:
                # We'll do a quick score. In a real scenario we'd use semantic or keyword density.
                # Here we'll just use the semantic matcher if available, or keyword overlap.
                p_id = p.get('id')
                score = 0
                if self.semantic_matcher:
                    # Holistic semantic check
                    score_res = self.semantic_matcher.score_job("Training Sample", jd)
                    for r in score_res:
                        if r['profile_id'] == p_id:
                            score = r['semantic_score']
                            break
                else:
                    # Fallback to simple keyword density check
                    text = jd.lower()
                    kws = p.get('unique_keywords', []) + p.get('keywords', [])
                    matches = sum(1 for kw in kws if kw.lower() in text)
                    score = (matches / len(kws) * 100) if kws else 0
                
                results.append({'id': p_id, 'name': p.get('name'), 'score': score})
            
            results.sort(key=lambda x: x['score'], reverse=True)
            best = results[0]
            
            if best['score'] > 20: # Reasonable threshold
                self.suggestion_var.set(f"✨ Match found: {best['name']} ({best['score']}%)")
                self.suggested_id = best['id']
                self.confirm_frame.pack(fill="x", pady=5)
                # Auto-select in combo for convenience
                self.trainer_profile_var.set(best['name'])
            else:
                self.suggestion_var.set("✨ AI is unsure. Low confidence match.")
                self.suggested_id = None
                self.confirm_frame.pack_forget()
                
        except Exception as e:
            self.logger.error(f"Analysis failed: {e}")
            self.suggestion_var.set("✨ AI Analysis failed.")

    def approve_suggestion(self):
        if not self.suggested_id: return
        
        jd = self.trainer_text.get("1.0", "end-1c").strip()
        p_name = next((p.get('name') for p in self.resume_profiles if p.get('id') == self.suggested_id), "Unknown")
        
        try:
            self.learning_engine.record_success(self.suggested_id, f"Approved Match: {p_name}", jd, source='manual')
            self.trainer_text.delete("1.0", tk.END)
            self.confirm_frame.pack_forget()
            self.suggestion_var.set("✨ Paste a JD and click Analyze...")
            self.refresh_ai_stats()
            messagebox.showinfo("Success", f"AI learned from your approval for '{p_name}'!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to record approval: {e}")

    def reject_suggestion(self):
        self.suggestion_var.set("✨ Suggestion rejected. Please pick manually.")
        self.confirm_frame.pack_forget()
        self.suggested_id = None

    def sync_trainer_profiles(self):
        """Updates the dropdown in the AI trainer tab with the current resume profiles"""
        if not hasattr(self, 'trainer_profile_combo'): return
        
        names = [p.get('name', 'Unknown') for p in self.resume_profiles]
        self.trainer_profile_combo['values'] = names
        if names and not self.trainer_profile_var.get():
             self.trainer_profile_combo.current(0)
             
        self._sync_kw_profiles()

    def _sync_kw_profiles(self):
        """Updates the dropdown in the Groq Scanner tab with current resume profiles"""
        if not hasattr(self, 'kw_profile_combo'): return
        
        names = [p.get('name', 'Unknown') for p in self.resume_profiles]
        self.kw_profile_combo['values'] = names
        if names and not self.kw_profile_var.get():
             self.kw_profile_combo.current(0)
             
    def refresh_ai_stats(self):
        """Rebuild per-profile training cards in the AI trainer right pane."""
        if not hasattr(self, '_ai_cards_frame'):
            return

        # Destroy existing cards
        for widget in self._ai_cards_frame.winfo_children():
            widget.destroy()

        stats  = self.learning_engine.get_stats()
        t      = getattr(self, '_theme', {})
        BG     = t.get('PANEL', '#ffffff')
        BORDER = t.get('BORDER', '#334155')

        if not self.resume_profiles:
            ttk.Label(self._ai_cards_frame,
                      text="No resume profiles configured.",
                      font=("Segoe UI", 9), foreground="#888").pack(pady=20)
            return

        for p in self.resume_profiles:
            p_id   = p.get('id')
            p_name = p.get('name', 'Unknown')
            p_stats = stats.get(p_id, {'manual': 0, 'auto': 0})
            m_count = p_stats.get('manual', 0)
            a_count = p_stats.get('auto',   0)
            total   = m_count + a_count

            status_icon  = "⭐" if total >= 5 else ("🌱" if total > 0 else "⬜")
            status_color = "#4ade80" if total >= 5 else ("#fbbf24" if total > 0 else "#94a3b8")

            # Card frame — use a slightly lighter panel shade so it pops against canvas
            card = tk.Frame(
                self._ai_cards_frame,
                bg="#1e293b", bd=0,
                highlightthickness=1,
                highlightbackground="#334155"
            )
            card.pack(fill="x", padx=6, pady=5, ipady=6)

            # Top row: icon + profile name (white text so it's always visible)
            top = tk.Frame(card, bg="#1e293b")
            top.pack(fill="x", padx=8, pady=(6, 2))
            tk.Label(top, text=status_icon, bg="#1e293b",
                     font=("Segoe UI", 13)).pack(side="left")
            tk.Label(top, text=p_name, bg="#1e293b",
                     font=("Segoe UI", 9, "bold"),
                     fg="#f1f5f9", wraplength=220, justify="left").pack(side="left", padx=6)

            # Middle row: sample counts
            mid = tk.Frame(card, bg="#1e293b")
            mid.pack(fill="x", padx=14, pady=2)
            count_text = f"{total} sample(s)  ·  {m_count} manual  ·  {a_count} auto"
            tk.Label(mid, text=count_text, bg="#1e293b",
                     font=("Segoe UI", 8), fg=status_color).pack(side="left")

            # Bottom row: Reset button (only if there IS training data)
            if total > 0:
                bot = tk.Frame(card, bg="#1e293b")
                bot.pack(fill="x", padx=8, pady=(4, 6))

                def _make_reset(pid, pname):
                    def _reset():
                        ans = messagebox.askyesno(
                            "Reset Training Data",
                            f"Delete ALL {total} training sample(s) for:\n\n  '{pname}'\n\n"
                            "This cannot be undone. The AI will start fresh for this profile."
                        )
                        if ans:
                            self.learning_engine.delete_profile_history(pid)
                            self.refresh_ai_stats()
                            messagebox.showinfo(
                                "Done",
                                f"Training data for '{pname}' has been cleared.\n"
                                "You can now retrain this profile from scratch."
                            )
                    return _reset

                reset_btn = tk.Button(
                    bot,
                    text="  Reset Training  ",
                    command=_make_reset(p_id, p_name),
                    bg="#dc2626", fg="white",
                    font=("Segoe UI", 8, "bold"),
                    relief="flat", cursor="hand2",
                    activebackground="#b91c1c", activeforeground="white",
                    padx=8, pady=3
                )
                reset_btn.pack(side="left")

        # Update canvas scroll region after rebuilding
        self._ai_cards_frame.update_idletasks()
        self._ai_cards_canvas.configure(
            scrollregion=self._ai_cards_canvas.bbox("all")
        )

    def _run_auto_scan_if_needed(self):
        """Silently scans top 5 resumes in the background if 7 days have passed."""
        import datetime
        try:
            last_run_str = getattr(self, 'last_auto_scan_timestamp', '')
            if last_run_str:
                last_run = datetime.datetime.fromisoformat(last_run_str)
                if (datetime.datetime.now() - last_run).days < 7:
                    return # Less than 7 days have passed
        except Exception:
            pass # Invalid or empty date format, run it now

        if not getattr(self, 'groq_scorer', None):
            return

        def _bg_scan():
            from core.resume_keyword_scanner import ResumeKeywordScanner
            from core.matcher import ResumeMatcher
            import re

            self.logger.info("[Auto-Scan] Starting weekly keyword extraction for up to 5 profiles...")
            scanner = ResumeKeywordScanner(self.groq_scorer)
            profiles_scanned = 0
            keywords_added = 0

            # Scan up to 5 profiles to save tokens
            for profile in self.resume_profiles[:5]:
                try:
                    file_path = profile.get('file_path', '')
                    if not file_path or not os.path.exists(file_path):
                        continue

                    # Call Groq API
                    res_kws = scanner.scan_resume(file_path)
                    
                    # Compute gaps manually here to avoid UI coupling
                    def _norm(lst): return {k.lower(): k for k in lst}
                    res_unique = _norm(res_kws.get("unique", []))
                    res_general = _norm(res_kws.get("general", []))
                    res_all = {**res_general, **res_unique}
                    
                    prof_unique = profile.get("unique_keywords", [])
                    prof_general = profile.get("keywords", [])
                    
                    prof_all_regex = []
                    for kw in prof_unique + prof_general:
                        pat = ResumeMatcher.build_keyword_pattern(kw)
                        if pat: prof_all_regex.append(re.compile(pat, re.IGNORECASE))
                        
                    def _is_in_profile(kw):
                        for pat in prof_all_regex:
                            if pat.search(kw): return True
                        return False

                    added_unique = []
                    added_general = []
                    for key, original in res_all.items():
                        if not _is_in_profile(key):
                            if key in res_unique: added_unique.append(original)
                            else: added_general.append(original)

                    if added_unique or added_general:
                        if "unique_keywords" not in profile: profile["unique_keywords"] = []
                        if "keywords" not in profile: profile["keywords"] = []
                        profile["unique_keywords"].extend(added_unique)
                        profile["keywords"].extend(added_general)
                        keywords_added += len(added_unique) + len(added_general)
                    
                    profiles_scanned += 1
                except Exception as e:
                    self.logger.error(f"[Auto-Scan] Error scanning {profile.get('name')}: {e}")

            # Update timestamp and save
            self.last_auto_scan_timestamp = datetime.datetime.now().isoformat()
            self.root.after(0, self.save_config)
            
            if keywords_added > 0:
                self.logger.info(f"[Auto-Scan] Complete. Added {keywords_added} new keywords across {profiles_scanned} profiles.")
                from tkinter import messagebox
                self.root.after(0, lambda: messagebox.showinfo("Auto-Scan Complete", f"Weekly Groq auto-scan found and added {keywords_added} new missing keywords to your profiles!"))
        
        import threading
        threading.Thread(target=_bg_scan, daemon=True).start()

    def setup_groq_scanner_tab(self):
        """Set up the UI for the Groq AI Scanner tab"""
        container = ttk.Frame(self.groq_scanner_tab, padding="20")
        container.pack(fill="both", expand=True)
        
        # Header
        header_lbl = ttk.Label(
            container, 
            text="🤖 Groq AI Keyword Discovery", 
            font=("Segoe UI", 16, "bold"),
            foreground="#2c3e50"
        )
        header_lbl.pack(anchor="w", pady=(0, 10))
        
        desc_lbl = ttk.Label(
            container,
            text="Scan your physical resume to find missing tech keywords, and monitor your Groq API usage.",
            font=("Segoe UI", 10),
            foreground="#666"
        )
        desc_lbl.pack(anchor="w", pady=(0, 20))
        
        # ─────────────────────────────────────────────────────────────────────
        # Universal API Key Manager
        # ─────────────────────────────────────────────────────────────────────
        api_mgr_frame = ttk.LabelFrame(container, text=" 🔑 API Key Manager ", padding="10")
        api_mgr_frame.pack(fill="x", pady=(0, 18))
        api_mgr_frame.columnconfigure(0, weight=1)

        PROVIDER_ICONS = {
            "Groq":      "⚡",
            "OpenAI":    "🤖",
            "Anthropic": "🧠",
            "Apify":     "🕷",
            "Custom":    "🔧",
        }
        PROVIDERS = list(PROVIDER_ICONS.keys())

        # ── Treeview table ─────────────────────────────────────────────────
        tree_frame = ttk.Frame(api_mgr_frame)
        tree_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 8))
        tree_frame.columnconfigure(0, weight=1)

        cols = ("provider", "name", "key_masked")
        self.api_key_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=5, selectmode="browse")
        self.api_key_tree.heading("provider", text="Provider")
        self.api_key_tree.heading("name",     text="Key Name")
        self.api_key_tree.heading("key_masked", text="API Key (Masked)")
        self.api_key_tree.column("provider",   width=110, anchor="center")
        self.api_key_tree.column("name",       width=160, anchor="w")
        self.api_key_tree.column("key_masked", width=250, anchor="w")

        tree_sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.api_key_tree.yview)
        self.api_key_tree.configure(yscrollcommand=tree_sb.set)
        self.api_key_tree.grid(row=0, column=0, sticky="ew")
        tree_sb.grid(row=0, column=1, sticky="ns")

        def _mask(key):
            """Show first 8 chars then ****"""
            key = key.strip()
            if len(key) > 8:
                return f"{key[:8]}...{'*' * 8}"
            return "*" * len(key)

        def _refresh_tree():
            for item in self.api_key_tree.get_children():
                self.api_key_tree.delete(item)
            for idx, entry in enumerate(getattr(self, 'api_keys_list', [])):
                icon = PROVIDER_ICONS.get(entry.get('provider', 'Custom'), "🔧")
                self.api_key_tree.insert("", "end", iid=str(idx), values=(
                    f"{icon}  {entry.get('provider', 'Custom')}",
                    entry.get('name', ''),
                    _mask(entry.get('key', ''))
                ))

        _refresh_tree()

        # ── Input row ─────────────────────────────────────────────────────
        input_frame = ttk.Frame(api_mgr_frame)
        input_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 5))
        for c in range(5): input_frame.columnconfigure(c, weight=(1 if c in (1, 3) else 0))

        ttk.Label(input_frame, text="Provider:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        _provider_var = tk.StringVar(value="Groq")
        provider_cb = ttk.Combobox(input_frame, textvariable=_provider_var,
                                   values=PROVIDERS, state="readonly", width=12)
        provider_cb.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        ttk.Label(input_frame, text="Name:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        _name_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=_name_var, width=18).grid(row=0, column=3, sticky="ew", padx=(0, 10))

        ttk.Label(input_frame, text="API Key:").grid(row=0, column=4, sticky="w", padx=(0, 4))
        _key_var = tk.StringVar()
        _key_entry = ttk.Entry(input_frame, textvariable=_key_var, show="*", width=30)
        _key_entry.grid(row=0, column=5, sticky="ew", padx=(0, 8))
        input_frame.columnconfigure(5, weight=2)

        def _add_key():
            provider = _provider_var.get().strip()
            name     = _name_var.get().strip()
            key      = _key_var.get().strip()
            if not key:
                messagebox.showwarning("Missing Key", "Please enter an API key before adding.")
                return
            if not name:
                # Auto-name based on provider + count
                existing = [e for e in getattr(self, 'api_keys_list', []) if e.get('provider') == provider]
                name = f"{provider} Key {len(existing) + 1}"
            entry = {'provider': provider, 'name': name, 'key': key}
            if not hasattr(self, 'api_keys_list') or self.api_keys_list is None:
                self.api_keys_list = []
            self.api_keys_list.append(entry)
            _refresh_tree()
            _name_var.set("")
            _key_var.set("")
            self._persist_config()

        def _remove_key():
            sel = self.api_key_tree.selection()
            if not sel:
                messagebox.showwarning("No Selection", "Please click a key in the table to select it first.")
                return
            idx = int(sel[0])
            entry = self.api_keys_list[idx]
            if messagebox.askyesno("Remove Key", f"Remove '{entry.get('name', 'this key')}'?"):
                self.api_keys_list.pop(idx)
                _refresh_tree()
                self._persist_config()

        # ── Action buttons ─────────────────────────────────────────────────
        btn_frame = ttk.Frame(api_mgr_frame)
        btn_frame.grid(row=2, column=0, sticky="w", padx=5, pady=(0, 5))

        ttk.Button(btn_frame, text="✅ Save & Add Key", command=_add_key, style="Start.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="🗑 Remove Selected", command=_remove_key).pack(side="left")

        ttk.Label(api_mgr_frame,
                  text="💡 Add any provider key (Groq, OpenAI, Claude, Apify...). Only Groq keys are used for the Tiebreaker. Other keys are safely stored for future features.",
                  foreground="#666", font=("Segoe UI", 8, "italic")
                  ).grid(row=3, column=0, sticky="w", padx=5, pady=(0, 5))


        # Split into Left (Scanner) and Right (Stats)
        panes = ttk.Frame(container)
        panes.pack(fill="both", expand=True)
        
        left_pane = ttk.Frame(panes)
        left_pane.pack(side="left", fill="both", expand=True, padx=(0, 20))
        
        # --- Keyword Discovery Section ---
        kw_discovery_frame = ttk.LabelFrame(left_pane, text=" Keyword Discovery (Groq) ", padding="10")
        kw_discovery_frame.pack(fill="x", pady=(0, 20))
        
        ttk.Label(kw_discovery_frame, text="Scan your resume .docx and automatically extract all technical keywords to fix profile gaps.").pack(anchor="w")
        
        kw_row = ttk.Frame(kw_discovery_frame)
        kw_row.pack(fill="x", pady=5)
        
        ttk.Label(kw_row, text="Target Profile:").pack(side="left", padx=(0, 5))
        self.kw_profile_var = tk.StringVar()
        self.kw_profile_combo = ttk.Combobox(kw_row, textvariable=self.kw_profile_var, state="readonly", width=30)
        self.kw_profile_combo.pack(side="left", padx=(0, 20))
        
        self.kw_results_var = tk.StringVar(value="")
        kw_result_lbl = ttk.Label(kw_discovery_frame, textvariable=self.kw_results_var, font=("Segoe UI", 9, "bold"))
        kw_result_lbl.pack(anchor="w", pady=(5,0))
        
        self.kw_missing_text = tk.Text(kw_discovery_frame, height=8, font=("Consolas", 10), wrap="word", bg="#f8d7da", fg="#721c24", state="disabled")
        
        def _on_scan_resume():
            p_name = self.kw_profile_var.get()
            if not p_name:
                from tkinter import messagebox
                messagebox.showwarning("Incomplete", "Please select a profile.")
                return
            
            profile = next((p for p in self.resume_profiles if p.get('name') == p_name), None)
            if not profile or not profile.get('file_path'):
                from tkinter import messagebox
                messagebox.showerror("Error", "Could not find profile or resume file path.")
                return
                
            self.kw_results_var.set("Scanning resume with Groq... Please wait.")
            self.kw_missing_text.pack_forget()
            kw_add_btn.pack_forget()
            self.root.update()
            
            import threading
            def _scan():
                try:
                    from core.resume_keyword_scanner import ResumeKeywordScanner
                    scanner = ResumeKeywordScanner(getattr(self, 'groq_scorer', None))
                    res_kws = scanner.scan_resume(profile['file_path'])
                    
                    def _norm(lst): return {k.lower(): k for k in lst}
                    res_unique = _norm(res_kws.get("unique", []))
                    res_general = _norm(res_kws.get("general", []))
                    res_all = {**res_general, **res_unique}
                    
                    prof_unique = profile.get("unique_keywords", [])
                    prof_general = profile.get("keywords", [])
                    
                    import re
                    from core.matcher import ResumeMatcher
                    prof_all_regex = []
                    for kw in prof_unique + prof_general:
                        pat = ResumeMatcher.build_keyword_pattern(kw)
                        if pat: prof_all_regex.append(re.compile(pat, re.IGNORECASE))
                        
                    def _is_in_profile(kw):
                        for pat in prof_all_regex:
                            if pat.search(kw): return True
                        return False
                        
                    gaps = {"unique": [], "general": []}
                    for key, original in res_all.items():
                        if not _is_in_profile(key):
                            if key in res_unique: gaps["unique"].append(original)
                            else: gaps["general"].append(original)
                            
                    self.root.after(0, lambda: _show_gaps(res_all, gaps, profile, res_kws))
                except Exception as e:
                    err_msg = str(e)
                    self.root.after(0, lambda m=err_msg: self.kw_results_var.set(f"Error: {m}"))
                    
            threading.Thread(target=_scan, daemon=True).start()
            
        def _show_gaps(res_all, gaps, profile, res_kws):
            total_missing = len(gaps["unique"]) + len(gaps["general"])
            self.kw_results_var.set(f"Found {len(res_all)} keywords in resume. {total_missing} missing from profile.")
            
            self._last_raw_scan = res_kws
            kw_view_btn.pack(side="left", padx=(5, 0))
            
            if total_missing > 0:
                self.kw_missing_text.config(state="normal")
                self.kw_missing_text.delete("1.0", tk.END)
                all_gaps = gaps["unique"] + gaps["general"]
                self.kw_missing_text.insert(tk.END, ", ".join(all_gaps))
                self.kw_missing_text.config(state="disabled")
                self.kw_missing_text.pack(fill="x", pady=5)
                
                self._current_gaps = gaps
                self._current_kw_profile = profile
                kw_add_btn.pack(side="right")
        
        def _add_gaps():
            if not hasattr(self, '_current_gaps') or not hasattr(self, '_current_kw_profile'):
                return
                
            p_dict = self._current_kw_profile
            gaps = self._current_gaps
            
            if "unique_keywords" not in p_dict: p_dict["unique_keywords"] = []
            if "keywords" not in p_dict: p_dict["keywords"] = []
            
            p_dict["unique_keywords"].extend(gaps["unique"])
            p_dict["keywords"].extend(gaps["general"])
            
            self.save_config()
            self.kw_results_var.set("Keywords added to profile successfully!")
            self.kw_missing_text.pack_forget()
            kw_add_btn.pack_forget()
            from tkinter import messagebox
            messagebox.showinfo("Success", f"Added {len(gaps['unique'])} unique and {len(gaps['general'])} general keywords to {p_dict.get('name')}.")

        kw_scan_btn = ttk.Button(kw_row, text="📄 Scan My Resume", command=_on_scan_resume, style="Blue.TButton")
        kw_scan_btn.pack(side="left")

        def _on_scan_all_resumes():
            """Scan ALL resume profiles with Groq and auto-add all missing keywords."""
            if not self.resume_profiles:
                messagebox.showwarning("No Profiles", "No resume profiles configured.")
                return
            if not getattr(self, 'groq_scorer', None):
                messagebox.showerror("No Groq Key", "Groq API key is not configured. Please set it in the Outreach Bot settings.")
                return
            if not messagebox.askyesno("Scan All Resumes",
                f"This will scan all {len(self.resume_profiles)} resume(s) with Groq AI and automatically add any missing keywords to each profile.\n\nContinue?"):
                return

            self.kw_results_var.set(f"Scanning all {len(self.resume_profiles)} resumes... Please wait.")
            self.kw_missing_text.pack_forget()
            kw_add_btn.pack_forget()
            self.root.update()

            def _scan_all():
                from core.resume_keyword_scanner import ResumeKeywordScanner
                from core.matcher import ResumeMatcher
                import re as _re

                scanner = ResumeKeywordScanner(self.groq_scorer)
                total_added = 0
                scanned = 0
                errors = 0

                for idx, profile in enumerate(self.resume_profiles):
                    p_name = profile.get('name', f'Profile {idx+1}')
                    self.root.after(0, lambda n=p_name, i=idx:
                        self.kw_results_var.set(f"Scanning {i+1}/{len(self.resume_profiles)}: {n}..."))

                    file_path = profile.get('file_path', '')
                    if not file_path or not os.path.exists(file_path):
                        self.logger.warning(f"[Scan All] Skipping {p_name} — file not found: {file_path}")
                        errors += 1
                        continue

                    try:
                        res_kws = scanner.scan_resume(file_path)

                        def _norm(lst): return {k.lower(): k for k in lst}
                        res_unique = _norm(res_kws.get("unique", []))
                        res_general = _norm(res_kws.get("general", []))
                        res_all = {**res_general, **res_unique}

                        prof_unique = profile.get("unique_keywords", [])
                        prof_general = profile.get("keywords", [])

                        prof_all_regex = []
                        for kw in prof_unique + prof_general:
                            pat = ResumeMatcher.build_keyword_pattern(kw)
                            if pat: prof_all_regex.append(_re.compile(pat, _re.IGNORECASE))

                        def _is_in_profile(kw, _regexes=prof_all_regex):
                            for pat in _regexes:
                                if pat.search(kw): return True
                            return False

                        added_unique, added_general = [], []
                        for key, original in res_all.items():
                            if not _is_in_profile(key):
                                if key in res_unique: added_unique.append(original)
                                else: added_general.append(original)

                        if added_unique or added_general:
                            profile.setdefault("unique_keywords", []).extend(added_unique)
                            profile.setdefault("keywords", []).extend(added_general)
                            total_added += len(added_unique) + len(added_general)

                        scanned += 1
                        self.logger.info(f"[Scan All] {p_name}: +{len(added_unique)} unique, +{len(added_general)} general keywords.")

                    except Exception as e:
                        self.logger.error(f"[Scan All] Error scanning {p_name}: {e}")
                        errors += 1

                # Save once after all profiles updated
                self.root.after(0, self.save_config)

                def _done():
                    msg = f"Scan All complete: {scanned}/{len(self.resume_profiles)} profiles scanned, {total_added} keywords added."
                    if errors:
                        msg += f" ({errors} skipped/errored)"
                    self.kw_results_var.set(msg)
                    messagebox.showinfo("Scan All Complete",
                        f"✅ Scanned {scanned} profile(s).\n"
                        f"➕ Added {total_added} new missing keywords.\n"
                        + (f"⚠️  {errors} profile(s) had errors or missing files." if errors else ""))

                self.root.after(0, _done)

            threading.Thread(target=_scan_all, daemon=True).start()

        kw_scan_all_btn = ttk.Button(kw_row, text="📋 Scan All Resumes", command=_on_scan_all_resumes, style="Amber.TButton")
        kw_scan_all_btn.pack(side="left", padx=(8, 0))
        
        def _view_raw_scan():
            if not hasattr(self, '_last_raw_scan') or not self._last_raw_scan:
                return
            top = tk.Toplevel(self.root)
            top.title("Raw Groq Scan Results")
            top.geometry("500x600")
            
            txt = tk.Text(top, font=("Segoe UI", 10), wrap="word")
            txt.pack(fill="both", expand=True, padx=10, pady=10)
            
            txt.insert("end", "--- UNIQUE / SPECIALIZED KEYWORDS ---\n")
            for k in self._last_raw_scan.get("unique", []):
                txt.insert("end", f"• {k}\n")
                
            txt.insert("end", "\n--- GENERAL KEYWORDS ---\n")
            for k in self._last_raw_scan.get("general", []):
                txt.insert("end", f"• {k}\n")
                
            txt.config(state="disabled")

        kw_view_btn = ttk.Button(kw_row, text="👀 View Raw Scan Results", command=_view_raw_scan)
        # Will be packed when scan finishes
        
        kw_add_btn = ttk.Button(kw_discovery_frame, text="✅ Add Missing Keywords", command=_add_gaps, style="Start.TButton")
        
        # --- Auto Scan Checkbox ---
        auto_scan_frame = tk.Frame(kw_discovery_frame, bg="#1e293b")
        auto_scan_frame.pack(fill="x", pady=(15, 0))
        
        self.auto_scan_var = tk.BooleanVar(value=getattr(self, 'auto_scan_enabled', False))
        auto_scan_chk = ToggleSwitch(
            auto_scan_frame, 
            variable=self.auto_scan_var,
            command=self._persist_config,   # lightweight write — no widget reads needed
            bg="#1e293b"
        )
        auto_scan_chk.pack(side="left")
        ttk.Label(auto_scan_frame, text="Enable Auto-Scan (Automatically scans 5 resumes silently every 7 days)").pack(side="left", padx=(8,0))

        # Initial sync for the profile dropdown
        self._sync_kw_profiles()

        # Right Pane: Groq API Usage Dashboard
        right_pane = ttk.LabelFrame(panes, text=" Groq API Usage Dashboard ", padding="15")
        right_pane.pack(side="right", fill="both", ipadx=5)

        ttk.Label(right_pane, text="Track llama-3.1-8b-instant inference activity.",
                  font=("Segoe UI", 9), foreground="#555").pack(anchor="w", pady=(0, 15))

        stats_frame = tk.Frame(right_pane, bg="#1e293b", bd=2, relief="groove")
        stats_frame.pack(fill="x", expand=False)
        
        def _stat_row(parent, label_text, val_text, color="#e2e8f0"):
            row = tk.Frame(parent, bg="#1e293b")
            row.pack(fill="x", pady=4, padx=10)
            tk.Label(row, text=label_text, font=("Segoe UI", 10, "bold"), bg="#1e293b", fg="#94a3b8").pack(side="left")
            val_lbl = tk.Label(row, text=val_text, font=("Consolas", 11, "bold"), bg="#1e293b", fg=color)
            val_lbl.pack(side="right")
            return val_lbl

        self.lbl_groq_status = _stat_row(stats_frame, "API Status:", "🟢 Online", "#4ade80")
        
        # Mask API Key — show currently active Groq key
        raw_key = ""
        if getattr(self, 'groq_scorer', None) and hasattr(self.groq_scorer, '_api_keys') and self.groq_scorer._api_keys:
            raw_key = self.groq_scorer._api_keys[self.groq_scorer._current_key_idx]
        masked = f"{raw_key[:8]}...****" if raw_key else "Not Configured"
        _stat_row(stats_frame, "Active Groq Key:", masked, "#fbbf24")

        # Show total number of Groq keys registered
        groq_key_count = len([e for e in getattr(self, 'api_keys_list', []) if e.get('provider', '').lower() == 'groq'])
        _stat_row(stats_frame, "Groq Keys Registered:", str(groq_key_count), "#a78bfa")
        
        # Add a separator
        tk.Frame(stats_frame, height=1, bg="#334155").pack(fill="x", pady=8, padx=10)
        
        self.lbl_groq_calls = _stat_row(stats_frame, "Total API Calls:", "0", "#60a5fa")
        self.lbl_groq_saved = _stat_row(stats_frame, "Saved via Cache:", "0", "#34d399")
        self.lbl_groq_last = _stat_row(stats_frame, "Last Call Time:", "Never", "#cbd5e1")
        
        def _refresh_stats():
            if hasattr(self, 'groq_scorer') and self.groq_scorer:
                from core.groq_resume_scorer import GroqResumeScorer
                stats = GroqResumeScorer.get_stats()
                self.lbl_groq_calls.config(text=str(stats.get('total', 0)))
                self.lbl_groq_saved.config(text=f"{stats.get('cached', 0)} calls prevented")
                self.lbl_groq_last.config(text=stats.get('last_time', 'Never'))
            self.root.after(2000, _refresh_stats)  # Refresh every 2 seconds
            
        # Start the refresh loop
        _refresh_stats()

    def update_status(self, message):
        """Update status message and log it"""
        self.logger.info(message)
        self.root.after(0, lambda msg=message: self.status_label.config(text=msg))
        
    def on_closing(self):
        """Handle window closing event to ensure web driver is cleaned up"""
        if self.running:
            if messagebox.askyesno("Confirm Exit", "The bot is currently running. Are you sure you want to stop it and exit?"):
                self.running = False
                self.is_paused = False
                if getattr(self, 'driver', None) is not None:
                    try:
                        self.driver.quit()
                    except Exception:
                        pass
                    self.driver = None
                self.root.destroy()
        else:
            self.root.destroy()
        

class LogTextHandler(logging.Handler):
    """Custom log handler that redirects logs to a tk Text widget"""
    
    def __init__(self, text_widget):
        logging.Handler.__init__(self)
        self.text_widget = text_widget
        
    def emit(self, record):
        msg = self.format(record)
        
        def append_log():
            self.text_widget.config(state="normal")
            self.text_widget.insert("end", msg + "\n")
            self.text_widget.see("end")  # Scroll to the bottom
            self.text_widget.config(state="disabled")
            
        # Schedule the update in the main thread
        self.text_widget.after(0, append_log)


def main():
    root = tk.Tk()
    app = DiceAutoBotApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()
