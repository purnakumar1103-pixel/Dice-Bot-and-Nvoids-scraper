# AI Job Search Automation Suite

An end-to-end job search automation toolkit with two independent bots:

| Bot | What it does |
|---|---|
| **Dice Auto-Apply Bot** | Searches Dice.com and auto-submits applications using AI resume matching |
| **Cold Outreach Pipeline** | Scrapes Nvoids for recruiter job posts and sends personalised cold emails |

Both bots share the same AI stack (Groq LLM + fastembed ONNX semantic matching + SQLite learning memory) and have their own Tkinter GUI so you can run them side-by-side.

[![Watch the Demo Video](https://img.shields.io/badge/Watch-Demo_Video-red?style=for-the-badge&logo=google-drive&logoColor=white)](https://drive.google.com/file/d/1c0Y69PZ5UlFb3dZg0_-_wn7UibRQQwlW/view?usp=sharing)

---

## Table of Contents

1. [Features](#features)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Running the Apps](#running-the-apps)
6. [Dice Auto-Apply Bot — Usage Guide](#dice-auto-apply-bot--usage-guide)
7. [How the Resume Scoring Pipeline Works](#how-the-resume-scoring-pipeline-works)
8. [Cold Outreach Pipeline — Usage Guide](#cold-outreach-pipeline--usage-guide)
9. [How the 3-Layer Dedup System Works](#how-the-3-layer-dedup-system-works)
10. [Project Structure](#project-structure)
11. [Output Files](#output-files)
12. [Troubleshooting](#troubleshooting)

---

## Features

### Dice Auto-Apply Bot

- **AI Semantic Resume Matching** — fastembed ONNX models rank your resumes conceptually against job descriptions (4-10x faster than PyTorch, ~2 GB less RAM)
- **5-Layer Scoring Pipeline** — keyword TF-IDF + name affinity boost + semantic AI score + learning memory boost + must-have filter
- **Parallel Multi-Query Fetch** — searches multiple job titles simultaneously using concurrent headless Chrome sessions with shared login cookies
- **Continual Learning Engine** — SQLite memory records every successful application; profiles with proven results get a Jaccard-similarity-weighted boost on future similar jobs
- **AI Tiebreaker (Groq LLM)** — when two profiles are within 5 points of each other, `llama-3.1-8b-instant` breaks the tie in ~80 ms
- **Native Resume Upload** — PyAutoGUI handles OS-level file dialogs for bulletproof uploads
- **Exclude / Include Keyword Filters** — instantly skip or require specific terms before any AI scoring runs
- **Batch Excel Output** — applied, skipped, and excluded jobs written to `.xlsx` files with skip-reason columns
- **Thread-Safe GUI** — Tkinter UI with live log stream, AI memory stats, progress dashboard, zero freezing
- **Cross-Platform** — Windows / macOS / Linux; auto-detects Brave, Chrome, Edge, Safari, Firefox

### Cold Outreach Pipeline

- **Nvoids Scraper** — headless Chrome crawls Nvoids job listings by role and age, extracting recruiter email, job title, location, and description
- **3-Layer Dedup** — blocks duplicate outreach across URL change, content repost, and time-window cooldown (see [How the 3-Layer Dedup System Works](#how-the-3-layer-dedup-system-works))
- **AI Email Extraction (Groq)** — `llama-3.3-70b-versatile` extracts recruiter emails from noisy job post HTML when none is visible in plain text
- **Smart Template Engine** — fills `{job_title}`, `{location}`, `{keywords}` placeholders automatically; optional Groq AI-generated personalised email body
- **Outlook Web / Gmail** — sends or drafts emails via Outlook Web (browser automation) or Gmail SMTP
- **Daily Cap & Cycle Control** — configurable daily send limit and per-cycle cap so you never spam
- **5-Tab GUI** — Dashboard, Scraper, Templates & AI, Email Config, Dedup management
- **Clear ALL Cooldowns** — one-click wipe of all cooldown and content-fingerprint records from the Dedup tab

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | 3.11 recommended; 3.8 minimum |
| **Web browser** | Brave (recommended), Google Chrome, Edge, or Firefox |
| **Groq API key** | Free at [console.groq.com](https://console.groq.com) — used for LLM tiebreaking and email AI features |
| **Dice.com account** | For the Dice Auto-Apply Bot |
| **Nvoids access** | For the Cold Outreach Pipeline (no login needed) |
| **Outlook / Gmail** | For sending emails via the outreach pipeline |
| **Git** (optional) | Only needed if cloning the repo |

> **Note on Groq:** The free tier is sufficient. The bot uses `llama-3.1-8b-instant` (~80 ms per call) for scoring and `llama-3.3-70b-versatile` only for one-time resume extraction. Retry-After logic handles rate limits automatically.

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/auto-apply-dice-jobs.git
cd auto-apply-dice-jobs
```

### 2. Create a Virtual Environment

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

> This installs all required packages. On Windows, `pywin32` is also installed for Outlook desktop support; on macOS/Linux it is skipped automatically. The first run will also download the AI embedding model (~80 MB) automatically.

### 4. Install fastembed (Recommended — Faster AI)

```bash
pip install fastembed
```

`fastembed` uses ONNX runtime for CPU-only inference — 4-10× faster than PyTorch, ~2 GB less RAM. If you skip this step, the bot gracefully falls back to `sentence-transformers` (requires PyTorch).

### 5. Tkinter (if missing)

Tkinter ships with standard Python on Windows. If you see a `No module named tkinter` error:

```bash
# macOS
brew install python-tk

# Ubuntu / Debian
sudo apt-get install python3-tk
```

---

## Configuration

### Dice Auto-Apply Bot — `config/settings.json`

Open the **Settings** tab in the GUI and fill in these fields, then click **Save Settings**:

| Field | What to put |
|---|---|
| `search_queries` | Job titles to search, e.g. `Data Engineer`, `MLOps Engineer` |
| `include_keywords` | Any job must contain at least one of these (e.g. `AWS`, `Python`, `LLM`) |
| `exclude_keywords` | Jobs containing any of these are instantly skipped (e.g. `Manager`, `W2 only`) |
| `job_application_limit` | Max applications per run (set a safe number like `50` to start) |
| `headless_mode` | `false` = visible browser (recommended while testing), `true` = background |
| `batch_excel_saves` | `true` = write Excel at end of run (faster); `false` = write after every job |
| Email / Password | Your Dice.com login credentials |

You can also edit `config/settings.json` directly with a text editor.

> **Security:** Keep `config/settings.json` out of version control — it contains your Dice password. Add it to `.gitignore`.

### Cold Outreach Pipeline — `config/outreach_settings.json`

Open the **Email Config** and **Scraper** tabs in `outreach_ui.py`:

| Field | What to put |
|---|---|
| `groq_api_key` | Your Groq API key |
| `email_provider` | `outlook_web` or `gmail` |
| `send_mode` | `draft` (save as draft, recommended) or `send` |
| `daily_cap` | Max emails to send/draft per calendar day |
| `cycle_cap` | Max emails per single scrape+send cycle |
| `subject_template` | Subject line with `{job_title}` and `{location}` placeholders |
| `template` | Email body with `{job_title}`, `{location}`, `{keywords}` placeholders |
| `nvoids_queries` | Roles to scrape, format: `Role Name:count` (e.g. `Data Engineer:12, AI Engineer:10`) |
| `nvoids_max_age_hours` | Only process job posts younger than this many hours |
| `cooldown_hours` | Hours before the same recruiter can be emailed again (default: 48) |
| `exclude_keywords` | Skip jobs whose titles contain these terms |
| `cc_email` | Optional CC email address on all outreach emails |
| `gmail_user` / `gmail_app_password` | Gmail credentials (only if using Gmail SMTP) |

> **Security:** `config/outreach_settings.json` contains your API keys and email password. Add it to `.gitignore` and never commit it.

---

## Running the Apps

### Dice Auto-Apply Bot

```bash
# Windows
python run.py

# macOS / Linux
python3 run.py
```

Or launch the GUI directly:
```bash
python app_tkinter.py
```

`run.py` is the recommended entry point — it auto-fixes ChromeDriver permissions before opening the GUI (important on macOS/Linux).

### Cold Outreach Pipeline

```bash
# Windows
python outreach_ui.py

# macOS / Linux
python3 outreach_ui.py
```

Both apps can run simultaneously in separate terminal windows.

---

## Dice Auto-Apply Bot — Usage Guide

The GUI has **5 tabs**: Run Bot, Resumes, Settings, AI Training, Logs.

### Step 1 — Configure Settings

1. Open the **Settings** tab.
2. Enter your **Dice.com email and password**, then click **Test Login** to verify.
3. Add **Job Search Queries** (one per line) — e.g. `Data Engineer`, `MLOps Engineer`.
4. Add **Include Keywords** (job must contain at least one) and **Exclude Keywords** (skip if any match).
5. Set **Max Applications** for this run.
6. Click **Save Settings**.

### Step 2 — Set Up Resume Profiles

The bot supports **multiple resume profiles**. Each profile links a resume file to targeting keywords. The AI engine automatically picks the best-matching resume per job.

**To add a profile:**
1. Go to the **Resumes** tab → click **Add Profile**.
2. Fill in:
   - **Profile Name** — descriptive label matching your resume type (e.g. `Azure Data Engineer`). Used in name-affinity scoring.
   - **Resume File** — click **Browse** to select your `.pdf` or `.docx` resume.
   - **Unique Keywords** — role-specific skills that score at **3× weight** (e.g. `Databricks`, `dbt`, `Unity Catalog`).
   - **General Keywords** — broader skills at **1× weight** (e.g. `Python`, `SQL`, `AWS`).
   - **Boost Mode** — controls how strongly your profile name influences selection:

| Mode | Behaviour |
|---|---|
| `EXACT` | Profile name matches job title ≥ 55% → this resume wins outright (+9,999 pts) |
| `HIGH` | Adds up to 80% of median keyword score as a bonus — strong nudge toward name match |
| `LOW` | Adds up to 20% of median keyword score — soft tiebreaker; keywords still dominate |
| `OFF` | Profile name is ignored entirely — pure keyword + semantic score decides |

3. Click **Save Profile**.

> **Tip:** Add 5–10 profiles targeting your different resume variants. The more specific your Unique Keywords, the better the AI can differentiate between similar roles.

### Step 3 — Test Resume Matching (Dry Run)

Before running the bot live, verify it picks the right resume:

1. Go to the **AI Training** tab.
2. Paste any real Dice job description into the text area.
3. Click **Analyze JD**.
4. Review which profile was selected and why (keyword breakdown + semantic score shown).
5. Click **Approve match** to save it as a training sample, or **Not a fit** to dismiss.

### Step 4 — Train the AI (Optional but Recommended)

The AI Learning Engine builds up a history of which resumes worked for which roles:

- **Automatic** — every successful bot application is recorded to `data/learning_v3.db`.
- **Manual** — paste a JD in the AI Training tab, select the target profile, click **Train AI on this Sample**. Repeat 5–10 times per profile for best results.

The **AI Memory Status** panel shows live training counts per profile (manual vs. auto).

### Step 5 — Run the Bot

1. Go to the **Run Bot** tab.
2. Click **▶ Start**.
3. Watch the **Live Log** panel for real-time updates.
4. Use **⏸ Pause** or **⏹ Stop** any time — the bot responds within seconds.

---

## How the Resume Scoring Pipeline Works

For every job found, all resume profiles are scored through **5 layers**. The highest total score wins:

```
Final Score = Keyword Score + Name Boost + Semantic AI Score + Learning Boost
```

### Layer 1 — Keyword Scoring

The bot scans the job description for every keyword defined in your profiles using a **single-pass compiled regex** (fast — under 5 ms per job). Each match is scored on a log scale to prevent keyword stuffing:

```
Unique keyword hit  →  3.0 × log₂(1 + times_found_in_JD)
General keyword hit →  1.0 × log₂(1 + times_found_in_JD)
```

**Example:**
- `Airflow` (Unique) appears 3 times → `3.0 × log₂(4) = 6.0 pts`
- `Python` (General) appears 5 times → `1.0 × log₂(6) = 2.58 pts`

### Layer 2 — Name Affinity Boost

Compares your profile name against the job title for concept overlap. Effect depends on Boost Mode (see table above).

### Layer 3 — Semantic AI Score

If the fastembed model loaded successfully, the full job description and each resume file are converted to ONNX embedding vectors. Cosine similarity (0–100%) is computed:

```
Semantic bonus = semantic_similarity% × 0.30
```

Vectors are cached both in memory (LRU, 512 entries) and on disk (`data/embedding_cache/`) so each resume and JD is only embedded once across all runs.

> If the model fails to load, the bot falls back to keyword-only scoring — no crash, no skipped jobs.

### Layer 4 — AI Tiebreaker (Groq LLM)

When the top two profiles are within 5 points of each other, `llama-3.1-8b-instant` reads the job description and profile descriptions and picks the better fit. This adds ~80 ms per tied pair and consumes Groq API quota.

### Layer 5 — Learning Boost

Profiles with past successful applications in AI memory receive a Jaccard-similarity-weighted boost:

- The bot tokenises the current job title and all past success titles for that profile.
- Average Jaccard similarity × 15 = boost (max 15 pts).
- Profiles that previously won similar jobs are rewarded proportionally.

**Real-World Example:**

Two profiles competing for `"Data Engineer – Spark/Airflow"`:

| | Data Engineer Profile | ML Engineer Profile |
|---|---|---|
| Keyword Score | 28.5 | 12.1 |
| Name Boost (HIGH) | +18.0 | +2.0 |
| Semantic AI Score | +19.5 | +8.4 |
| Learning Boost | +5.0 | +0.0 |
| **Total** | **71.0 ✅** | **22.5** |

---

## Cold Outreach Pipeline — Usage Guide

The GUI has **5 tabs**: Dashboard, Scraper, Templates & AI, Email Config, Dedup.

### Tab 1 — Dashboard

Shows real-time run statistics:
- Jobs scraped, emails drafted/sent today, remaining daily cap
- Live log stream with timestamps
- Start / Stop / Pause controls

### Tab 2 — Scraper

Configure what to scrape from Nvoids:

- **Queries** — format `Role Name:count` (e.g. `Data Engineer:12, AI Engineer:10`). Each role is searched separately and results are merged.
- **Max Age (hours)** — only process job posts newer than this (default: 12 hours). Prevents re-processing stale listings.
- **Exclude Keywords** — skip any job title containing these terms.
- **Headless** — run scraper browser in the background (check this for production use).

### Tab 3 — Templates & AI

Configure your outreach email:

- **Subject Template** — supports `{job_title}` and `{location}` placeholders.
- **Email Body Template** — supports `{job_title}`, `{location}`, and `{keywords}` (auto-filled from the job description's top matching skills).
- **Resume** — select which resume to attach. Choose **Auto-Match (AI)** to let the AI pick per job using the same 5-layer scoring as the Dice bot.
- **Use AI Email** — if enabled, Groq generates a fully personalised email body per job instead of using the template.

### Tab 4 — Email Config

- **Provider** — `Outlook Web` (browser-based, supports company/personal accounts) or `Gmail` (SMTP).
- **Send Mode** — `Draft` (saves emails as drafts — recommended) or `Send` (sends immediately).
- **Daily Cap** — max emails per calendar day.
- **Cycle Cap** — max emails per single scrape-and-send cycle.
- **CC / BCC** — optional addresses added to every email.
- **Groq API Key** — required for AI email extraction and optional AI-generated email bodies.
- **Cooldown Hours** — how long (hours) before the same recruiter can be emailed again.

### Tab 5 — Dedup

Shows active cooldown records:
- **Refresh** — reload the cooldown table from database.
- **Clear Selected** — remove cooldown for a specific recruiter.
- **Clear ALL Cooldowns** — wipe all cooldown and content-fingerprint records (Layer 1 & 2 only — Layer 0 URL history is preserved).

> Use **Clear ALL Cooldowns** when starting a fresh outreach campaign or after fixing a configuration error that caused bad sends.

---

## How the 3-Layer Dedup System Works

The outreach pipeline prevents duplicate emails through three independent checks, each catching a different type of repeat:

### Layer 0 — URL-Exact Hash

Hash of `email | company | title | location | job_url`. Catches the exact same Nvoids listing re-scraped in a later run. Stored in `sent_jobs` table and **never cleared** (permanent record).

### Layer 1 — Content Fingerprint

Hash of `email | normalized_title | description_snippet[:400]`. Catches the same job reposted with a new URL the next day. Records expire after `content_dup_days` (default: 7 days) so the same recruiter becomes contactable again for a genuinely new posting. Stored in `content_fingerprints`.

### Layer 2 — Recruiter Cooldown

Blocks the same `(recruiter_email, normalized_title)` pair for `cooldown_hours` (default: 48 hours), even if the job description changed. Prevents rapid-fire contact with the same vendor for the same role. Stored in `recruiter_cooldowns`.

**All three layers run in sequence for every job.** A job is skipped if ANY layer flags it as duplicate.

---

## Project Structure

```
auto-apply-dice-jobs/
│
├── run.py                          # Dice bot entry point (fixes ChromeDriver perms on Mac/Linux)
├── app_tkinter.py                  # Dice Auto-Apply Bot — 5-tab Tkinter GUI
├── outreach_ui.py                  # Cold Outreach Pipeline — 5-tab Tkinter GUI
├── requirements.txt                # Python dependencies
│
├── config/
│   ├── settings.json               # Dice bot config (search queries, profiles, credentials)
│   └── outreach_settings.json      # Outreach bot config (email, Groq key, Nvoids queries)
│
├── core/
│   ├── main_script.py              # Dice bot — Selenium automation & application logic
│   ├── matcher.py                  # TF-IDF + Jaccard keyword scoring engine (single-pass regex)
│   ├── semantic_matcher.py         # fastembed ONNX semantic matching with two-level cache
│   ├── learning_engine.py          # SQLite learning memory (WAL mode, persistent connection)
│   ├── groq_resume_scorer.py       # Groq LLM tiebreaker + resume auto-extraction
│   ├── browser_detector.py         # Auto-detects installed browsers
│   ├── dice_login.py               # Dice.com login automation
│   ├── file_utils.py               # PDF / DOCX resume text extractor
│   ├── email_rag.py                # RAG-enhanced email personalisation (optional)
│   ├── resume_keyword_scanner.py   # Scans resume files for keyword matches
│   │
│   └── outreach/
│       ├── outreach_pipeline.py    # Main outreach orchestrator
│       ├── nvoids_scraper.py       # Headless Chrome Nvoids scraper
│       ├── dedup_engine.py         # 3-layer dedup (SQLite WAL, persistent connection)
│       ├── email_engine.py         # Outlook Web / Gmail send/draft engine
│       ├── excel_store.py          # Excel output for outreach results
│       ├── ai_extractor.py         # Groq-powered recruiter email extraction
│       ├── resume_picker.py        # Resume auto-match for outreach
│       ├── scraper_engine.py       # Scraper orchestration layer
│       └── linkedin_xray_scraper.py # LinkedIn X-Ray search (optional)
│
├── utils/
│   ├── config_manager.py           # Settings read/write helper (thread-safe)
│   ├── log_manager.py              # Log file management and rotation
│   ├── timing.py                   # Performance timing utilities
│   └── ui_components.py            # Reusable Tkinter widget components
│
├── resources/
│   ├── app_icon.ico                # Windows app icon
│   ├── app_icon.png                # Cross-platform app icon
│   └── app_icon.icns               # macOS app icon
│
├── data/                           # Auto-created on first run
│   ├── learning_v3.db              # Dice bot AI learning memory
│   ├── outreach_dedup.db           # Outreach dedup database (3-layer)
│   └── embedding_cache/            # Disk cache for ONNX embedding vectors (.npy files)
│
├── logs/                           # Auto-created — session log files
│
├── applied_jobs.xlsx               # Auto-created — successfully applied Dice jobs
├── not_applied_jobs.xlsx           # Auto-created — skipped jobs with reasons
└── excluded_jobs.xlsx              # Auto-created — keyword-excluded jobs
```

> Files and folders marked **Auto-created** do not exist in the repo. The bots create them automatically on first run.

---

## Output Files

### Dice Auto-Apply Bot

| File | Contents |
|---|---|
| `applied_jobs.xlsx` | Every job successfully submitted — title, company, URL, resume used, scores |
| `not_applied_jobs.xlsx` | Skipped jobs — title, company, URL, and the exact skip reason |
| `excluded_jobs.xlsx` | Jobs filtered out by your exclude keywords — shows which keyword triggered exclusion |

### Cold Outreach Pipeline

| File | Contents |
|---|---|
| `outreach_results.xlsx` | All processed jobs — recruiter email, job title, email status, dedup layer that blocked it |

---

## Troubleshooting

### Dice Auto-Apply Bot

**"ChromeDriver not found" or WebDriver error**
- Run via `python run.py` instead of `python app_tkinter.py` — `run.py` auto-fixes driver permissions.
- The bot uses `webdriver-manager` to download ChromeDriver automatically. Ensure you have internet access on first run.

**Bot opens browser but doesn't log in**
- Verify your Dice.com email and password in Settings → Test Login.
- If Dice changed their login page, open an issue — the login selectors may need an update.

**Wrong resume being selected**
- Use the **AI Training** tab to paste sample JDs and verify scoring. Check that your Unique Keywords are specific enough to differentiate profiles.
- Increase Unique Keywords for the profile that should win on that job type.

**"No jobs found" even though Dice shows results**
- Your Include Keywords may be too restrictive. Try broadening them or temporarily removing them.
- Dice sometimes changes their search page structure — check the Logs tab for selector errors.

**Bot skips jobs that have application questions**
- Dice sometimes shows custom application questions (work authorisation, availability, etc.) before the submit button. If the bot can't find or answer these, it logs the job to `not_applied_jobs.xlsx` with a skip reason. This is expected safe behaviour — the bot never submits incomplete applications.
- Check `not_applied_jobs.xlsx` for jobs skipped with reason containing "question" or "form" — you can apply to those manually.

**Bot applies too slowly**
- Enable `headless_mode: true` in Settings — runs the browser in the background without rendering.
- Reduce the number of search queries if you're getting duplicate jobs across queries.

**fastembed model fails to download**
- Ensure you have ~200 MB of free disk space and internet access.
- The bot falls back to `sentence-transformers` automatically if `fastembed` isn't installed.
- To force fallback: `pip uninstall fastembed` (keyword-only scoring is still very effective).

---

### Cold Outreach Pipeline

**No emails are being scraped**
- Verify your Nvoids queries are correct (format: `Role:count`).
- Increase `nvoids_max_age_hours` if listings are older than your current setting.
- Check the Live Log for scraper errors — Nvoids may have changed their page structure.

**Emails are being blocked by Layer 0/1/2 dedup**
- Check the **Dedup** tab to see which cooldown records are active.
- If you need to re-contact recruiters, click **Clear ALL Cooldowns** (Layer 0 history is preserved).
- Reduce `cooldown_hours` in Email Config if 48 hours is too long for your workflow.

**Outlook Web email not sending**
- Ensure Outlook is open in your browser and you're logged in before starting the pipeline.
- Try `send_mode: draft` first to verify emails are being created correctly.

**Groq API rate limit errors**
- The bot includes Retry-After logic — it reads the `retry-after` header and waits the exact number of seconds before retrying on the same key.
- If you have multiple Groq API keys, add them to the settings; the bot rotates through them automatically.

**"Daily cap reached" — emails stop early**
- Increase `daily_cap` in Email Config.
- The cap resets at midnight (local time).

---

### General

**Python module not found**
- Make sure you activated your virtual environment: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (macOS/Linux).
- Re-run `pip install -r requirements.txt`.

**Application runs but GUI doesn't open**
- Tkinter may not be installed. See [Installation — Tkinter](#5-tkinter-if-missing).

**Config file resets on every run**
- Make sure you click **Save Settings** / **Save** in the respective tab. Settings are only persisted when you explicitly save.

**How do I add a second Groq API key?**
- In `config/outreach_settings.json`, set `groq_api_key` to your primary key. The bot supports key rotation — add additional keys as `groq_api_keys` (a list) and the bot rotates through them on rate limit errors.

---

## Browser Support

The Dice bot auto-detects installed browsers in this preference order:

1. Brave Browser (recommended — best compatibility)
2. Google Chrome
3. Microsoft Edge
4. Firefox
5. Safari (macOS only)

---

## Security Notes

- **Never commit** `config/settings.json` or `config/outreach_settings.json` — they contain passwords and API keys. Add both to `.gitignore`.
- The SQLite databases in `data/` contain your application history and recruiter contact records. Back them up if you want to preserve your dedup history across machines.
- Resume file paths in `settings.json` are absolute paths local to your machine — they won't resolve on other machines.

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you would like to change.

## Support

If you find this project useful, please consider supporting its development:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/yuvarajareddy)
