"""
OutreachUI

All heavy imports (selenium, pipeline, scraper) are deferred to the
functions that actually need them so the GUI window opens instantly.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from utils.ui_components import LabeledToggleSwitch
import json
import os
import re
import threading
import traceback
from datetime import datetime

# Heavy imports (selenium, torch, pipeline) are intentionally deferred
# inside the methods that use them so the GUI opens instantly.
from utils.config_manager import ConfigManager


class OutreachUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Career-Ops  ·  Cold Outreach Pipeline")
        self.root.resizable(True, True)

        # ── Unified dark color palette ─────────────────────────────────
        BG       = "#1e2130"   # main app background
        PANEL    = "#252b3b"   # frame / panel background
        CARD_B   = "#1a3a5c"   # blue stat card
        CARD_G   = "#1a3a2a"   # green stat card
        CARD_Y   = "#3a2e10"   # amber stat card
        BORDER   = "#3d4a6a"   # widget borders
        FG       = "#e8eaf6"   # primary text
        FG2      = "#9aa3c2"   # secondary / label text
        ENTRY_BG = "#2d3550"   # entry field bg
        self._theme = dict(BG=BG, PANEL=PANEL, BORDER=BORDER, FG=FG, FG2=FG2,
                           ENTRY_BG=ENTRY_BG, CARD_B=CARD_B, CARD_G=CARD_G, CARD_Y=CARD_Y)

        self.root.configure(bg=BG)

        style = ttk.Style()
        style.theme_use('clam')

        BF  = ("Segoe UI", 10)
        BBF = ("Segoe UI", 10, "bold")

        style.configure(".",              font=BF, background=PANEL, foreground=FG)
        style.configure("TFrame",         background=PANEL)
        style.configure("TLabel",         font=BF,  background=PANEL, foreground=FG, padding=2)
        style.configure("TCheckbutton",   background=PANEL, foreground=FG, font=BF)
        style.configure("TEntry",         fieldbackground=ENTRY_BG, foreground=FG,
                        insertcolor=FG, padding=5)
        style.configure("TCombobox",      fieldbackground=ENTRY_BG, foreground=FG,
                        background=PANEL, padding=4)
        style.map("TCombobox",            fieldbackground=[("readonly", ENTRY_BG)],
                  foreground=[("readonly", FG)])
        style.configure("TLabelframe",    background=PANEL, relief="groove")
        style.configure("TLabelframe.Label", font=BBF, background=PANEL, foreground="#7dd3fc")
        style.configure("TNotebook",      background=BG, tabmargins=[2, 4, 2, 0])
        style.configure("TNotebook.Tab",  padding=[24, 9], font=BBF,
                        background="#252b3b", foreground=FG2)
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL)],
                  foreground=[("selected", FG)])
        style.configure("TScrollbar",     background=BORDER, troughcolor=PANEL)
        style.configure("TCheckbutton",   background=PANEL, foreground=FG, font=("Segoe UI", 10),
                        indicatorsize=18, indicatormargin=5)
        style.map("TCheckbutton",         background=[("active", PANEL)])

        # Named button styles
        for name, bg, abg in [
            ("Start",   "#16a34a", "#15803d"),
            ("Stop",    "#dc2626", "#b91c1c"),
            ("Blue",    "#2563eb", "#1d4ed8"),
            ("Amber",   "#d97706", "#b45309"),
            ("Skip",    "#7c3aed", "#6d28d9"),
        ]:
            style.configure(f"{name}.TButton",
                background=bg, foreground="white",
                font=("Segoe UI", 10, "bold"), padding=(10, 6), relief="flat")
            style.map(f"{name}.TButton",
                background=[("active", abg), ("disabled", "#374151")])

        self.settings_file = "config/outreach_settings.json"
        self.cm      = ConfigManager()
        self.profiles = self.cm.get("resume_profiles", [])

        self.settings = self._load_settings()

        # Pipeline cache — rebuilt only when key settings change
        self._pipeline = None
        self._pipeline_key = None

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = min(int(sw * 0.88), 1280)
        win_h = min(int(sh * 0.88), 900)
        win_w = max(win_w, 900)
        win_h = max(win_h, 640)
        x_pos = max(0, (sw - win_w) // 2)
        y_pos = max(0, (sh - win_h) // 2)

        saved_geometry = self.settings.get("window_geometry", "")
        if saved_geometry:
            try:
                import re
                m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", saved_geometry)
                if m:
                    w, h = int(m.group(1)), int(m.group(2))
                    x, y = int(m.group(3)), int(m.group(4))
                    w = min(max(w, 900), sw)
                    h = min(max(h, 640), sh)
                    if x < -w // 2 or x > sw - 40 or y < -20 or y > sh - 40:
                        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2)
                    self.root.geometry(f"{w}x{h}+{x}+{y}")
                else:
                    self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
            except Exception:
                self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        else:
            self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        self.root.minsize(900, 640)
            
        self._build_widgets()
        
        self.on_provider_change()
        
        # ── Auto-Save Bindings ──
        for var in [
            self.provider_var, self.mode_var, self.target_resume_var,
            self.gmail_font_family_var, self.gmail_font_size_var,
            self.zoho_user_var, self.zoho_pwd_var, self.zoho_domain_var,
            self.cc_var, self.bcc_var, self.groq_key_var,
            self.use_ai_email_var, self.subject_template_var,
            self.nvoids_queries_var, self.nvoids_limit_var, self.nvoids_max_age_var,
            self.continuous_var, self.my_core_skills_var, self.min_match_score_var,
            self.rest_time_var, self.exclude_keywords_var, self.headless_var,
            self.cooldown_hours_var, self.always_accept_titles_var
        ]:
            var.trace_add("write", self._queue_save)
            
        self.template_text.bind("<<Modified>>", lambda e: self._on_template_modified(e))

    def _on_template_modified(self, event):
        if self.template_text.edit_modified():
            self._queue_save()
            self.template_text.edit_modified(False)



    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_settings(self) -> dict:
        defaults = {
            "email_provider":    "outlook",
            "send_mode":         "draft",
            "target_resume":     "Auto-Match (AI)",
            "headless":          False,
            "gmail_font_family": "Arial",
            "gmail_font_size":   "14px",
            "zoho_user":         "",
            "zoho_app_password": "",
            "zoho_domain":       "zoho.com",
            "cc_email":          "",
            "bcc_email":         "",
            "groq_api_key":      "",
            "nvoids_queries":    "Data Engineer, AI Engineer",
            "nvoids_limit":      10,
            "nvoids_max_age_hours": 24,
            "subject_template":  "Application for {job_title}",
            "my_core_skills":    "Python, AWS, PyTorch, SQL, Spark",
            "always_accept_titles": "",
            "min_match_score":   30,
            "cooldown_hours":    48,
            "template": (
                "Hi {recruiter_name},\n\n"
                "I came across your listing for the {job_title} role at "
                "{company_name} and believe my background aligns well with "
                "what you are looking for. Please find my resume attached "
                "for your consideration.\n\n"
                "Best regards,\n[Your Name]"
            ),
        }
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Merge: saved values win, but preserve all defaults if key missing
            for k, v in defaults.items():
                saved.setdefault(k, v)
            return saved
        except Exception:
            return defaults

    
    def _queue_save(self, *args):
        if hasattr(self, "_save_timer"):
            self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(800, self._save_settings)

    def _save_settings(self):
        """Read all widget values into self.settings and write to disk."""
        self.settings["email_provider"]     = self.provider_var.get()
        self.settings["send_mode"]          = self.mode_var.get()
        self.settings["target_resume"]      = self.target_resume_var.get()
        self.settings["gmail_font_family"]  = self.gmail_font_family_var.get()
        self.settings["gmail_font_size"]    = self.gmail_font_size_var.get()
        self.settings["zoho_user"]          = self.zoho_user_var.get()
        self.settings["zoho_app_password"]  = self.zoho_pwd_var.get()
        self.settings["zoho_domain"]        = self.zoho_domain_var.get()
        self.settings["cc_email"]           = self.cc_var.get()
        self.settings["bcc_email"]          = self.bcc_var.get()
        self.settings["groq_api_key"]       = self.groq_key_var.get()
        self.settings["use_ai_email"]       = getattr(self, "use_ai_email_var", tk.BooleanVar()).get()
        self.settings["subject_template"]   = self.subject_template_var.get()
        self.settings["nvoids_queries"]     = self.nvoids_queries_var.get()
        try:
            self.settings["nvoids_limit"]   = int(self.nvoids_limit_var.get() or 10)
        except ValueError:
            self.settings["nvoids_limit"]   = 10
            
        try:
            self.settings["nvoids_max_age_hours"] = int(self.nvoids_max_age_var.get() or 24)
        except ValueError:
            self.settings["nvoids_max_age_hours"] = 24
        
        self.settings["continuous_mode"] = self.continuous_var.get()
        self.settings["my_core_skills"] = self.my_core_skills_var.get()
        
        try:
            self.settings["min_match_score"] = int(self.min_match_score_var.get() or 0)
        except ValueError:
            self.settings["min_match_score"] = 0
        
        try:
            self.settings["rest_time_mins"] = int(self.rest_time_var.get() or 5)
        except ValueError:
            self.settings["rest_time_mins"] = 5

        self.settings["exclude_keywords"] = self.exclude_keywords_var.get()
        self.settings["always_accept_titles"] = self.always_accept_titles_var.get()
        self.settings["headless"] = self.headless_var.get()

        try:
            self.settings["cooldown_hours"] = int(self.cooldown_hours_var.get() or 48)
        except ValueError:
            self.settings["cooldown_hours"] = 48

        raw_template = self.template_text.get("1.0", tk.END).strip()
        # Strip accidental "Subject: …\n\n" prefix that crept into older configs
        if raw_template.lower().startswith("subject:"):
            blank = raw_template.find("\n\n")
            if blank != -1:
                raw_template = raw_template[blank + 2:].strip()
        self.settings["template"] = raw_template
        
        try:
            self.settings["window_geometry"] = self.root.geometry()
        except Exception:
            pass

        
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=4)

        # ── Real-time Push to live bot instances ──────────────────────────
        if hasattr(self, 'scraper') and self.scraper:
            self.scraper.exclude_keywords = [k.strip().lower() for k in self.settings["exclude_keywords"].split(",") if k.strip()]
            self.scraper._parse_always_accept(self.settings.get("always_accept_titles", ""))
            self.scraper.my_core_skills = [s.strip().lower() for s in self.settings["my_core_skills"].split(",") if s.strip()]
            self.scraper.min_match_score = self.settings["min_match_score"]
            self.scraper.rest_time_mins = self.settings["rest_time_mins"]
            self.scraper.continuous_loop = self.settings["continuous_mode"]
            self.scraper.headless = self.settings["headless"]
            self.scraper.max_hours = self.settings["nvoids_max_age_hours"]
            # Push new queries
            raw_queries = [q.strip() for q in self.settings["nvoids_queries"].split(",") if q.strip()]
            parsed_queries = []
            for q in raw_queries:
                if ":" in q:
                    parts = q.split(":")
                    try:
                        q_limit = int(parts[-1].strip())
                        q_str = ":".join(parts[:-1]).strip()
                        parsed_queries.append((q_str, q_limit))
                    except ValueError:
                        parsed_queries.append((q, self.settings.get("nvoids_limit", 10)))
                else:
                    parsed_queries.append((q, self.settings.get("nvoids_limit", 10)))
            self.scraper.parsed_queries = parsed_queries

        if hasattr(self, 'pipeline') and self.pipeline:
            self.pipeline.config = self.settings

    def _get_pipeline(self):
        """Return a cached OutreachPipeline; rebuild only when key settings change."""
        from core.outreach.outreach_pipeline import OutreachPipeline
        key = (
            self.settings.get("email_provider", ""),
            self.settings.get("target_resume", ""),
            self.settings.get("groq_api_key", ""),
            id(self.profiles),
        )
        if self._pipeline is None or self._pipeline_key != key:
            self.log_ui("⚙ Initializing pipeline…")
            self._pipeline = OutreachPipeline(self.settings, self.profiles)
            self._pipeline_key = key
        else:
            self.log_ui("♻ Reusing existing pipeline (no settings changed)…")
            if hasattr(self._pipeline, 'reset_runtime_state'):
                self._pipeline.reset_runtime_state()
        self._pipeline.email_engine.log = self.log_ui
        return self._pipeline

    def save_settings_ui(self):
        """Save settings explicitly and show confirmation to the user."""
        self._save_settings()
        messagebox.showinfo("Settings Saved", "Your outreach settings have been saved successfully.")
        self.log_ui("✅ Settings saved successfully.")


    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_widgets(self):

        t = self._theme
        BG, PANEL = t["BG"], t["PANEL"]
        FG, FG2   = t["FG"], t["FG2"]
        BORDER    = t["BORDER"]
        ENTRY_BG  = t["ENTRY_BG"]

        # ── Dark Header Banner ────────────────────────────────────────
        header = tk.Frame(self.root, bg=BG, height=58)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="🎯  Career-Ops  ·  Cold Outreach Pipeline",
                 bg=BG, fg="#93c5fd",
                 font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT, padx=18, pady=12)
        self.status_var = tk.StringVar(value="◉  Idle")
        self.status_lbl = tk.Label(header, textvariable=self.status_var,
                                   bg=BG, fg="#4ade80",
                                   font=("Segoe UI", 11, "bold"))
        self.status_lbl.pack(side=tk.RIGHT, padx=18)

        # ── Main notebook ─────────────────────────────────────────────
        main = ttk.Frame(self.root, padding=(10, 6))
        main.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_dashboard = ttk.Frame(self.notebook, padding=10)
        self.tab_templates = ttk.Frame(self.notebook, padding=10)
        self.tab_config    = ttk.Frame(self.notebook, padding=10)
        self.tab_dedup     = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.tab_dashboard, text="🚀  Dashboard")
        self.notebook.add(self.tab_templates, text="✉️  Templates & AI")
        self.notebook.add(self.tab_config,    text="📧  Email Config")
        self.notebook.add(self.tab_dedup,     text="🛡️  Dedup")

        self._build_dashboard_tab(t, BG, PANEL, FG, FG2, BORDER, ENTRY_BG)
        self._build_templates_tab(t, PANEL, FG, ENTRY_BG)
        self._build_config_tab(t, PANEL, FG, FG2)
        self._build_dedup_tab(t, PANEL, FG, FG2)

    # ------------------------------------------------------------------
    def _build_dashboard_tab(self, t, BG, PANEL, FG, FG2, BORDER, ENTRY_BG):
        db = self.tab_dashboard

        # ── Action Buttons ────────────────────────────────────────────
        btn_frame = ttk.Frame(db)
        btn_frame.pack(fill=tk.X, pady=(0, 8))
        for c in range(6):
            btn_frame.columnconfigure(c, weight=1)

        self.scrape_btn = ttk.Button(btn_frame, text="▶  Scrape & Queue",
                                     command=self.run_nvoids_scraper, style="Start.TButton")
        self.scrape_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=2)

        self.email_btn = ttk.Button(btn_frame, text="✉  Draft Emails",
                                    command=self.run_email_sender, style="Blue.TButton")
        self.email_btn.grid(row=0, column=1, sticky="ew", padx=3, pady=2)

        self.auto_btn = ttk.Button(btn_frame, text="🚀  Auto-Pilot",
                                   command=self.run_auto_pilot, style="Start.TButton")
        self.auto_btn.grid(row=0, column=2, sticky="ew", padx=3, pady=2)

        self.stop_btn = ttk.Button(btn_frame, text="⏹  Stop",
                                   command=self.stop_scraper, state=tk.DISABLED, style="Stop.TButton")
        self.stop_btn.grid(row=0, column=3, sticky="ew", padx=3, pady=2)

        self.pause_btn = ttk.Button(btn_frame, text="⏸  Pause",
                                    command=self.pause_scraper, state=tk.DISABLED, style="Amber.TButton")
        self.pause_btn.grid(row=0, column=4, sticky="ew", padx=3, pady=2)

        self.skip_btn = ttk.Button(btn_frame, text="⏭  Skip Job",
                                   command=self.skip_current_job, state=tk.DISABLED, style="Skip.TButton")
        self.skip_btn.grid(row=0, column=5, sticky="ew", padx=(3, 0), pady=2)

        # ── Stat Cards ────────────────────────────────────────────────
        cards_frame = ttk.Frame(db)
        cards_frame.pack(fill=tk.X, pady=(0, 6))
        for c in range(4):
            cards_frame.columnconfigure(c, weight=1)

        def _card(col, title, init="0", bg="#1a3a5c", fg="#60a5fa", fg2="#93c5fd"):
            f = tk.Frame(cards_frame, bg=bg, bd=0, highlightthickness=1, highlightbackground=BORDER)
            f.grid(row=0, column=col, sticky="ew", padx=5, pady=2)
            tk.Label(f, text=title, bg=bg, fg=fg2, font=("Segoe UI", 8, "bold")).pack(pady=(8, 0))
            lbl = tk.Label(f, text=init, bg=bg, fg=fg, font=("Segoe UI", 22, "bold"))
            lbl.pack(pady=(2, 8))
            return lbl

        self.jobs_scraped_label  = _card(0, "JOBS SCRAPED",  bg=t["CARD_B"], fg="#60a5fa", fg2="#93c5fd")
        self.jobs_pipeline_label = _card(1, "IN PIPELINE",   bg=t["CARD_G"], fg="#4ade80", fg2="#86efac")
        self.jobs_remaining_label= _card(2, "REMAINING",     bg=t["CARD_Y"], fg="#fbbf24", fg2="#fcd34d")
        self.groq_calls_label    = _card(3, "GROQ CALLS",    bg="#1a1a3a",   fg="#a78bfa", fg2="#c4b5fd")

        def _refresh_groq_counter():
            try:
                from core.outreach.ai_extractor import AIExtractor
                count = AIExtractor.get_call_count()
                self.groq_calls_label.config(text=str(count))
                color = "#f87171" if count >= 12960 else ("#fbbf24" if count >= 10000 else "#a78bfa")
                self.groq_calls_label.config(fg=color)
            except Exception:
                pass
            self.root.after(5000, _refresh_groq_counter)
        _refresh_groq_counter()

        # ── Card 1: Search & Skill Match Targets ─────────────────────────
        card1 = ttk.LabelFrame(db, text="🎯  Search & Skill Match Targets")
        card1.pack(fill=tk.X, pady=(0, 6))

        # Row 1: Search Queries, Max Jobs, Max Age
        c1_row1 = ttk.Frame(card1)
        c1_row1.pack(fill=tk.X, padx=10, pady=(6, 4))
        ttk.Label(c1_row1, text="Queries:").pack(side=tk.LEFT, padx=(0, 6))
        self.nvoids_queries_var = tk.StringVar(value=self.settings.get("nvoids_queries", "Data Engineer, AI Engineer"))
        ttk.Entry(c1_row1, textvariable=self.nvoids_queries_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))

        ttk.Label(c1_row1, text="Max Jobs:").pack(side=tk.LEFT, padx=(0, 4))
        self.nvoids_limit_var = tk.StringVar(value=str(self.settings.get("nvoids_limit", 10)))
        ttk.Entry(c1_row1, textvariable=self.nvoids_limit_var, width=6).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(c1_row1, text="Max Age (h):").pack(side=tk.LEFT, padx=(0, 4))
        self.nvoids_max_age_var = tk.StringVar(value=str(self.settings.get("nvoids_max_age_hours", 24)))
        ttk.Combobox(c1_row1, textvariable=self.nvoids_max_age_var, values=["12", "24", "48", "72", "168"], width=5).pack(side=tk.LEFT)

        # Row 2: Core Skills, Scan Resume, Min Match %
        c1_row2 = ttk.Frame(card1)
        c1_row2.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(c1_row2, text="Core Skills:").pack(side=tk.LEFT, padx=(0, 6))
        self.my_core_skills_var = tk.StringVar(value=self.settings.get("my_core_skills", "Python, AWS, PyTorch, SQL, Spark"))
        ttk.Entry(c1_row2, textvariable=self.my_core_skills_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(c1_row2, text="📄 Scan Resume", command=self._auto_scan_resume, style="Blue.TButton").pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(c1_row2, text="Min Match %:").pack(side=tk.LEFT, padx=(0, 4))
        self.min_match_score_var = tk.StringVar(value=str(self.settings.get("min_match_score", 30)))
        ttk.Combobox(c1_row2, textvariable=self.min_match_score_var, values=["0","10","20","30","40","50","60","80"], state="readonly", width=5).pack(side=tk.LEFT)

        # ── Card 2: Rules, Automation & Toolbar ──────────────────────────
        card2 = ttk.LabelFrame(db, text="⚙️  Rules, Automation & Actions")
        card2.pack(fill=tk.X, pady=(0, 6))

        # Row 1: Exclude Words
        c2_row1 = ttk.Frame(card2)
        c2_row1.pack(fill=tk.X, padx=10, pady=(6, 4))
        ttk.Label(c2_row1, text="Exclude Words:").pack(side=tk.LEFT, padx=(0, 6))
        self.exclude_keywords_var = tk.StringVar(value=self.settings.get("exclude_keywords", "No C2C, W2 Only, US Citizen Only, Clearance"))
        ttk.Entry(c2_row1, textvariable=self.exclude_keywords_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Row 2: Always Accept Titles
        c2_row2 = ttk.Frame(card2)
        c2_row2.pack(fill=tk.X, padx=10, pady=(0, 4))
        ttk.Label(c2_row2, text="Always Accept:").pack(side=tk.LEFT, padx=(0, 6))
        self.always_accept_titles_var = tk.StringVar(value=self.settings.get("always_accept_titles", ""))
        ttk.Entry(c2_row2, textvariable=self.always_accept_titles_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        def _load_accept_titles():
            titles = [p.get("name", "").strip() for p in getattr(self, "profiles", []) if p.get("name", "").strip()]
            if titles:
                current = self.always_accept_titles_var.get().strip()
                if current:
                    existing = {t.strip().lower() for t in current.split(",")}
                    new_ones = [t for t in titles if t.lower() not in existing]
                    self.always_accept_titles_var.set(current + (", " + ", ".join(new_ones) if new_ones else ""))
                else:
                    self.always_accept_titles_var.set(", ".join(titles))
                self.log_ui(f"✅ Imported {len(titles)} title(s) into Always Accept.")
            else:
                messagebox.showinfo("No Profiles", "No resume profiles found.")
        ttk.Button(c2_row2, text="📂 Load Profiles", command=_load_accept_titles).pack(side=tk.RIGHT)

        # Row 3: Toggles & Rest Time
        c2_row3 = ttk.Frame(card2)
        c2_row3.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.continuous_var = tk.BooleanVar(value=self.settings.get("continuous_mode", False))
        ck_cont = LabeledToggleSwitch(c2_row3, text="Continuous (24/7)", variable=self.continuous_var, bg=PANEL, fg=FG)
        ck_cont.pack(side=tk.LEFT, padx=(0, 12))

        self.headless_var = tk.BooleanVar(value=self.settings.get("headless", False))
        ck_head = LabeledToggleSwitch(c2_row3, text="👁 Headless", variable=self.headless_var, bg=PANEL, fg=FG)
        ck_head.pack(side=tk.LEFT, padx=(0, 12))

        self.transparent_var = tk.BooleanVar(value=False)
        ck_trans = LabeledToggleSwitch(c2_row3, text="👁 Top", variable=self.transparent_var, command=self._toggle_transparent, bg=PANEL, fg=FG)
        ck_trans.pack(side=tk.LEFT, padx=(0, 4))

        self.transparency_level = tk.DoubleVar(value=0.85)
        ttk.Scale(c2_row3, from_=0.1, to_=1.0, variable=self.transparency_level, orient=tk.HORIZONTAL, command=lambda _: self._toggle_transparent(), length=70).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(c2_row3, text="Rest Window (mins):").pack(side=tk.LEFT, padx=(0, 4))
        self.rest_time_var = tk.StringVar(value=str(self.settings.get("rest_time_mins", 5)))
        ttk.Combobox(c2_row3, textvariable=self.rest_time_var, values=["1","3","5","10","15","30","60"], state="readonly", width=6).pack(side=tk.LEFT)

        # Row 4: Action Toolbar (Separated dedicated button row)
        c2_row4 = ttk.Frame(card2)
        c2_row4.pack(fill=tk.X, padx=10, pady=(4, 6))

        ttk.Button(c2_row4, text="📂 Open Excel",    command=self._open_excel,      style="Blue.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(c2_row4, text="🗑 Clear Logs",    command=self._clear_logs,      style="Amber.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(c2_row4, text="☠ Erase DB",       command=self._clear_excel,     style="Stop.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(c2_row4, text="💾 Save Settings", command=self.save_settings_ui, style="Start.TButton").pack(side=tk.RIGHT)

        # ── Live Log ──────────────────────────────────────────────────
        lf = ttk.LabelFrame(db, text="📋  Live Log")
        lf.pack(fill=tk.BOTH, expand=True, pady=(0, 0))
        self.log_text = tk.Text(lf, state=tk.DISABLED,
                                bg="#0d1117", fg="#c9d1d9",
                                font=("Consolas", 9), wrap=tk.WORD,
                                insertbackground="white", relief="flat")
        sb = ttk.Scrollbar(lf, command=self.log_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------------
    def _build_templates_tab(self, t, PANEL, FG, ENTRY_BG):
        tb = self.tab_templates

        # Action buttons
        top_row = ttk.Frame(tb)
        top_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(top_row, text="💾 Save Settings", command=self.save_settings_ui, style="Start.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_row, text="🧪 Test Email", command=self._test_email, style="Blue.TButton").pack(side=tk.RIGHT)

        # Target Resume
        res_f = ttk.LabelFrame(tb, text="📄  Target Resume")
        res_f.pack(fill=tk.X, pady=(0, 6))
        res_inner = ttk.Frame(res_f)
        res_inner.pack(fill=tk.X, padx=10, pady=8)
        self.target_resume_var = tk.StringVar(value=self.settings.get("target_resume", "Auto-Match (AI)"))
        profile_names = ["Auto-Match (AI)"] + [p.get("name", "") for p in self.profiles]
        ttk.Combobox(res_inner, textvariable=self.target_resume_var,
                     values=profile_names, state="readonly", width=40).pack(side=tk.LEFT)
        ttk.Label(res_inner, text="  ←  Auto-Match uses AI to select the best resume per job",
                  font=("Segoe UI", 8, "italic")).pack(side=tk.LEFT)

        # AI Email toggle
        ai_f = ttk.LabelFrame(tb, text="🤖  AI Email Generation")
        ai_f.pack(fill=tk.X, pady=(0, 6))
        ai_inner = ttk.Frame(ai_f)
        ai_inner.pack(fill=tk.X, padx=10, pady=8)
        self.use_ai_email_var = tk.BooleanVar(value=self.settings.get("use_ai_email", False))
        LabeledToggleSwitch(ai_inner, text="Generate Dynamic Email Body via Groq AI",
                            variable=self.use_ai_email_var, bg=PANEL, fg=FG).pack(side=tk.LEFT)
        ttk.Label(ai_inner, text="  (requires Groq API key in System Config)",
                  font=("Segoe UI", 8, "italic")).pack(side=tk.LEFT)

        # Subject template
        subj_f = ttk.LabelFrame(tb, text="✏️  Email Subject Template")
        subj_f.pack(fill=tk.X, pady=(0, 6))
        subj_inner = ttk.Frame(subj_f)
        subj_inner.pack(fill=tk.X, padx=10, pady=8)
        self.subject_template_var = tk.StringVar(
            value=self.settings.get("subject_template", "Application for {job_title}"))
        ttk.Entry(subj_inner, textvariable=self.subject_template_var).pack(fill=tk.X, expand=True)

        # Template body
        tf = ttk.LabelFrame(tb, text="📝  Email Body  ·  {job_title}  {company_name}  {recruiter_name}  {keywords}  {location}")
        tf.pack(fill=tk.BOTH, expand=True, pady=(0, 0))
        self.template_text = tk.Text(tf, wrap=tk.WORD, font=("Segoe UI", 10),
                                     relief="flat", bg=ENTRY_BG, fg=FG,
                                     insertbackground=FG, padx=8, pady=6)
        ts = ttk.Scrollbar(tf, command=self.template_text.yview)
        ts.pack(side=tk.RIGHT, fill=tk.Y)
        self.template_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.template_text.configure(yscrollcommand=ts.set)
        template_val = self.settings.get("template", "")
        if template_val.lower().startswith("subject:"):
            blank = template_val.find("\n\n")
            if blank != -1:
                template_val = template_val[blank + 2:].strip()
        self.template_text.insert(tk.END, template_val)

    # ------------------------------------------------------------------
    def _build_config_tab(self, t, PANEL, FG, FG2):
        cb = self.tab_config

        # ── Email Provider & Mode ────────────────────────────────────
        ep_f = ttk.LabelFrame(cb, text="📧  Email Provider & Mode")
        ep_f.pack(fill=tk.X, pady=(0, 6))
        ep_inner = ttk.Frame(ep_f)
        ep_inner.pack(fill=tk.X, padx=10, pady=8)

        ttk.Label(ep_inner, text="Provider:").pack(side=tk.LEFT, padx=(0, 6))
        self.provider_var = tk.StringVar(value=self.settings.get("email_provider", "outlook"))
        self.provider_var.trace_add("write", lambda *_: self.on_provider_change())
        ttk.Combobox(ep_inner, textvariable=self.provider_var,
                     values=["outlook", "outlook_web", "gmail", "zoho"], state="readonly", width=14).pack(side=tk.LEFT)

        ttk.Label(ep_inner, text="   Send Mode:").pack(side=tk.LEFT, padx=(0, 6))
        self.mode_var = tk.StringVar(value=self.settings.get("send_mode", "draft"))
        ttk.Combobox(ep_inner, textvariable=self.mode_var,
                     values=["draft", "send"], state="readonly", width=10).pack(side=tk.LEFT)

        # Credential frames (shown/hidden)
        self.gmail_frame = ttk.Frame(cb)
        gl = ttk.Frame(self.gmail_frame)
        gl.pack(fill=tk.X, padx=10, pady=6)
        self.gmail_status_var = tk.StringVar(value="")
        self.gmail_status_label = tk.Label(gl, textvariable=self.gmail_status_var, font=("Segoe UI", 9, "bold"))
        self.gmail_status_label.pack(side=tk.LEFT, padx=(0, 12))
        self.gmail_auth_btn = ttk.Button(gl, text="🔐 Authorize Gmail", command=self._authorize_gmail)
        self.gmail_auth_btn.pack(side=tk.LEFT)

        gl2 = ttk.Frame(self.gmail_frame)
        gl2.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(gl2, text="Font:").pack(side=tk.LEFT, padx=(0, 6))
        self.gmail_font_family_var = tk.StringVar(value=self.settings.get("gmail_font_family", "Arial"))
        ttk.Entry(gl2, textvariable=self.gmail_font_family_var, width=14).pack(side=tk.LEFT)
        ttk.Label(gl2, text="   Size:").pack(side=tk.LEFT, padx=(6, 6))
        self.gmail_font_size_var = tk.StringVar(value=self.settings.get("gmail_font_size", "14px"))
        ttk.Entry(gl2, textvariable=self.gmail_font_size_var, width=8).pack(side=tk.LEFT)

        gl3 = ttk.Frame(self.gmail_frame)
        gl3.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(gl3, text="ℹ One-time setup: create an OAuth 2.0 Client ID (Desktop app) in Google Cloud "
                            "Console, download the JSON, save it as config/gmail_client_secret.json, then "
                            "click 'Authorize Gmail' — a browser window opens once for you to sign in and "
                            "approve access. After that it reconnects automatically, no re-login needed. "
                            "Both Draft and Send modes work via the Gmail API.",
                  font=("Segoe UI", 8, "italic"), wraplength=650, justify="left").pack(side=tk.LEFT)

        self._refresh_gmail_status()

        self.zoho_frame = ttk.Frame(cb)
        zl = ttk.Frame(self.zoho_frame)
        zl.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(zl, text="Zoho Email:").pack(side=tk.LEFT, padx=(0, 6))
        self.zoho_user_var = tk.StringVar(value=self.settings.get("zoho_user", ""))
        ttk.Entry(zl, textvariable=self.zoho_user_var, width=26).pack(side=tk.LEFT)
        ttk.Label(zl, text="   App Password:").pack(side=tk.LEFT, padx=(0, 6))
        self.zoho_pwd_var = tk.StringVar(value=self.settings.get("zoho_app_password", ""))
        ttk.Entry(zl, textvariable=self.zoho_pwd_var, width=22, show="*").pack(side=tk.LEFT)
        ttk.Label(zl, text="   Domain:").pack(side=tk.LEFT, padx=(0, 6))
        self.zoho_domain_var = tk.StringVar(value=self.settings.get("zoho_domain", "zoho.com"))
        ttk.Entry(zl, textvariable=self.zoho_domain_var, width=10).pack(side=tk.LEFT)
        zl2 = ttk.Frame(self.zoho_frame)
        zl2.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(zl2, text="ℹ Use an app-specific password from Zoho Mail → Security → App Passwords. "
                            "Change Domain to zoho.eu / zoho.in / zoho.com.au if your account is on a "
                            "regional data center.",
                  font=("Segoe UI", 8, "italic"), wraplength=650, justify="left").pack(side=tk.LEFT)

        self.outlook_frame = ttk.Frame(cb)
        tk.Label(self.outlook_frame, text="✅  Native Outlook Desktop — no credentials needed.",
                 fg="#4ade80", bg="#14532d",
                 font=("Segoe UI", 9, "bold"), padx=12, pady=8).pack(fill=tk.X, padx=10, pady=4)

        self.outlook_web_frame = ttk.Frame(cb)
        tk.Label(self.outlook_web_frame, text="✅  Chrome / Outlook Web — browser opens once for login.",
                 fg="#93c5fd", bg="#1e3a5f",
                 font=("Segoe UI", 9, "bold"), padx=12, pady=8).pack(fill=tk.X, padx=10, pady=4)

        # ── CC / BCC ─────────────────────────────────────────────────
        cc_f = ttk.LabelFrame(cb, text="📨  CC / BCC")
        cc_f.pack(fill=tk.X, pady=(0, 6))
        cc_inner = ttk.Frame(cc_f)
        cc_inner.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(cc_inner, text="CC:").pack(side=tk.LEFT, padx=(0, 6))
        self.cc_var = tk.StringVar(value=self.settings.get("cc_email", ""))
        ttk.Entry(cc_inner, textvariable=self.cc_var, width=30).pack(side=tk.LEFT)
        ttk.Label(cc_inner, text="   BCC:").pack(side=tk.LEFT, padx=(0, 6))
        self.bcc_var = tk.StringVar(value=self.settings.get("bcc_email", ""))
        ttk.Entry(cc_inner, textvariable=self.bcc_var, width=30).pack(side=tk.LEFT)

        # ── Groq API Key ──────────────────────────────────────────────
        gk_f = ttk.LabelFrame(cb, text="🔑  Groq API Key  (for AI email generation)")
        gk_f.pack(fill=tk.X, pady=(0, 6))
        gk_inner = ttk.Frame(gk_f)
        gk_inner.pack(fill=tk.X, padx=10, pady=8)
        self.groq_key_var = tk.StringVar(value=self.settings.get("groq_api_key", ""))
        ttk.Entry(gk_inner, textvariable=self.groq_key_var, show="*").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(gk_inner, text="  Free at console.groq.com",
                  font=("Segoe UI", 8, "italic")).pack(side=tk.LEFT)

        # Save button
        ttk.Button(cb, text="💾 Save Settings", command=self.save_settings_ui,
                   style="Start.TButton").pack(fill=tk.X, pady=(8, 0))

        # row1 alias needed by on_provider_change
        self.row1 = ep_inner


    # ------------------------------------------------------------------
    # Provider frame switcher
    # ------------------------------------------------------------------

    def on_provider_change(self, *_):
        """Show the correct credential row for the selected email provider."""
        for f in (self.gmail_frame, self.zoho_frame, self.outlook_frame, self.outlook_web_frame):
            f.pack_forget()

        provider = self.provider_var.get()
        # All frames are children of tab_config — pack after the ep_f LabelFrame
        # The ep_f is always the first child; credential frame goes right after it.
        if provider == "gmail":
            self.gmail_frame.pack(fill=tk.X, pady=(0, 6))
        elif provider == "zoho":
            self.zoho_frame.pack(fill=tk.X, pady=(0, 6))
        elif provider == "outlook_web":
            self.outlook_web_frame.pack(fill=tk.X, pady=(0, 6))
        else:
            self.outlook_frame.pack(fill=tk.X, pady=(0, 6))

    def _refresh_gmail_status(self):
        token_path = os.path.join("config", "gmail_token.json")
        if os.path.exists(token_path):
            self.gmail_status_var.set("✅ Gmail Authorized")
            self.gmail_status_label.config(fg="#4ade80")
        else:
            self.gmail_status_var.set("⚠ Gmail Not Authorized")
            self.gmail_status_label.config(fg="#f59e0b")

    def _authorize_gmail(self):
        """Runs the Gmail OAuth consent flow in a background thread (it opens
        a local browser window and blocks until the user approves access)."""
        client_secret_path = os.path.join("config", "gmail_client_secret.json")
        if not os.path.exists(client_secret_path):
            messagebox.showerror(
                "Missing Client Secret",
                f"Could not find {client_secret_path}.\n\n"
                "Download an OAuth 2.0 Client ID (Desktop app type) JSON from Google Cloud "
                "Console and save it at that exact path first."
            )
            return

        self.gmail_auth_btn.config(state=tk.DISABLED, text="🔐 Authorizing...")
        self.log_ui("▶ Opening browser for Gmail authorization...")

        def _run():
            try:
                from core.outreach.email_engine import EmailEngine
                EmailEngine._gmail_service = None  # force a fresh auth attempt
                eng = EmailEngine(self.settings)
                eng._get_gmail_service()
                self.root.after(0, lambda: self.log_ui("✅ Gmail authorized successfully."))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda: self.log_ui(f"❌ Gmail authorization failed: {err}"))
                self.root.after(0, lambda: messagebox.showerror("Gmail Authorization Failed", err))
            finally:
                self.root.after(0, self._refresh_gmail_status)
                self.root.after(0, lambda: self.gmail_auth_btn.config(state=tk.NORMAL, text="🔐 Authorize Gmail"))

        threading.Thread(target=_run, daemon=True).start()


    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    

    def _clear_logs(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _open_excel(self):
        path = os.path.abspath("data/outreach_jobs.xlsx")
        if os.path.exists(path):
            os.startfile(path)
        else:
            data_dir = os.path.abspath("data")
            backups = [f for f in os.listdir(data_dir) if f.startswith("outreach_jobs_backup") and f.endswith(".xlsx")] if os.path.exists(data_dir) else []
            if backups:
                latest = sorted(backups)[-1]
                os.startfile(os.path.join(data_dir, latest))
                self.log_ui(f"📂 Opened latest backup file: {latest}")
            else:
                self.log_ui("⚠ Excel file not found — run 'Scrape & Queue' first.")

    def _close_all_db_connections(self):
        """Close any active SQLite database handles in pipeline or scraper so files can be deleted cleanly."""
        import gc
        try:
            if hasattr(self, '_pipeline') and self._pipeline:
                if hasattr(self._pipeline, 'stop_worker'):
                    self._pipeline.stop_worker()
                if hasattr(self._pipeline, 'dedup') and self._pipeline.dedup:
                    self._pipeline.dedup.close()
                self._pipeline = None
                self._pipeline_key = None

            if hasattr(self, 'pipeline') and self.pipeline:
                if hasattr(self.pipeline, 'dedup') and self.pipeline.dedup:
                    self.pipeline.dedup.close()
                self.pipeline = None

            if hasattr(self, 'scraper') and self.scraper:
                if hasattr(self.scraper, '_dedup_db') and self.scraper._dedup_db:
                    self.scraper._dedup_db.close()
                self.scraper = None

            gc.collect()
        except Exception as e:
            print(f"[OutreachUI] Error closing DB connections: {e}")

    def _clear_excel(self):
        excel_path = os.path.abspath("data/outreach_jobs.xlsx")
        db_path = os.path.abspath("data/outreach_dedup.db")
        
        if not os.path.exists(excel_path) and not os.path.exists(db_path):
            messagebox.showinfo("Clear Data", "No tracking data exists yet. Nothing to clear.")
            return
            
        if messagebox.askyesno("Confirm Clear", "Are you sure you want to completely delete the outreach_jobs.xlsx file AND the Dedup Tracking Database? This will permanently erase the bot's memory of previously processed jobs."):
            self._close_all_db_connections()
            errs = []
            if os.path.exists(excel_path):
                try:
                    os.remove(excel_path)
                except Exception as e:
                    errs.append(f"Excel ({e})")
            if os.path.exists(db_path):
                try:
                    for p in (db_path, db_path + "-wal", db_path + "-shm"):
                        if os.path.exists(p):
                            os.remove(p)
                except Exception:
                    # Fallback SQL wipe if Windows process lock prevents os.remove
                    try:
                        from core.outreach.dedup_engine import DedupEngine
                        d = DedupEngine()
                        d.wipe_database()
                        d.close()
                    except Exception as e2:
                        errs.append(f"Dedup DB ({e2})")

            if not errs:
                self.log_ui("🗑 Deleted outreach_jobs.xlsx and outreach_dedup.db. Bot memory cleared.")
                messagebox.showinfo("Success", "Data cleared successfully! The bot will now treat all jobs as brand new.")
            else:
                self.log_ui(f"⚠ Memory cleared (with notes: {', '.join(errs)})")
                messagebox.showinfo("Success", "Data cleared successfully!")

    def run_email_sender(self):
        """Process all 'Pending Sending' rows in a background thread."""
        self._save_settings()
        self.log_ui("▶ Loading pipeline…")
        self._set_buttons_state(tk.DISABLED)

        def _run():
            try:
                self.pipeline = self._get_pipeline()
                self.pipeline.process_pending_emails(log_ui=self.log_ui)
            except Exception as e:
                self.log_ui(f"❌ CRITICAL ERROR: {e}\n{traceback.format_exc()}")
            finally:
                self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))

        threading.Thread(target=_run, daemon=True).start()

    def _test_email(self):
        from tkinter import simpledialog
        test_email = simpledialog.askstring("Test Email", "Enter a recipient email address to send a test message to:")
        if not test_email:
            return
            
        self._save_settings()
        self.log_ui(f"▶ Testing email integration to {test_email}...")
        self._set_buttons_state(tk.DISABLED)

        def _run():
            try:
                self.pipeline = self._get_pipeline()

                mock_job = {
                    "Job Title": "Test Software Engineer",
                    "Company": "TestCorp Inc.",
                    "Recruiter Name": "Alex Testing",
                    "Recruiter Email": test_email,
                    "Keywords": "Python, React, SQL",
                    "Location": "Remote"
                }
                
                resume_path = self.pipeline._resolve_resume_path(self.settings.get("target_resume", ""))
                if not resume_path and self.profiles:
                    resume_path = self.profiles[0].get("file_path", "")
                    
                body, subject = self.pipeline._build_email(mock_job, mock_job["Job Title"], mock_job["Company"])
                
                status = self.pipeline.email_engine.send_email(mock_job, resume_path, body, subject)
                self.log_ui(f"✅ Test Email Result: {status}")
                
                if "Error" not in status and "Not Sent" not in status:
                    messagebox.showinfo("Test Success", f"Test email processed successfully!\nResult: {status}")
                else:
                    messagebox.showwarning("Test Failed", f"Email attempt failed or was blocked.\nResult: {status}\n\nCheck Live Log for details.")
            except Exception as e:
                self.log_ui(f"❌ Test Failed: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))

        threading.Thread(target=_run, daemon=True).start()

    def run_nvoids_scraper(self):
        """
        Start the Nvoids scraper in ONE background thread.
        The scraper itself is now synchronous inside that thread.
        Heavy imports (selenium, pipeline) happen inside the thread
        so the UI never blocks.
        """
        self._save_settings()
        self.log_ui("▶ Starting Nvoids scraper…")
        self._set_buttons_state(tk.DISABLED)

        def _run():
            try:
                self.pipeline = self._get_pipeline()

                from core.outreach.nvoids_scraper import NvoidsScraper
                self.scraper  = NvoidsScraper(
                    queries      = self.settings["nvoids_queries"],
                    limit        = self.settings["nvoids_limit"],
                    pipeline     = self.pipeline,
                    skip_email   = True,
                    log_callback = self.log_ui,
                    stats_callback = self.update_scraper_stats,
                    max_age_hours = self.settings.get("nvoids_max_age_hours", 24),
                    continuous_loop = self.settings.get("continuous_mode", False),
                    rest_time_mins = self.settings.get("rest_time_mins", 5),
                    groq_api_key = self.settings.get("groq_api_key", ""),
                    my_core_skills = self.settings.get("my_core_skills", ""),
                    always_accept_titles = self.settings.get("always_accept_titles", ""),
                    min_match_score = self.settings.get("min_match_score", 30),
                    exclude_keywords = self.settings.get("exclude_keywords", ""),
                    headless     = self.settings.get("headless", False),
                    email_recontact_days = self.settings.get("cooldown_hours", 48) / 24,
                )
                self.scraper.start()   # Synchronous in this thread
            except Exception as e:
                self.log_ui(f"❌ SCRAPER ERROR: {e}\n{traceback.format_exc()}")
            finally:
                self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))

        threading.Thread(target=_run, daemon=True).start()

    def run_auto_pilot(self):
        """
        Runs the scraper but executes emails immediately (skip_email=False).
        """
        self._save_settings()
        self.log_ui("🚀 Starting Auto-Pilot (Scrape + Email)...")
        self._set_buttons_state(tk.DISABLED)

        def _run():
            try:
                from core.outreach.nvoids_scraper import NvoidsScraper
                self.pipeline = self._get_pipeline()
                
                self.scraper = NvoidsScraper(
                    queries      = self.settings["nvoids_queries"],
                    limit        = self.settings["nvoids_limit"],
                    pipeline     = self.pipeline,
                    skip_email   = False,
                    log_callback = self.log_ui,
                    stats_callback = self.update_scraper_stats,
                    max_age_hours = self.settings.get("nvoids_max_age_hours", 24),
                    continuous_loop = self.settings.get("continuous_mode", False),
                    rest_time_mins = self.settings.get("rest_time_mins", 5),
                    groq_api_key = self.settings.get("groq_api_key", ""),
                    my_core_skills = self.settings.get("my_core_skills", ""),
                    always_accept_titles = self.settings.get("always_accept_titles", ""),
                    min_match_score = self.settings.get("min_match_score", 30),
                    exclude_keywords = self.settings.get("exclude_keywords", ""),
                    headless     = self.settings.get("headless", False),
                    email_recontact_days = self.settings.get("cooldown_hours", 48) / 24,
                )
                self.scraper.start()
            except Exception as e:
                self.log_ui(f"❌ AUTO-PILOT ERROR: {e}\n{traceback.format_exc()}")
            finally:
                self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))

        threading.Thread(target=_run, daemon=True).start()

    def pause_scraper(self):
        is_paused = False
        
        if hasattr(self, 'scraper') and self.scraper:
            self.scraper.pause_flag = not getattr(self.scraper, 'pause_flag', False)
            is_paused = self.scraper.pause_flag
            
        if hasattr(self, 'pipeline') and self.pipeline:
            self.pipeline.pause_flag = not getattr(self.pipeline, 'pause_flag', False)
            is_paused = self.pipeline.pause_flag or is_paused

        if not is_paused:
            self.pause_btn.config(text="⏸  Pause")
            self.log_ui("▶ Resumed pipeline...")
            self.status_var.set("⬤  Running")
            self.status_lbl.config(fg="#6ee7b7")
        else:
            self.pause_btn.config(text="▶  Resume")
            self.log_ui("⏸ Paused pipeline. Will halt after current action.")
            self.status_var.set("⏸  Paused")
            self.status_lbl.config(fg="#fbbf24")

    def stop_scraper(self):
        self.log_ui("⏹ Stop requested! Will halt after current job finishes...")
        if hasattr(self, 'scraper') and self.scraper:
            self.scraper.stop_flag = True
            self.scraper.pause_flag = False
        if hasattr(self, 'pipeline') and self.pipeline:
            self.pipeline.stop_flag = True
        self.stop_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.DISABLED)
        self.skip_btn.config(state=tk.DISABLED)
        self.status_var.set("⏹  Stopping...")
        self.status_lbl.config(fg="#f87171")

    def skip_current_job(self):
        """Tell the scraper to abandon the current job and move to the next one."""
        skipped = False
        if hasattr(self, 'scraper') and self.scraper:
            self.scraper.skip_flag = True
            skipped = True
        if hasattr(self, 'pipeline') and self.pipeline:
            self.pipeline.skip_flag = True
            skipped = True
            
        if skipped:
            self.log_ui("⏭ Skip requested — will jump to next job after current action.")
            self.status_var.set("⏭  Skipping...")
            self.status_lbl.config(fg="#a78bfa")
        else:
            self.log_ui("⚠ No active process to skip.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _toggle_transparent(self):
        if self.transparent_var.get():
            self.root.attributes("-alpha", self.transparency_level.get())
            self.root.attributes("-topmost", True)
        else:
            self.root.attributes("-alpha", 1.0)
            self.root.attributes("-topmost", False)

    def _blink_ui(self):
        original_bg = self._theme["BG"]
        blink_bg = "#b45309"
        
        def step1(): self.root.configure(bg=blink_bg)
        def step2(): self.root.configure(bg=original_bg)
        def step3(): self.root.configure(bg=blink_bg)
        def step4(): self.root.configure(bg=original_bg)
        
        self.root.after(0, step1)
        self.root.after(300, step2)
        self.root.after(600, step3)
        self.root.after(900, step4)

    def _auto_scan_resume(self):
        """Auto-parse selected PDF/DOCX resume using PyMuPDF / python-docx and extract core tech skills."""
        file_path = filedialog.askopenfilename(
            title="Select Resume File",
            filetypes=[
                ("Resume Files", "*.pdf *.docx *.doc *.txt"),
                ("PDF Documents", "*.pdf"),
                ("Word Documents", "*.docx *.doc"),
                ("All Files", "*.*")
            ]
        )
        if not file_path:
            return

        try:
            ext = os.path.splitext(file_path)[1].lower()
            text = ""
            if ext == ".pdf":
                import fitz
                doc = fitz.open(file_path)
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
            elif ext in (".docx", ".doc"):
                import docx
                doc = docx.Document(file_path)
                text = "\n".join(p.text for p in doc.paragraphs)
            else:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()

            if not text.strip():
                messagebox.showwarning("Empty File", "Could not extract text from the selected file.")
                return

            # Comprehensive tech skill terms catalog (100+ skills across Data, AI, Cloud, Dev)
            TECH_CATALOG = [
                "Python", "AWS", "SQL", "Spark", "PySpark", "Databricks", "Snowflake", "ETL", "ELT",
                "Data Engineering", "Data Pipeline", "Big Data", "Hadoop", "Hive", "Redshift", "Glue",
                "Athena", "EMR", "S3", "Lambda", "Azure", "ADF", "Synapse", "GCP", "BigQuery",
                "PostgreSQL", "MySQL", "MongoDB", "NoSQL", "Redis", "Kafka", "Airflow", "dbt",
                "Docker", "Kubernetes", "K8s", "Terraform", "CI/CD", "Git", "GitHub", "Linux", "Bash",
                "Java", "Scala", "C++", "C#", ".NET", "Go", "Rust", "JavaScript", "TypeScript",
                "React", "Node.js", "FastAPI", "Flask", "Django", "REST API", "GraphQL",
                "PyTorch", "TensorFlow", "Scikit-Learn", "Pandas", "NumPy", "MLOps",
                "GenAI", "Generative AI", "LLM", "RAG", "LangChain", "LlamaIndex", "Vector DB",
                "Pinecone", "Chroma", "Weaviate", "NLP", "Computer Vision", "Machine Learning",
                "Deep Learning", "AI", "Power BI", "Tableau", "Looker", "Excel"
            ]

            # Also harvest candidate skills from loaded profiles if present
            profile_kws = []
            for p in getattr(self, "profiles", []):
                for k in p.get("unique_keywords", []) + p.get("keywords", []):
                    if k.strip() and k.strip().title() not in TECH_CATALOG:
                        profile_kws.append(k.strip().title())

            ALL_SKILLS = TECH_CATALOG + profile_kws
            text_lower = text.lower()
            found_skills = []

            for term in ALL_SKILLS:
                pattern = r'\b' + re.escape(term.lower()).replace(r'\ ', r'\s+') + r'\b'
                if re.search(pattern, text_lower):
                    if term not in found_skills:
                        found_skills.append(term)

            if found_skills:
                skills_str = ", ".join(found_skills[:30])
                self.my_core_skills_var.set(skills_str)
                self.save_settings_ui()
                self.log_ui(f"📄 Auto-scanned '{os.path.basename(file_path)}': extracted {len(found_skills)} skills -> {skills_str}")
                messagebox.showinfo(
                    "Resume Scanned Successfully",
                    f"Successfully extracted {len(found_skills)} tech skills from '{os.path.basename(file_path)}':\n\n{skills_str}"
                )
            else:
                messagebox.showinfo("Resume Scanned", f"Scanned '{os.path.basename(file_path)}', but no standard tech keywords were found.")
        except Exception as e:
            messagebox.showerror("Error Scanning Resume", f"Could not parse resume file:\n{e}")

    def log_ui(self, message: str):
        """Thread-safe append to the log box with timestamp."""
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        print(line, end="")

        def _update():
            if "BLINK_UI_PHONE_DETECTED" in message:
                self._blink_ui()
                return

            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line)
            
            if "(Phone:" in message or "Call Pending" in message:
                self.log_text.tag_add("phone_hi", "end-1l linestart", "end-1l lineend")
                self.log_text.tag_config("phone_hi", background="#eab308", foreground="black", font=("Consolas", 9, "bold"))

            if "Drafting [" in message or "📅 Scraped:" in message:
                self.log_text.tag_add("draft_hi", "end-1l linestart", "end-1l lineend")
                self.log_text.tag_config("draft_hi", background="#1e3a8a", foreground="#93c5fd", font=("Consolas", 9, "bold"))

            if "🎯 Skill Match:" in message or "✅ Skill Match:" in message:
                self.log_text.tag_add("match_hi", "end-1l linestart", "end-1l lineend")
                self.log_text.tag_config("match_hi", background="#065f46", foreground="#a7f3d0", font=("Consolas", 9, "bold"))
                
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        self.root.after(0, _update)

    def update_scraper_stats(self, scraped: int, pipeline: int, remaining: int):
        def _update():
            self.jobs_scraped_label.config(text=str(scraped))
            self.jobs_pipeline_label.config(text=str(pipeline))
            self.jobs_remaining_label.config(text=str(remaining))
        self.root.after(0, _update)

    def _set_buttons_state(self, state):
        self.scrape_btn.config(state=state)
        self.email_btn.config(state=state)
        self.auto_btn.config(state=state)
        if state == tk.NORMAL:
            self.status_var.set("⬤  Idle")
            self.status_lbl.config(fg="#6ee7b7")
            self.stop_btn.config(state=tk.DISABLED)
            self.pause_btn.config(state=tk.DISABLED)
            self.skip_btn.config(state=tk.DISABLED)
            self.pause_btn.config(text="⏸  Pause")
        else:
            self.status_var.set("⬤  Running")
            self.status_lbl.config(fg="#34d399")
            self.stop_btn.config(state=tk.NORMAL)
            self.pause_btn.config(state=tk.NORMAL)
            self.skip_btn.config(state=tk.NORMAL)

    # ------------------------------------------------------------------
    # Dedup tab
    # ------------------------------------------------------------------

    def _build_dedup_tab(self, t, PANEL, FG, FG2):
        """Build the Dedup Controls tab."""
        BORDER = t["BORDER"]
        cb = self.tab_dedup

        dd = ttk.LabelFrame(cb, text="🛡️  Duplicate Suppression Controls")
        dd.pack(fill=tk.X, pady=(0, 8))

        # Row 1 – cooldown hours + refresh button
        row1 = ttk.Frame(dd)
        row1.pack(fill=tk.X, padx=6, pady=(6, 2))

        ttk.Label(row1, text="Cooldown Window (hours):").pack(side=tk.LEFT, padx=(0, 6))
        self.cooldown_hours_var = tk.StringVar(
            value=str(self.settings.get("cooldown_hours", 48))
        )
        ttk.Combobox(
            row1, textvariable=self.cooldown_hours_var,
            values=["12", "24", "48", "72", "168"],
            state="readonly", width=8
        ).pack(side=tk.LEFT)
        ttk.Label(
            row1,
            text="  ← same vendor re-posting same role is blocked for this many hours",
            font=("Segoe UI", 8, "italic"), foreground=FG2
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Button(
            row1, text="🔄 Refresh Status",
            command=self._refresh_cooldown_status,
            style="Blue.TButton"
        ).pack(side=tk.RIGHT, padx=(4, 0))

        # Row 2 – clear cooldown for a specific email
        row2 = ttk.Frame(dd)
        row2.pack(fill=tk.X, padx=6, pady=(2, 4))

        ttk.Label(row2, text="Clear Cooldown for Email:").pack(side=tk.LEFT, padx=(0, 6))
        self.clear_cooldown_email_var = tk.StringVar()
        ttk.Entry(
            row2, textvariable=self.clear_cooldown_email_var, width=36
        ).pack(side=tk.LEFT)
        ttk.Button(
            row2, text="🧹 Clear (All Roles)",
            command=lambda: self._clear_cooldown_for_email(all_roles=True),
            style="Amber.TButton"
        ).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Button(
            row2, text="🗑 Erase Dedup DB",
            command=self._erase_dedup_db,
            style="Stop.TButton"
        ).pack(side=tk.RIGHT, padx=(0, 0))

        # Row 2b – clear ALL cooldowns at once
        row2b = ttk.Frame(dd)
        row2b.pack(fill=tk.X, padx=6, pady=(0, 4))
        ttk.Button(
            row2b, text="⚡ Clear ALL Cooldowns (Layers 1 & 2)",
            command=self._clear_all_cooldowns,
            style="Stop.TButton"
        ).pack(side=tk.LEFT)

        # Row 3 – status display
        status_frame = ttk.LabelFrame(dd, text="Active Cooldowns (last 50)")
        status_frame.pack(fill=tk.X, padx=6, pady=(0, 6))

        self.cooldown_status_text = tk.Text(
            status_frame,
            height=8,
            state=tk.DISABLED,
            bg="#0d1117", fg="#94a3b8",
            font=("Consolas", 8),
            relief="flat",
            wrap=tk.NONE,
        )
        sb = ttk.Scrollbar(status_frame, orient=tk.HORIZONTAL, command=self.cooldown_status_text.xview)
        sb.pack(side=tk.BOTTOM, fill=tk.X, padx=4)
        self.cooldown_status_text.configure(xscrollcommand=sb.set)
        self.cooldown_status_text.pack(fill=tk.X, padx=4, pady=(4, 0))

        # Save button
        ttk.Button(cb, text="💾 Save Settings", command=self.save_settings_ui,
                   style="Start.TButton").pack(fill=tk.X, pady=(8, 0))

        # Auto-refresh every 30 s
        self._schedule_cooldown_refresh()

    def _schedule_cooldown_refresh(self):
        """Auto-refresh cooldown status every 30 seconds."""
        self._refresh_cooldown_status()
        self.root.after(30_000, self._schedule_cooldown_refresh)

    def _refresh_cooldown_status(self):
        """Read active cooldowns from DedupEngine and display them."""
        try:
            from core.outreach.dedup_engine import DedupEngine
            cooldown_hours = int(self.cooldown_hours_var.get() or 48)
            dedup = DedupEngine(cooldown_hours=cooldown_hours)
            records = dedup.get_cooldown_status()

            self.cooldown_status_text.config(state=tk.NORMAL)
            self.cooldown_status_text.delete("1.0", tk.END)

            if not records:
                self.cooldown_status_text.insert(
                    tk.END, "✅  No active cooldowns — all vendors are contactable.\n"
                )
            else:
                header = f"{'EMAIL':<35} {'ROLE':<30} {'SENT':<20} {'HRS LEFT'}\n"
                self.cooldown_status_text.insert(tk.END, header)
                self.cooldown_status_text.insert(tk.END, "-" * 95 + "\n")
                for r in records:
                    line = (
                        f"{r['recruiter_email']:<35} "
                        f"{r['norm_title'][:28]:<30} "
                        f"{r['sent_at'][:16]:<20} "
                        f"{r['hours_remaining']:.1f}h remaining\n"
                    )
                    self.cooldown_status_text.insert(tk.END, line)

            self.cooldown_status_text.config(state=tk.DISABLED)
        except Exception as e:
            pass  # Non-critical UI widget — don't crash

    def _clear_cooldown_for_email(self, all_roles: bool = True):
        """Clear cooldown records for the entered email address."""
        email = self.clear_cooldown_email_var.get().strip()
        if not email:
            messagebox.showwarning(
                "Input Required",
                "Please enter a recruiter email address to clear cooldown for."
            )
            return
        try:
            from core.outreach.dedup_engine import DedupEngine
            cooldown_hours = int(self.cooldown_hours_var.get() or 48)
            dedup = DedupEngine(cooldown_hours=cooldown_hours)
            dedup.clear_cooldown(email, norm_title=None)  # clears all roles for this email
            self.log_ui(f"🧹 Cooldown cleared for: {email} (all roles)")
            messagebox.showinfo(
                "Cooldown Cleared",
                f"Cooldown cleared for {email}.\n"
                f"This vendor will be treated as contactable on the next scrape run."
            )
            self._refresh_cooldown_status()
        except Exception as e:
            messagebox.showerror("Error", f"Could not clear cooldown: {e}")

    def _clear_all_cooldowns(self):
        """Delete every cooldown and content-fingerprint record (Layers 1 & 2)."""
        if not messagebox.askyesno(
            "Clear All Cooldowns",
            "This will remove ALL active cooldowns (Layers 1 & 2) so every vendor\n"
            "becomes immediately re-contactable.\n\n"
            "The sent_jobs history (Layer 0) is NOT affected.\n\nContinue?"
        ):
            return
        try:
            from core.outreach.dedup_engine import DedupEngine
            cooldown_hours = int(self.cooldown_hours_var.get() or 48)
            dedup = DedupEngine(cooldown_hours=cooldown_hours)
            deleted = dedup.clear_all_cooldowns()
            self.log_ui(f"⚡ Cleared {deleted} cooldown record(s) — all vendors are now re-contactable.")
            messagebox.showinfo("Done", f"Cleared {deleted} cooldown record(s).\nAll vendors are now immediately contactable.")
            self._refresh_cooldown_status()
        except Exception as e:
            messagebox.showerror("Error", f"Could not clear cooldowns: {e}")

    def _erase_dedup_db(self):
        """Nuclear option — wipe the entire dedup DB."""
        db_path = os.path.abspath("data/outreach_dedup.db")
        if not os.path.exists(db_path):
            messagebox.showinfo("Erase Dedup DB", "No dedup database found.")
            return
        if messagebox.askyesno(
            "Confirm Erase",
            "This will delete ALL dedup records (sent_jobs, content fingerprints, cooldowns).\n"
            "The bot will treat every vendor as brand-new. Continue?"
        ):
            try:
                self._close_all_db_connections()
                wiped = False
                try:
                    for p in (db_path, db_path + "-wal", db_path + "-shm"):
                        if os.path.exists(p):
                            os.remove(p)
                    wiped = True
                except Exception:
                    from core.outreach.dedup_engine import DedupEngine
                    d = DedupEngine()
                    wiped = d.wipe_database()
                    d.close()

                if wiped:
                    self.log_ui("🗑 Dedup database erased. All vendor history cleared.")
                    self._refresh_cooldown_status()
                    messagebox.showinfo("Done", "Dedup DB erased. The bot starts fresh on next run.")
                else:
                    messagebox.showerror("Error", "Could not erase DB file.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to erase DB: {e}")



if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    app  = OutreachUI(root)
    root.mainloop()

