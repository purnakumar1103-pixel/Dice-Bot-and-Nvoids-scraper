"""
Core Selenium Automation Script
===============================

This module is the heart of the Dice Auto-Apply Bot's interaction with the web browser.
It manages all Selenium WebDriver lifecycles, DOM querying, and automation routines.

Key Capabilities:
-----------------
1. **get_web_driver**: Initializes customized headful or headless Chromium-based 
   browsers with anti-bot circumvention flags and alternative fallbacks.
2. **apply_to_job_url**: The primary job application orchestrator (the 'Wizard'). 
   - Uses Shadow-DOM parsing to navigate complex, React-based Dice components.
   - Detects the presence of Resume inputs and uses either PyAutoGUI OS-level
     hooks or direct DOM injection to upload the mathematically selected resume.
3. **fetch_jobs_with_requests**: Responsible for iterating through search result
   pages and intelligently collecting valid Job IDs while dynamically backing off
   if it encounters captchas or empty states.
"""

import os
import json
import pandas as pd
import platform
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import ElementClickInterceptedException
from dotenv import load_dotenv
import time
import re
import pyautogui
import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from core.matcher import ResumeMatcher
# Try both absolute and relative imports for compatibility
try:
    from dice_auto_apply.core.browser_detector import get_browser_path
    from dice_auto_apply.core.dice_login import login_to_dice
except ImportError:
    try:
        from ..core.browser_detector import get_browser_path
        from ..core.dice_login import login_to_dice
    except ImportError:
        from core.browser_detector import get_browser_path
        from core.dice_login import login_to_dice


# Load environment variables
load_dotenv()

def get_web_driver(headless=False, retry_with_alternative=True):
    """
    Initializes a Selenium WebDriver with fallback options.
    If the primary browser (Brave) fails to load, it will try Chrome as a fallback.
    
    Parameters:
        headless (bool): Whether to use headless mode
        retry_with_alternative (bool): Whether to try alternative browsers if primary fails
        
    Returns:
        WebDriver: Initialized WebDriver instance
    """
    # Fix ChromeDriver permissions first
    try:
        # Import fix_chromedriver
        import sys
        import os
        
        # Add the parent directory to the path to find fix_chromedriver
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
            
        # Run fix_chromedriver
        from fix_chromedriver import fix_chromedriver_permissions
        fix_chromedriver_permissions()
    except Exception as e:
        print(f"Warning: Could not fix ChromeDriver permissions: {e}")


    import platform  # Add this import for system detection
    
    # Get browser path from .env or detect it
    web_browser_path = get_browser_path()
    
    if not web_browser_path:
        raise Exception("Browser path not found in .env file. Please set WEB_BROWSER_PATH.")

    tried_browsers = []
    
    # Try the primary browser first
    try:
        options = Options()
        options.binary_location = web_browser_path

        # Add headless mode options if requested
        if headless:
            options.add_argument("--headless")

        # Enable incognito mode FIRST before other options
        options.add_argument("--incognito")

        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-web-security")
        options.add_argument("--disable-features=EnableEphemeralFlashPermission")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-notifications")

        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Set page load timeout to prevent hanging on slow pages
        driver.set_page_load_timeout(20)

        print(f"Successfully initialized browser: {os.path.basename(web_browser_path)}")
        return driver
        
    except Exception as e:
        tried_browsers.append(os.path.basename(web_browser_path))
        print(f"Error initializing primary browser ({os.path.basename(web_browser_path)}): {e}")
        
        if not retry_with_alternative:
            raise Exception(f"Failed to initialize browser and retry is disabled.")
    
    # If we get here, the primary browser failed - let's try alternatives
    system = platform.system()
    alternative_paths = []
    
    if system == "Darwin":  # macOS
        alternative_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Firefox.app/Contents/MacOS/firefox",
            "/Applications/Safari.app/Contents/MacOS/Safari"
        ]
    elif system == "Windows":
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        alternative_paths = [
            f"{program_files}\\Google\\Chrome\\Application\\chrome.exe",
            f"{program_files_x86}\\Google\\Chrome\\Application\\chrome.exe",
            f"{program_files}\\Mozilla Firefox\\firefox.exe",
            f"{program_files_x86}\\Mozilla Firefox\\firefox.exe"
        ]
    else:  # Linux
        alternative_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/firefox"
        ]
    
    # Try each alternative browser
    for alt_path in alternative_paths:
        if alt_path not in tried_browsers and os.path.exists(alt_path):
            try:
                options = Options()
                options.binary_location = alt_path

                # Enable incognito mode FIRST
                options.add_argument("--incognito")

                if headless:
                    options.add_argument("--headless")

                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920,1080")
                options.add_argument("--disable-blink-features=AutomationControlled")
                
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                driver.set_page_load_timeout(20)
                
                print(f"Successfully initialized alternative browser: {os.path.basename(alt_path)}")
                
                # Update the .env file with working browser
                from dotenv import set_key, find_dotenv
                dotenv_path = find_dotenv()
                if dotenv_path:
                    set_key(dotenv_path, "WEB_BROWSER_PATH", alt_path)
                    print(f"Updated WEB_BROWSER_PATH in .env file to: {alt_path}")
                
                return driver
                
            except Exception as e:
                tried_browsers.append(os.path.basename(alt_path))
                print(f"Error initializing alternative browser ({os.path.basename(alt_path)}): {e}")
    
    # If we get here, all browsers failed
    raise Exception(f"Failed to initialize any browser. Tried: {', '.join(tried_browsers)}")



def apply_to_job_url(driver, job_url, resume_profiles=None, job_title: str = "", semantic_matcher=None, learning_engine=None, groq_scorer=None, pause_check=None, headless_mode=False, **kwargs):
    """
    Applies to a job without opening a new tab, preventing focus stealing.
    Instead navigates to job URL in the same tab and returns to original URL when done.
    Returns: (applied: bool, profile_name: str, match_reason: str, skip_reason: str, job_description: str, profile_id: int)
    """
    if resume_profiles is None:
        resume_profiles = []
        
    # Store current URL to return to later
    original_url = driver.current_url
    
    # Navigate to job URL in the same tab
    driver.get(job_url)
    
    # Read the optional name-boost mode from config
    # Modes: "exact" | "high" | "low" | "off"  (default: "high")
    try:
        from utils.config_manager import ConfigManager
        _cfg = ConfigManager()
        name_boost_mode = str(_cfg.get("profile_name_boost_mode", "off")).strip().lower()
        if name_boost_mode not in ("exact", "high", "low", "off"):
            name_boost_mode = "off"
    except Exception:
        name_boost_mode = "off"

    # Extract job description for resume matching
    selected_resume_path = None
    selected_profile_name = "Default/None"
    selected_profile_id = None
    matched_reason = "No keywords matched or no profiles"
    skip_reason = ""  # Tracks why the job was not applied to
    job_desc_text = ""
    if resume_profiles:
        try:
            time.sleep(1) # Let the page load
            job_desc_element = driver.find_element(By.TAG_NAME, "body")
            job_desc_text = job_desc_element.text
            
            from core.matcher import ResumeMatcher
            matcher = ResumeMatcher(resume_profiles, semantic_matcher=semantic_matcher, learning_engine=learning_engine)
            ranked_results = matcher.score_profiles(
                job_desc_text,
                job_title=job_title,
                name_boost_mode=name_boost_mode
            )
            
            if ranked_results:
                best_match = ranked_results[0]
                selected_resume_path = best_match['file_path']
                selected_profile_name = best_match['name']
                selected_profile_id = best_match.get('id')
                
                u_sc   = best_match['uni_score']
                g_sc   = best_match['gen_score']
                tot    = best_match['score']
                boost  = best_match.get('name_boost', 0.0)
                aff    = best_match.get('name_affinity', 0.0)
                sem_sc = best_match.get('semantic_score', 0)
                l_sc   = best_match.get('learning_boost', 0)
                
                aff_pct = int(aff * 100)
                
                # Retrieve and format the actual keyword matches for the log
                matched_uni = list(best_match.get('matched_uni', set()))
                matched_gen = list(best_match.get('matched_gen', set()))
                all_matched = sorted(matched_uni + matched_gen)
                
                # Limit to first 12 keywords for readability in Excel columns
                kw_summary = ", ".join(all_matched[:12])
                if len(all_matched) > 12:
                    kw_summary += "..."
                
                matched_reason = (f"Score: {tot} (Words: {len(all_matched)}) "
                                  f"| Matching: [{kw_summary}] "
                                  f"| AI: {sem_sc}% | Name: {aff_pct}%")
                
                if l_sc > 0:
                    matched_reason += f" | Learned: +{l_sc}"
                
                print(f"Selected resume: {selected_profile_name} (Reason: {matched_reason})")
        except Exception as e:
            print(f"Could not extract description for resume matching: {e}")
            
    # Dice pages can be slow/heavy; give a bit more time for the apply control to become interactable
    wait = WebDriverWait(driver, 20)
    applied = False
    
    # move pointer to prevent sleeping
    pyautogui.moveRel(1, 1, duration=0.1)
    pyautogui.moveRel(-1, -1, duration=0.1)

    try:
        # Dice UI has evolved multiple times. Current (Feb 2026) uses:
        # - button[data-testid="apply-button"] with text "Apply Now" or "Easy Apply"
        # - Located inside a job-detail-header-card or the older #applyButton container
        #
        # We poll until the button appears and has actionable text.
        max_attempts = 40  # ~20 seconds at 0.5s intervals
        status = None
        apply_kind = None

        for _ in range(max_attempts):
            if pause_check and pause_check():
                driver.get(original_url)
                return False, selected_profile_name, "Stopped by user", "Bot stopped", job_desc_text, selected_profile_id
                
            apply_check = driver.execute_script("""
                // 2026 Dice UI: button with data-testid="apply-button"
                const applyBtn = document.querySelector('button[data-testid="apply-button"]');
                if (applyBtn) {
                    const text = (applyBtn.textContent || '').trim();
                    const disabled = applyBtn.disabled || applyBtn.getAttribute('aria-disabled') === 'true';
                    return { found: true, kind: 'button', text, disabled };
                }

                // 2026 Dice UI variant: anchor <a> with data-testid="apply-button" (can be anywhere in page)
                const applyAnchor = document.querySelector('a[data-testid="apply-button"]');
                if (applyAnchor) {
                    const text = (applyAnchor.textContent || '').trim();
                    const href = applyAnchor.getAttribute('href') || '';
                    const ariaDisabled = applyAnchor.getAttribute('aria-disabled');
                    return { found: true, kind: 'anchor', text, href, ariaDisabled };
                }

                // Legacy: shadow DOM web component
                const applyButtonWc = document.querySelector('apply-button-wc');
                if (applyButtonWc && applyButtonWc.shadowRoot) {
                    const shadowText = applyButtonWc.shadowRoot.textContent || '';
                    if (shadowText.includes('Application Submitted')) {
                        return { found: true, kind: 'shadow', status: 'already_applied' };
                    }
                    if ((shadowText || '').toLowerCase().includes('easy apply') ||
                        (shadowText || '').toLowerCase().includes('apply now') ||
                        (shadowText || '').toLowerCase().includes('apply')) {
                        return { found: true, kind: 'shadow', status: 'can_apply' };
                    }
                    return { found: true, kind: 'shadow', status: 'unknown' };
                }

                return { found: false };
            """)

            if apply_check and apply_check.get("found"):
                apply_kind = apply_check.get("kind")

                if apply_kind == "button":
                    text = (apply_check.get("text") or "").strip()
                    text_l = text.lower()
                    disabled = apply_check.get("disabled", False)

                    if "applied" in text_l or "application submitted" in text_l:
                        status = "already_applied"
                        break

                    if ("apply now" in text_l or "easy apply" in text_l or "apply" in text_l) and not disabled:
                        status = "can_apply"
                        break

                    # Button present but not yet hydrated or still disabled; keep waiting.
                    status = None

                elif apply_kind == "anchor":
                    text = (apply_check.get("text") or "").strip()
                    href = (apply_check.get("href") or "").strip()
                    text_l = text.lower()

                    if "applied" in text_l or "application submitted" in text_l:
                        status = "already_applied"
                        break

                    # Match "Apply", "Easy Apply", "Apply Now", or href pointing to wizard
                    if text_l in ("apply", "easy apply", "apply now") or "apply" in text_l or ("/job-applications/" in href and "/wizard" in href):
                        status = "can_apply"
                        break

                    status = None

                else:
                    shadow_status = apply_check.get("status", "unknown")
                    if shadow_status in {"already_applied", "can_apply"}:
                        status = shadow_status
                        break
                    status = None

            time.sleep(0.5)

        if not status:
            driver.get(original_url)
            return False, selected_profile_name, "Button not found", "Apply button not found or timed out", job_desc_text, selected_profile_id

        if status == "already_applied":
            print(f"Skipping this Job as it is already applied: {job_url}")
            applied = True
            skip_reason = "Already applied previously"

        elif status == "can_apply":
            click_success = False

            if apply_kind == "button":
                # New Dice UI: button[data-testid="apply-button"]
                try:
                    apply_button = wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-testid="apply-button"]'))
                    )
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", apply_button)
                    time.sleep(0.2)
                    try:
                        apply_button.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", apply_button)
                    click_success = True
                except Exception as e:
                    print(f"Failed to click Apply button: {e}")
                    click_success = False

            elif apply_kind == "anchor":
                # Anchor <a> with data-testid="apply-button" (can be anywhere in page)
                try:
                    easy_apply_link = wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 'a[data-testid="apply-button"]'))
                    )
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", easy_apply_link)
                    time.sleep(0.3)
                    try:
                        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'a[data-testid="apply-button"]')))
                        easy_apply_link.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", easy_apply_link)
                    click_success = True
                except Exception as e:
                    print(f"Failed to click Apply link: {e}")
                    click_success = False

            else:
                # Legacy: shadow-DOM web component click
                click_success = driver.execute_script("""
                    const applyButtonWc = document.querySelector('apply-button-wc');
                    if (!applyButtonWc || !applyButtonWc.shadowRoot) return false;

                    const easyApplyBtn =
                        applyButtonWc.shadowRoot.querySelector('button.btn.btn-primary') ||
                        (applyButtonWc.shadowRoot.querySelector('apply-button') &&
                         applyButtonWc.shadowRoot.querySelector('apply-button').shadowRoot &&
                         applyButtonWc.shadowRoot.querySelector('apply-button').shadowRoot.querySelector('button.btn.btn-primary')) ||
                        Array.from(applyButtonWc.shadowRoot.querySelectorAll('button')).find(btn =>
                            (btn.textContent || '').toLowerCase().includes('easy apply') ||
                            (btn.textContent || '').toLowerCase().includes('apply now')
                        );

                    if (!easyApplyBtn) return false;
                    easyApplyBtn.click();
                    return true;
                """)

            if click_success:
                # Dice commonly spawns popup application tabs. We MUST physically shift out of the base job-board tab if this happens!
                # Use a longer wait for slow websites — 4 seconds is safer than 2
                time.sleep(4.0)
                original_window = driver.current_window_handle
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[-1])

                # Continue with the application process
                try:
                    # Dice "Easy Apply" is a multi-step wizard. Keep clicking "Next" until "Submit" appears.
                    next_locator = (
                        By.XPATH,
                        "//button[not(@disabled) and (@type='submit' or @type='button') and "
                        "(normalize-space(.)='Next' or .//span[normalize-space()='Next'])]",
                    )
                    submit_locator = (
                        By.XPATH,
                        "//button[not(@disabled) and (@type='submit' or @type='button') and "
                        "(normalize-space(.)='Submit' or .//span[normalize-space()='Submit'])]",
                    )

                    # Fast polling so we click as soon as buttons appear (avoid long "Submit" waits)
                    # 20s timeout gives slow websites enough time to render each wizard step
                    step_wait = WebDriverWait(driver, 20, poll_frequency=0.3)
                    max_steps = 15
                    submitted = False
                    has_uploaded_resume = False
                    # Guard: how many consecutive times we have been on the resume
                    # step without managing to upload. After MAX_RESUME_RETRIES we
                    # give up and let the wizard continue so it doesn't get stuck.
                    stuck_on_resume_count = 0
                    # NOTE: stuck_on_resume_count is incremented BEFORE this is checked
                    # (see `stuck_on_resume_count += 1` below), so MAX_RESUME_RETRIES=1
                    # previously gave ZERO real retries (1 < 1 is False) — a single
                    # transient upload hiccup would give up instantly and submit with
                    # no resume attached at all. This now allows 3 genuine retries.
                    MAX_RESUME_RETRIES = 4

                    for _ in range(max_steps):
                        if pause_check and pause_check():
                            driver.get(original_url)
                            return False, selected_profile_name, "Stopped by user", "Bot stopped", job_desc_text, selected_profile_id

                        # Detect if the Resume upload section is physically visible on the current wizard step (super fuzzy)
                        is_resume_step = driver.execute_script("""
                            return Array.from(document.querySelectorAll('h1, h2, h3, h4, span, legend, p'))
                                .some(el => {
                                    let t = (el.textContent || '').trim().toLowerCase();
                                    return t.includes('resume & cover') || t === 'resume *' || t === 'resume' || t.includes('upload your resume') || t.includes('add resume');
                                });
                        """)
                        
                        if selected_resume_path and os.path.exists(selected_resume_path) and not has_uploaded_resume and is_resume_step:
                            try:
                                # Intelligently locate the native hidden Resume input, bypassing any blocking Dice UI / OS dialogues natively
                                target_input = driver.execute_script("""
                                    let inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                                    let bestMatch = null;
                                    
                                    // Strip out the Cover Letter input definitively
                                    let candidateInputs = inputs.filter(inp => {
                                        let outer = inp.outerHTML.toLowerCase();
                                        if (outer.includes('cover')) return false;
                                        
                                        // Walk up DOM to catch if it's trapped in a Cover Letter container
                                        let node = inp;
                                        let isCover = false;
                                        for(let i=0; i<10; i++) {
                                            if(node && node.parentElement) {
                                                // Be careful: the entire page might contain "cover letter", so we look for tight bounds.
                                                // Actually, checking its specific ID or name is safer.
                                                if(node.id && node.id.toLowerCase().includes('cover')) isCover = true;
                                                node = node.parentElement;
                                            }
                                        }
                                        return !isCover;
                                    });
                                    
                                    if (candidateInputs.length > 0) {
                                        bestMatch = candidateInputs[0];
                                    }
                                    
                                    // Make sure it is completely unrestricted for Selenium's internal upload
                                    if (bestMatch) {
                                        bestMatch.removeAttribute('disabled');
                                        bestMatch.style.display = 'block';
                                        bestMatch.style.visibility = 'visible';
                                        bestMatch.style.opacity = '1';
                                    }
                                    return bestMatch;
                                """)
                                need_os_dialog_fallback = False
                                if target_input:
                                    try:
                                        print(f"Attempting automatic resume upload via Selenium (works on all platforms)...")
                                        target_input.send_keys(selected_resume_path)
                                        print(f"✓ Uploaded mapped resume automatically: {selected_resume_path}")
                                        has_uploaded_resume = True
                                        time.sleep(2)
                                    except Exception as send_keys_error:
                                        # BUG (fixed): this used to only print "Trying OS file dialog..."
                                        # without actually falling through to that logic below, since it
                                        # lived in the sibling `else` branch (only reachable when
                                        # target_input was never found at all). A send_keys failure on a
                                        # *found* input silently gave up here with no real fallback.
                                        print(f"✗ Automatic upload failed: {send_keys_error}. Trying OS file dialog...")
                                        need_os_dialog_fallback = True
                                else:
                                    need_os_dialog_fallback = True

                                if need_os_dialog_fallback:
                                    # Fallback: Dice entirely pruned the input box from the DOM. We must manually trigger the Windows 'Replace' dialogue.
                                    try:
                                        # Click the '...' menu explicitly scoped to the active resume card
                                        driver.execute_script("""
                                            var headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, span, div, p'))
                                                .filter(el => el.textContent && (el.textContent.trim() === 'Resume *' || el.textContent.trim() === 'Resume'));
                                            if (headers.length > 0) {
                                                var header = headers[0];
                                                var card = header.parentElement;
                                                while (card && card.tagName !== 'BODY') {
                                                    if (card.textContent.includes('Cover letter')) break;
                                                    var contentStr = card.textContent.toLowerCase();
                                                    if (contentStr.includes('uploaded') || contentStr.includes('.pdf') || contentStr.includes('.doc')) {
                                                        var btns = card.querySelectorAll('button');
                                                        if(btns.length > 0) {
                                                            btns[btns.length - 1].click(); // Open the ... menu
                                                            break;
                                                        }
                                                    }
                                                    card = card.parentElement;
                                                }
                                            }
                                        """)
                                        time.sleep(0.5)
                                        # Strictly click 'Replace' to execute the OS-level file prompt
                                        driver.execute_script("""
                                            var items = Array.from(document.querySelectorAll('div, li, span, button, a'))
                                                .filter(el => {
                                                    let t = (el.textContent || '').trim();
                                                    return t === 'Replace';
                                                });
                                            for (var i=0; i<items.length; i++) {
                                                var rect = items[i].getBoundingClientRect();
                                                if (rect.width > 0 && rect.height > 0) {
                                                    items[i].click();
                                                    break;
                                                }
                                            }
                                        """)
                                        # Wait 2 seconds for the OS file dialog to launch and stabilize
                                        time.sleep(2.0)

                                        # Use cross-platform clipboard to copy the file path
                                        import subprocess
                                        system = platform.system()
                                        try:
                                            if system == "Darwin":  # macOS
                                                process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
                                                process.communicate(selected_resume_path.strip().encode('utf-8'))
                                            elif system == "Windows":
                                                subprocess.run('clip.exe', text=True, input=selected_resume_path.strip())
                                            else:  # Linux
                                                subprocess.run(['xclip', '-selection', 'clipboard'], input=selected_resume_path.strip().encode('utf-8'))
                                        except Exception as clipboard_err:
                                            print(f"Warning: Clipboard copy failed: {clipboard_err}")

                                        time.sleep(1.0)

                                        # Use OS-specific methods for file dialog
                                        # Tracks whether we have real evidence the dialog was driven
                                        # successfully — previously has_uploaded_resume was set to True
                                        # unconditionally below regardless of what happened here, so an
                                        # AppleScript failure would still be reported (and retried-past)
                                        # as a successful upload, submitting the application with the
                                        # wrong resume (or none) attached.
                                        os_dialog_succeeded = True
                                        if system == "Darwin":  # macOS
                                            print("macOS: Automating file dialog with AppleScript...")
                                            import subprocess
                                            # Use osascript to handle the file dialog properly on macOS
                                            escaped_path = selected_resume_path.replace('"', '\\"')
                                            applescript = f'''
                                                tell application "System Events"
                                                    keystroke "g" using {{command down, shift down}}
                                                    delay 1.2
                                                    keystroke "{escaped_path}"
                                                    delay 0.8
                                                    key code 36
                                                    delay 1.0
                                                    key code 36
                                                    delay 1.5
                                                end tell
                                            '''
                                            try:
                                                result = subprocess.run(['osascript', '-e', applescript], timeout=10, capture_output=True, text=True)
                                                if result.returncode == 0:
                                                    print("✓ File dialog automation complete - resume uploading")
                                                else:
                                                    print(f"✗ AppleScript error: {result.stderr}")
                                                    # Fallback: Try pressing Tab + Enter to click Open button
                                                    print("Trying fallback: navigating to Open button...")
                                                    time.sleep(0.5)
                                                    pyautogui.press('tab')
                                                    time.sleep(0.3)
                                                    pyautogui.press('enter')
                                                    os_dialog_succeeded = False
                                            except Exception as apple_err:
                                                print(f"✗ AppleScript failed: {apple_err}")
                                                os_dialog_succeeded = False
                                        else:
                                            # Windows/Linux: Use standard clipboard paste
                                            paste_key = 'ctrl'
                                            print(f"Windows/Linux: Pasting file path...")
                                            pyautogui.hotkey(paste_key, 'v')
                                            time.sleep(0.5)
                                            pyautogui.press('enter')

                                        time.sleep(3.0)  # Wait for file dialog to close and file to upload

                                        if os_dialog_succeeded:
                                            print(f"Uploaded mapped resume via Native OS file dialog: {selected_resume_path}")
                                            has_uploaded_resume = True
                                        else:
                                            print("✗ Native OS file dialog upload could not be confirmed — will retry.")
                                    except Exception as e:
                                        print(f"Native GUI injection fallback failed: {e}")
                            except Exception as e:
                                print(f"Resume upload attempt errored before any method could run: {e}")

                        # If we're on the Resume wizard step and we haven't uploaded our mapped resume, don't skip to Next yet.
                        # We allow up to MAX_RESUME_RETRIES attempts before giving up so the wizard never gets stuck.
                        if is_resume_step and selected_resume_path and os.path.exists(selected_resume_path) and not has_uploaded_resume:
                            stuck_on_resume_count += 1
                            if stuck_on_resume_count < MAX_RESUME_RETRIES:
                                print(f"Waiting to upload resume... attempt {stuck_on_resume_count}/{MAX_RESUME_RETRIES}")
                                time.sleep(1.5)
                                continue
                            else:
                                print(f"Resume upload failed after {MAX_RESUME_RETRIES} attempts — proceeding through wizard without it.")
                                
                        # Check immediately for Submit/Next (no blocking waits that delay Next)
                        submit_candidates = driver.find_elements(*submit_locator)
                        submit_button = next((b for b in submit_candidates if b.is_displayed() and b.is_enabled()), None)
                        if submit_button:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                                submit_button,
                            )
                            time.sleep(0.1)
                            try:
                                submit_button.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", submit_button)
                            submitted = True
                            break

                        next_candidates = driver.find_elements(*next_locator)
                        next_button = next((b for b in next_candidates if b.is_displayed() and b.is_enabled()), None)
                        if next_button:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                                next_button,
                            )
                            time.sleep(0.1)
                            try:
                                next_button.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", next_button)
                        else:
                            # Neither button is ready yet; wait (fast poll) until one becomes clickable.
                            def _ready_button(d):
                                for loc in (submit_locator, next_locator):
                                    try:
                                        el = d.find_element(*loc)
                                        if el.is_displayed() and el.is_enabled():
                                            return el
                                    except Exception:
                                        continue
                                return False

                            step_wait.until(_ready_button)
                            continue

                        # Let the wizard step render/hydrate (staleness isn't always reliable with React).
                        # 2.5s gives React/Angular SPA pages enough time to fully paint the next step
                        # before the top-of-loop is_resume_step check fires.
                        time.sleep(2.5)
                    
                    # If we exhausted all steps without ever clicking Submit, record why
                    if not submitted:
                        skip_reason = "Wizard completed max steps but Submit button was never found"

                    # Always wait a moment after Submit for the page to process the request
                    # before checking for the success card – some sites take 3-5 seconds to respond
                    if submitted:
                        time.sleep(3.0)

                    try:
                        confirmation_wait = WebDriverWait(driver, 30)
                        confirmation_wait.until(
                            EC.presence_of_element_located(
                                (
                                    By.CSS_SELECTOR,
                                    '[data-testid="job-application-success-card"]',
                                )
                            )
                        )
                        print(f"Application confirmed for New Job: {job_url}")
                        applied = True
                    except Exception:
                        # Backwards-compatible fallback for older Dice success banner
                        try:
                            confirmation_wait = WebDriverWait(driver, 15)
                            confirmation_wait.until(
                                EC.presence_of_element_located(
                                    (
                                        By.XPATH,
                                        "//header[contains(@class, 'post-apply-banner')]//h1[contains(text(), 'Application submitted')]",
                                    )
                                )
                            )
                            print(f"Application confirmed for New Job: {job_url}")
                            applied = True
                        except Exception as e:
                            print(f"Could not confirm application submission: {e}")
                            if submitted:
                                # Submit button was clicked but confirmation UI never appeared —
                                # give benefit of the doubt; flag it in the reason for auditing
                                applied = True
                                skip_reason = "Submitted (Submit clicked) but confirmation UI not detected"
                            else:
                                # Submit was never clicked — wizard ran out of steps
                                applied = False
                                skip_reason = "Wizard finished all steps but Submit button was never clicked"
                        
                except Exception as e:
                    applied = False
                    skip_reason = f"Wizard error: {e}"
            else:
                print("Failed to click Easy apply button")
                applied = False
                skip_reason = "Failed to click the Apply button"
        else:
            print(f"Unknown shadow DOM state: {status}")
            applied = False
            skip_reason = f"Unknown apply button state: {status}"
            
    except Exception as e:
        print(f"Error in application process: {e}")
        applied = False
        skip_reason = f"Exception during application: {e}"
        
    # Always cleanly terminate any pop-up tabs and return to the original window/URL mapping
    try:
        if len(driver.window_handles) > 1:
            for handle in driver.window_handles[1:]:
                driver.switch_to.window(handle)
                driver.close()
        driver.switch_to.window(driver.window_handles[0])
    except Exception:
        pass
        
    # Give the browser a moment to fully settle after the application / popup close
    # before navigating away, so any in-flight XHR from the submission can complete.
    time.sleep(2.0)
    driver.get(original_url)
    return applied, selected_profile_name, matched_reason, skip_reason, job_desc_text, selected_profile_id

def fetch_jobs_with_requests(driver, search_query, include_keywords=None, exclude_keywords=None, pause_check=None,
                             emp_type="THIRD_PARTY", posted_date="ONE", work_setting="ALL", easy_apply=False,
                             location="", radius="30", will_sponsor=False, salary_min=""):
    """
    Use the existing browser instance to fetch job listings.
    Accepts job filter parameters to build the appropriate Dice search URL.
    """
    print(f"Fetching jobs for query: {search_query}")

    # Format search parameters for URL
    encoded_query = quote(search_query)

    # Map filter values to Dice URL parameters
    employment_type_map = {
        "ALL": "ALL",
        "FULLTIME": "FULLTIME",
        "CONTRACTS": "CONTRACTS",
        "PARTTIME": "PARTTIME",
        "THIRD_PARTY": "THIRD_PARTY"
    }

    posted_date_map = {
        "ONE": "ONE",
        "SEVEN": "SEVEN",
        "THIRTY": "THIRTY",
        "THREE": "THREE"
    }

    # Get mapped values or use defaults
    emp_type_param = employment_type_map.get(emp_type, "THIRD_PARTY")
    posted_date_param = posted_date_map.get(posted_date, "ONE")

    # Build URL with filters
    base_url = f"https://www.dice.com/jobs?filters.employmentType={emp_type_param}&filters.postedDate={posted_date_param}&q={encoded_query}"

    # Add additional filters if specified
    if location:
        base_url += f"&filters.location={quote(location)}"
    if radius and radius != "30":
        base_url += f"&filters.radius={radius}"
    if will_sponsor:
        base_url += "&filters.willSponsor=true"
    if easy_apply:
        base_url += "&filters.easyApply=true"
    
    included_jobs = []
    excluded_jobs = []
    total_jobs_found = 0
    
    # Create WebDriverWait objects with different timeout values
    short_wait = WebDriverWait(driver, 20)
    medium_wait = WebDriverWait(driver, 60)  # Increased timeout for slow loading
    
    try:
        # First load the initial page
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Loading search results for query: '{search_query}'...")
                driver.get(base_url)
                short_wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Error loading initial page. Retry {attempt+1}/{max_retries}...")
                else:
                    print(f"Failed to load initial page after {max_retries} attempts.")
                    raise e
        
        # Move mouse to prevent system sleeping
        pyautogui.moveRel(1, 1, duration=0.1)
        pyautogui.moveRel(-1, -1, duration=0.1)
        
        # Get total jobs count - use multiple fallback strategies, always default to at least 1 page
        total_pages = 1  # Safe default: always try at least one page
        try:
            print("Looking for job count or job cards...")
            
            # Wait up to 20s for the page to show EITHER the count text OR actual job cards OR body to be present
            def find_page_state(d):
                # Strategy 1: Look for the numerical results count text (multiple possible class names)
                for xpath in [
                    "//p[contains(@class, 'text-neutral-900') and contains(text(), 'results')]",
                    "//p[contains(text(), 'results')]",
                    "//*[contains(@class,'results-count')]",
                    "//*[contains(@data-testid,'results-count')]",
                ]:
                    try:
                        el = d.find_element(By.XPATH, xpath)
                        if el and el.is_displayed() and el.text.strip():
                            return {"type": "count", "element": el}
                    except: pass

                # Strategy 2: Job cards already rendered - great, proceed
                try:
                    cards = d.find_elements(By.CSS_SELECTOR, "div[data-id][data-job-guid]")
                    if cards and len(cards) > 0:
                        return {"type": "cards_only", "element": None}
                except: pass

                # Strategy 3: Page body is loaded - at minimum we can inspect it
                try:
                    body = d.find_element(By.TAG_NAME, "body")
                    if body and body.is_displayed():
                        return {"type": "body_only", "element": None}
                except: pass

                return False

            state_result = short_wait.until(find_page_state)

            if state_result["type"] == "count":
                job_count_element = state_result["element"]
                total_jobs_text = job_count_element.text
                print(f"Found job count text: '{total_jobs_text}'")

                total_jobs_match = re.search(r'([\d,]+)\s+results', total_jobs_text)
                if total_jobs_match:
                    total_jobs = int(total_jobs_match.group(1).replace(',', ''))
                    print(f"Total jobs for query '{search_query}': {total_jobs}")
                    if total_jobs <= 0:
                        print(f"Zero jobs returned for '{search_query}' — skipping.")
                        total_pages = 0
                    else:
                        jobs_per_page = 20
                        total_pages = min(11, (total_jobs + jobs_per_page - 1) // jobs_per_page)
                        print(f"Will process {total_pages} pages ({jobs_per_page} jobs per page)")
                else:
                    print(f"Could not parse job count from '{total_jobs_text}'. Defaulting to 3 pages.")
                    total_pages = 3

            elif state_result["type"] == "cards_only":
                print("Job cards already visible, no count text found. Defaulting to 3 pages.")
                total_pages = 3

            else:
                # body_only or unknown — page loaded but we could not confirm jobs exist; try 1 page
                print(f"Page body loaded but no job count or cards detected yet for '{search_query}'. Will try 1 page.")
                total_pages = 1

        except Exception as e:
            print(f"Could not detect page state for query '{search_query}': {e}. Trying 1 page anyway.")
            total_pages = 1
        
        # Process each page
        for page in range(1, total_pages + 1):
            if pause_check and pause_check():
                print("Fetching jobs stopped by user.")
                break
            
            current_url = base_url if page == 1 else f"{base_url}&page={page}"
            print(f"Processing page {page}/{total_pages}: {current_url}")
            
            if page > 1:  # Only need to navigate if not on first page
                try:
                    print(f"Navigating to page {page} by clicking 'Next'...")
                    clicked = driver.execute_script("""
                        let nextBtns = Array.from(document.querySelectorAll('a, button, span, li')).filter(el => {
                            let text = (el.textContent || '').trim().toLowerCase();
                            let aria = (el.getAttribute('aria-label') || '').toLowerCase();
                            let cls = (el.className || '').toLowerCase();
                            
                            // Prevent picking disabled buttons
                            if (el.disabled || el.getAttribute('aria-disabled') === 'true' || cls.includes('disabled')) return false;
                            
                            if (text === 'next' || text === 'next »' || text === '>') return true;
                            if (aria === 'next' || aria === 'next page') return true;
                            if (cls.includes('pagination-next') || cls.includes('next-page')) return true;
                            return false;
                        });
                        
                        let visible = nextBtns.filter(el => {
                            let rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0 && 
                                   window.getComputedStyle(el).display !== 'none' &&
                                   window.getComputedStyle(el).visibility !== 'hidden';
                        });
                        
                        if (visible.length > 0) {
                            let target = visible[visible.length - 1]; // Pick the last matching element (usually the bottom pagination)
                            if (target.tagName === 'LI' && target.querySelector('a, button')) {
                                target = target.querySelector('a, button');
                            }
                            target.scrollIntoView({block: 'center', inline: 'nearest'});
                            target.click();
                            return true;
                        }
                        return false;
                    """)
                    
                    if not clicked:
                        print("Next button not found. Falling back to URL parameter...")
                        driver.get(current_url)
                        time.sleep(3)
                    else:
                        print("Clicked 'Next' physically on UI!")
                        # Wait for the React/Angular framework to fetch new cards and refresh the DOM
                        time.sleep(4.0)
                        
                except Exception as e:
                    print(f"Error navigating to page {page} (will retry or continue): {e}")
                    continue
            
            # Wait for job cards to appear with a more specific selector based on example
            try:
                print("Waiting for job cards to load...")
                
                # NEW APPROACH: Wait specifically for job cards using data attributes
                short_wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-id][data-job-guid]"))
                )
                
                # Add a small delay to ensure dynamic content is fully rendered
                time.sleep(2)
                
                # Get all job cards using the data-id and data-job-guid attributes
                job_cards = driver.find_elements(By.CSS_SELECTOR, "div[data-id][data-job-guid]")
                
                if not job_cards:
                    print(f"No job cards found on page {page}")
                    continue
                    
                print(f"Found {len(job_cards)} jobs on page {page}")
                
                # Process each job card
                for card_index, card in enumerate(job_cards):
                    if pause_check and pause_check():
                        print("Fetching jobs stopped by user.")
                        return included_jobs, excluded_jobs
                        
                    try:
                        # Get job ID and URL from data attributes
                        job_id = card.get_attribute('data-id')
                        job_guid = card.get_attribute('data-job-guid') 
                        if not job_guid:
                            print(f"Missing job_guid on card {card_index}")
                            continue
                            
                        job_url = f"https://www.dice.com/job-detail/{job_guid}"
                        
                        # Extract job title - using the exact classes from example
                        job_title_element = card.find_element(
                            By.CSS_SELECTOR, 
                            "a[data-testid='job-search-job-detail-link']"
                        )
                        job_title = job_title_element.text.strip() if job_title_element else "Unknown"
                        
                        # Extract company name - using the exact structure from example
                        company_element = card.find_element(
                            By.CSS_SELECTOR, 
                            "a[href*='company-profile'] p"
                        )
                        company_name = company_element.text.strip() if company_element else "Unknown"
                        
                        # Extract location - first text paragraph with the specified class
                        location_elements = card.find_elements(
                            By.CSS_SELECTOR, 
                            "p.text-sm.font-normal.text-zinc-600"
                        )
                        job_location = location_elements[0].text.strip() if location_elements else "Unknown"
                        
                        # Extract employment type from the box with specific ID
                        job_employment_type = "THIRD PARTY"  # Default since we're filtering for contracts
                        try:
                            emp_type_element = card.find_element(
                                By.CSS_SELECTOR, 
                                "p#employmentType-label"
                            )
                            if emp_type_element:
                                job_employment_type = emp_type_element.text.strip()
                        except:
                            # Fallback: look for any box containing "Contract"
                            try:
                                box_elements = card.find_elements(By.CSS_SELECTOR, "div.box p")
                                for element in box_elements:
                                    if "THIRD PARTY" in element.text:
                                        job_employment_type = element.text.strip()
                                        break
                            except:
                                pass
                        
                        # Posted date is always "Today" since we filter for last 24 hours
                        job_posted_date = "Today"
                        
                        # Create job entry
                        job_entry = {
                            "Job Title": job_title,
                            "Job URL": job_url,
                            "Company": company_name,
                            "Location": job_location,
                            "Employment Type": job_employment_type,
                            "Posted Date": job_posted_date,
                            "Applied": False
                        }
                        
                        # ── Hardcoded W2 block ────────────────────────────────────────────
                        # W2-only roles are never wanted. BUT if a job mentions BOTH W2 AND
                        # C2C/Corp-to-Corp it means C2C is also accepted — keep those.
                        W2_PATTERNS = [
                            r'\bW2\b', r'\bW-2\b', r'\bW 2\b',
                            r'\bW2 Only\b', r'\bW2only\b',
                            r'\bFull[- ]?Time\b',   # FT-only postings are not contract
                        ]
                        # These signal that C2C is also accepted — override the W2 block
                        C2C_PATTERNS = [
                            r'\bC2C\b', r'\bCorp[- ]?to[- ]?Corp\b',
                            r'\bContract[- ]?to[- ]?Contract\b',
                            r'\bC2H\b', r'\bContract[- ]?to[- ]?Hire\b',
                        ]
                        try:
                            card_text = card.text  # All visible text in the card
                        except Exception:
                            card_text = ""
                        
                        # Check if C2C is mentioned anywhere in the card
                        all_card_text = f"{job_title} {job_employment_type} {card_text}"
                        has_c2c = any(
                            re.search(p, all_card_text, re.IGNORECASE) for p in C2C_PATTERNS
                        )
                        
                        w2_hit = False
                        w2_hit_reason = ""
                        if not has_c2c:
                            # Only apply the W2 block when C2C is NOT also offered
                            for pat in W2_PATTERNS:
                                for source_text, source_name in [
                                    (job_title, "title"),
                                    (job_employment_type, "employment-type"),
                                    (card_text, "card-body"),
                                ]:
                                    if re.search(pat, source_text, re.IGNORECASE):
                                        w2_hit = True
                                        w2_hit_reason = f"W2-only detected in {source_name}: '{pat}'"
                                        break
                                if w2_hit:
                                    break
                        
                        if w2_hit:
                            job_entry["Exclusion Reason"] = w2_hit_reason
                            excluded_jobs.append(job_entry)
                            print(f"  [W2 SKIP] {job_title} — {w2_hit_reason}")
                            continue
                        elif has_c2c and any(re.search(p, all_card_text, re.IGNORECASE) for p in W2_PATTERNS):
                            # W2 + C2C together — keep it but log it
                            print(f"  [W2+C2C KEEP] {job_title} — C2C also offered, keeping job")
                        # ─────────────────────────────────────────────────────────────────
                        
                        # Apply user-defined keyword filtering
                        include_job = True
                        exclusion_reason = ""

                        # Check exclude keywords (title + employment type + visible card
                        # snippet — was title-only, which let excluded content slip through
                        # whenever the title itself looked generic/harmless)
                        if exclude_keywords:
                            matching_excludes = []
                            for kw in exclude_keywords:
                                pat = ResumeMatcher.build_keyword_pattern(kw)
                                if pat and re.search(pat, all_card_text, re.IGNORECASE):
                                    matching_excludes.append(kw)
                            if matching_excludes:
                                exclusion_reason = f"Contains excluded keywords (title/snippet): {', '.join(matching_excludes)}"
                                include_job = False

                        # Check include keywords (same title+snippet scan as above)
                        if include_keywords and include_job:
                            found_include = False
                            for kw in include_keywords:
                                pat = ResumeMatcher.build_keyword_pattern(kw)
                                if pat and re.search(pat, all_card_text, re.IGNORECASE):
                                    found_include = True
                                    break
                            if not found_include:
                                exclusion_reason = f"Missing required keywords (title/snippet): {', '.join(include_keywords)}"
                                include_job = False
                        
                        if include_job:
                            included_jobs.append(job_entry)
                        else:
                            job_entry["Exclusion Reason"] = exclusion_reason
                            excluded_jobs.append(job_entry)
                    
                    except Exception as e:
                        print(f"Error processing job card {card_index} on page {page}: {str(e)}")
                        continue
                
                total_jobs_found += len(job_cards)
                
            except Exception as e:
                print(f"Error processing job cards on page {page} (timeout reached). Stopping page traversal: {str(e)}")
                break # Stop processing subsequent pages if current page fails or has no cards
                
    except Exception as e:
        print(f"Error during job fetching: {str(e)}")
    
    print(f"Total jobs processed: {total_jobs_found}")
    print(f"Jobs included after filtering: {len(included_jobs)}")
    print(f"Jobs excluded after filtering: {len(excluded_jobs)}")
    
    return included_jobs, excluded_jobs


            

def save_to_excel(job_data, filename="job_application_report.xlsx"):
    """
    Saves job data to an Excel file.
    """
    try:
        df = pd.DataFrame(job_data["jobs"])
        df.to_excel(filename, index=False)
        print(f"Job application report saved to {filename}")
    except Exception as e:
        print(f"Error saving to Excel: {e}")

def main():
    # Record the start time of the entire script
    script_start_time = time.time()
    
    # Disable PyAutoGUI failsafe to prevent accidental triggering
    pyautogui.FAILSAFE = False
    
    driver = get_web_driver()  # Use browser
    
    # Define file names for fresh start
    applied_jobs_file = "applied_jobs.xlsx"
    not_applied_jobs_file = "not_applied_jobs.xlsx"
    job_report_file = "job_application_report.xlsx"
    excluded_jobs_file = "excluded_jobs.xlsx"
 
    # Delete existing files before login to start fresh (excluding applied_jobs.xlsx)
    for file in [not_applied_jobs_file, job_report_file, excluded_jobs_file]:
        if os.path.exists(file):
            os.remove(file)
            
    # Ensure applied_jobs.xlsx exists before writing
    if not os.path.exists(applied_jobs_file):
        df_empty = pd.DataFrame(columns=["Job Title", "Job URL", "Company", "Location", "Employment Type", "Posted Date", "Applied"])
        df_empty.to_excel(applied_jobs_file, index=False)

    job_data = {
        "Total Jobs Posted Today": 0,
        "jobs": []
    }

    try:
        # Record login start time
        login_start_time = time.time()
        
        if login_to_dice(driver):
            login_time = time.time() - login_start_time
            print(f"Login successful in {login_time:.2f} seconds. Starting job search...")

            # Move mouse to prevent system sleeping
            pyautogui.moveRel(1, 1, duration=0.1)
            pyautogui.moveRel(-1, -1, duration=0.1)

            # Use existing driver to fetch jobs
            collected_jobs = {}  # Dictionary to hold unique jobs by URL
            excluded_jobs = []   # List to hold excluded jobs
            fetch_start_time = time.time()
            
            for query in DICE_SEARCH_QUERIES:
                # Pass the existing driver to fetch_jobs_with_requests
                included_jobs, query_excluded_jobs = fetch_jobs_with_requests(driver, query, INCLUDE_KEYWORDS, EXCLUDE_KEYWORDS)
                
                # Add each job to the collected jobs dictionary
                for job in included_jobs:
                    if job["Job URL"] not in collected_jobs:
                        collected_jobs[job["Job URL"]] = job
                
                # Add to excluded jobs list
                excluded_jobs.extend(query_excluded_jobs)
                
                print(f"Query '{query}' returned {len(included_jobs)} jobs")
                
                # Mouse movement between queries to prevent sleep
                pyautogui.moveRel(1, 1, duration=0.1)
                pyautogui.moveRel(-1, -1, duration=0.1)
                
            fetch_time = time.time() - fetch_start_time
            print(f"Finished fetching jobs in {fetch_time:.2f} seconds")

            # Save excluded jobs to Excel
            if excluded_jobs:
                df_excluded = pd.DataFrame(excluded_jobs)
                df_excluded.to_excel(excluded_jobs_file, index=False)
                print(f"Saved {len(excluded_jobs)} excluded jobs to {excluded_jobs_file}")

            # Merge all job details into job_data
            job_data["jobs"] = list(collected_jobs.values())
            print(f"==========> Total unique jobs collected from all queries: {len(job_data['jobs'])}")
            
            # Rest of your code stays the same...
            # Check for already applied jobs
            if not os.path.exists(not_applied_jobs_file):
                df_empty = pd.DataFrame(columns=["Job Title", "Job URL", "Company", "Location", "Employment Type", "Posted Date", "Applied", "Resume Profile", "Match Reason", "Skip Reason"])
                df_empty.to_excel(not_applied_jobs_file, index=False)
                
            existing_applied_jobs = set()
            existing_not_applied_jobs = set()

            if os.path.exists(applied_jobs_file):
                try:
                    df_applied = pd.read_excel(applied_jobs_file)
                    existing_applied_jobs = set(df_applied["Job URL"].dropna())
                except Exception as e:
                    print(f"Error loading existing applied jobs: {e}")

            if os.path.exists(not_applied_jobs_file):
                try:
                    df_not_applied = pd.read_excel(not_applied_jobs_file)
                    existing_not_applied_jobs = set(df_not_applied["Job URL"].dropna())
                except Exception as e:
                    print(f"Error loading not applied jobs: {e}")

            # Count already applied jobs
            already_applied_count = sum(1 for job in job_data["jobs"] if job["Job URL"] in existing_applied_jobs)
            print(f"==========> Skipping jobs that were already applied: {already_applied_count}")

            # Filter jobs before applying
            pending_jobs = [job for job in job_data["jobs"] if job["Job URL"] not in existing_applied_jobs]
            print(f"==========> Total jobs to apply for: {len(pending_jobs)}")
            
            # Calculate and display the estimated time
            print(f"==========> Estimated time to apply all {len(pending_jobs)} jobs: {len(pending_jobs)//8//60} hours {len(pending_jobs)//8%60} minutes")
            
            # Record application start time
            apply_start_time = time.time()
            successful_applications = 0
            failed_applications = 0
            
            # Process only pending jobs
            for job_index, job in enumerate(pending_jobs):
                # Move mouse every 3 jobs to prevent system sleeping
                if job_index % 3 == 0:
                    pyautogui.moveRel(1, 1, duration=0.1)
                    pyautogui.moveRel(-1, -1, duration=0.1)
                
                job_start_time = time.time()
                
                if not job["Applied"] and job["Job URL"] != "Unknown":
                    applied, profile_used, reason, skip_reason = apply_to_job_url(
                        driver, job["Job URL"], [], job_title=job.get("Job Title", "")
                    )
                    job["Applied"] = applied
                    job["Resume Profile"] = profile_used
                    job["Match Reason"] = reason
                    
                    job_time = time.time() - job_start_time
                    
                    if applied:
                        successful_applications += 1
                        # Store any audit note (e.g. submitted but confirmation not detected)
                        if skip_reason:
                            job["Application Note"] = skip_reason
                        try:
                            df_existing = pd.read_excel(applied_jobs_file)
                        except Exception:
                            df_existing = pd.DataFrame(columns=["Job Title", "Job URL", "Company", "Location", "Employment Type", "Posted Date", "Applied", "Resume Profile", "Match Reason", "Application Note"])
                        df_new = pd.DataFrame([job])
                        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                        df_combined.to_excel(applied_jobs_file, index=False)
                    else:
                        failed_applications += 1
                        job["Skip Reason"] = skip_reason
                        try:
                            df_existing = pd.read_excel(not_applied_jobs_file)
                        except Exception:
                            df_existing = pd.DataFrame(columns=["Job Title", "Job URL", "Company", "Location", "Employment Type", "Posted Date", "Applied", "Resume Profile", "Match Reason", "Skip Reason"])
                        df_new = pd.DataFrame([job])
                        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                        df_combined.to_excel(not_applied_jobs_file, index=False)
                    
                    # Print progress every 5 jobs
                    if (job_index + 1) % 5 == 0 or job_index == len(pending_jobs) - 1:
                        elapsed = time.time() - apply_start_time
                        progress = (job_index + 1) / len(pending_jobs) * 100
                        estimated_total = elapsed / (job_index + 1) * len(pending_jobs)
                        remaining = estimated_total - elapsed
                        
                        print(f"Progress: {job_index+1}/{len(pending_jobs)} jobs ({progress:.1f}%) | "
                              f"Last job: {job_time:.1f}s | "
                              f"Success rate: {successful_applications}/{job_index+1} | "
                              f"Est. remaining: {remaining/60:.1f} mins")

            # ── Retry pass ────────────────────────────────────────────────────
            # Re-attempt jobs that failed due to transient issues (button timeout,
            # wizard not finding Submit, etc.).  Only ONE retry per job.
            RETRYABLE_REASONS = [
                "Apply button not found or timed out",
                "Wizard completed max steps but Submit button was never found",
                "Wizard finished all steps but Submit button was never clicked",
                "Failed to click the Apply button",
                "Wizard error",
                "Exception during application",
            ]
            retry_candidates = [
                job for job in pending_jobs
                if not job.get("Applied", False)
                and any(r in job.get("Skip Reason", "") for r in RETRYABLE_REASONS)
            ]

            if retry_candidates:
                print(f"\n==========> Retrying {len(retry_candidates)} failed job(s)...")
                retry_success = 0
                retry_fail    = 0

                for job in retry_candidates:
                    print(f"  Retrying: {job.get('Job Title', 'Unknown')} | Previous reason: {job.get('Skip Reason', '')}")
                    time.sleep(3.0)  # Brief pause before each retry

                    applied, profile_used, reason, skip_reason = apply_to_job_url(
                        driver, job["Job URL"], []
                    )
                    job["Applied"]        = applied
                    job["Resume Profile"] = profile_used
                    job["Match Reason"]   = reason
                    job["Skip Reason"]    = skip_reason if not applied else job.get("Skip Reason", "")

                    if applied:
                        retry_success        += 1
                        successful_applications += 1
                        failed_applications     -= 1
                        # Write to applied_jobs
                        try:
                            df_existing = pd.read_excel(applied_jobs_file)
                        except Exception:
                            df_existing = pd.DataFrame(columns=["Job Title", "Job URL", "Company",
                                "Location", "Employment Type", "Posted Date", "Applied",
                                "Resume Profile", "Match Reason"])
                        df_combined = pd.concat([df_existing, pd.DataFrame([job])], ignore_index=True)
                        df_combined.to_excel(applied_jobs_file, index=False)
                        # Remove from not_applied_jobs if it was written there
                        try:
                            df_not = pd.read_excel(not_applied_jobs_file)
                            df_not = df_not[df_not["Job URL"] != job["Job URL"]]
                            df_not.to_excel(not_applied_jobs_file, index=False)
                        except Exception:
                            pass
                        print(f"    ✓ Retry succeeded for: {job.get('Job Title', 'Unknown')}")
                    else:
                        retry_fail += 1
                        job["Skip Reason"] = f"[Retry] {skip_reason}"
                        # Update the existing not_applied row
                        try:
                            df_not = pd.read_excel(not_applied_jobs_file)
                            if job["Job URL"] in df_not["Job URL"].values:
                                df_not.loc[df_not["Job URL"] == job["Job URL"], "Skip Reason"] = job["Skip Reason"]
                            else:
                                df_not = pd.concat([df_not, pd.DataFrame([job])], ignore_index=True)
                            df_not.to_excel(not_applied_jobs_file, index=False)
                        except Exception:
                            pass
                        print(f"    ✗ Retry failed for: {job.get('Job Title', 'Unknown')} | {skip_reason}")

                print(f"==========> Retry results: {retry_success} succeeded, {retry_fail} still failed")

            apply_time = time.time() - apply_start_time
            applications_per_minute = (successful_applications + failed_applications) / (apply_time / 60) if apply_time > 0 else 0
            print(f"\n==========> Application phase completed in {apply_time:.2f} seconds")
            print(f"==========> Successfully applied: {successful_applications} jobs")
            print(f"==========> Failed applications: {failed_applications} jobs")
            print(f"==========> Average application rate: {applications_per_minute:.2f} jobs per minute")

            # Save final data to JSON
            with open("job_data.json", "w") as json_file:
                json.dump(job_data, json_file, indent=4)
            print("Job data saved to job_data.json")

            # Final save to Excel
            save_to_excel(job_data)

        else:
            print("Login failed. Exiting...")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        pass
        # Don't close the browser immediately for debugging
        # driver.quit()
        
    # Calculate and print total execution time
    total_time = time.time() - script_start_time
    hours, remainder = divmod(total_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    print("\n===== EXECUTION TIME SUMMARY =====")
    print(f"Total script execution time: {int(hours)}h {int(minutes)}m {seconds:.2f}s")
    if 'pending_jobs' in locals() and pending_jobs:
        print(f"Average time per job processed: {total_time/len(pending_jobs):.2f} seconds")
    print("==================================")



if __name__ == "__main__":
    # Search in dice
    DICE_SEARCH_QUERIES = ["AI ML", "Gen AI", "Agentic AI", "Data Engineer", "Data Analyst", "Machine Learning"]  # You can update this list anytime

    # Optional: Define keywords for filtering job applications
    EXCLUDE_KEYWORDS = ["Manager", "Director",".net", "SAP","java","w2 only","only w2","no c2c",
        "only on w2","w2 profiles only","tester","f2f"]  # Add more if needed
    INCLUDE_KEYWORDS = ["AI", "Artificial","Inteligence","Machine","Learning", "ML", "Data", "NLP", "ETL",
        "Natural Language Processing","analyst","scientist","senior","cloud", 
        "aws","gcp","Azure","agentic","python","rag","llm"]  # Add more if needed

    start_time = datetime.datetime.now()
    main()
    end_time = datetime.datetime.now()
    print(f"Exact Execution time: {end_time - start_time}")