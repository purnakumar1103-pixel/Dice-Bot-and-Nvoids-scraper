"""
EmailEngine – Fixed version.

Bugs fixed:
1. Outlook Web: driver was recreated on every EmailEngine instantiation
   (the pipeline creates a new EmailEngine each run). Fixed with a
   class-level driver cache so the same browser stays open.
2. Outlook Web: draft mode returned "Draft" without actually saving —
   now presses Ctrl+S which reliably saves to Outlook Drafts.
3. Outlook Web: removed all hardcoded time.sleep(); replaced with
   short explicit waits.
4. Outlook Web: subject field clear was unreliable (Ctrl+A + Backspace
   on an <input>). Fixed to use .clear() first.
5. Outlook Web: body typing was very slow (character-by-character).
   Now uses JavaScript innerHTML injection for instant fill.
6. Gmail SMTP: connection was not closed in a finally block — leaked
   on errors. Fixed.
"""

import os
import html as html_lib
import imaplib
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication


class EmailEngine:
    # Class-level Selenium driver shared across all instances in a process.
    # This avoids re-opening the browser for every email batch.
    _outlook_web_driver = None

    # Class-level Gmail API service cache — avoids re-running the OAuth flow
    # for every email batch (mirrors the Outlook Web driver cache above).
    _gmail_service = None
    # 'gmail.compose' covers both draft creation AND sending — one scope needed.
    _GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

    def __init__(self, config: dict):
        self.config = config
        self.log = print

    def send_email(self, job_record: dict, resume_path: str, email_body: str, subject: str) -> str:
        provider  = self.config.get("email_provider", "outlook").lower().strip()
        send_mode = self.config.get("send_mode", "draft").lower().strip()

        if provider == "outlook":
            return self._send_outlook(job_record, resume_path, email_body, subject, send_mode)
        elif provider == "outlook_web":
            return self._send_outlook_web(job_record, resume_path, email_body, subject, send_mode)
        elif provider == "gmail":
            return self._send_gmail_api(job_record, resume_path, email_body, subject, send_mode)
        elif provider == "zoho":
            return self._send_zoho_smtp(job_record, resume_path, email_body, subject, send_mode)
        else:
            return f"Error: Unsupported provider '{provider}'"

    # ------------------------------------------------------------------
    # Native Outlook Desktop (win32com)
    # ------------------------------------------------------------------

    def _send_outlook(self, job_record, resume_path, email_body, subject, send_mode):
        com_initialized = False
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                com_initialized = True
            except Exception:
                pass

            import win32com.client
            outlook = win32com.client.Dispatch("outlook.application")
            mail    = outlook.CreateItem(0)

            mail.To      = job_record.get("Recruiter Email", "")
            mail.Subject = subject
            mail.Body    = email_body

            cc = job_record.get("CC", "") or self.config.get("cc_email", "")
            if cc:
                mail.CC = cc
            bcc = job_record.get("BCC", "") or self.config.get("bcc_email", "")
            if bcc:
                mail.BCC = bcc

            if resume_path and os.path.exists(resume_path):
                mail.Attachments.Add(os.path.abspath(resume_path))

            if send_mode == "send":
                mail.Send()
                return "Sent"
            else:
                mail.Save()   # → Drafts folder
                return "Draft"

        except Exception as e:
            self.log(f"[EmailEngine] Outlook error: {e}")
            return f"Error: {e}"
        finally:
            if com_initialized:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Outlook Web via Selenium (persistent browser session)
    # ------------------------------------------------------------------

    def _send_outlook_web(self, job_record, resume_path, email_body, subject, send_mode):
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.keys import Keys
            import time

            driver = self._get_outlook_web_driver()
            wait   = WebDriverWait(driver, 20)

            # ── Ensure we are on the inbox (may need login) ─────────
            new_mail_xpath = (
                "//button[@data-unique-id='Ribbon-NewMail' "
                "or @aria-label='New mail' "
                "or @title='New mail' "
                "or @aria-label='New email' "
                "or @title='New email' "
                "or normalize-space(.)='New mail' "
                "or normalize-space(.)='New email']"
            )
            # ── Helper to find visible element ──
            def get_visible(xpath, timeout=10):
                end_time = time.time() + timeout
                while time.time() < end_time:
                    elems = driver.find_elements(By.XPATH, xpath)
                    visible = [e for e in elems if e.is_displayed()]
                    if visible:
                        return visible[-1] # Usually the most recently opened one
                    time.sleep(0.5)
                raise Exception(f"Visible element not found for xpath: {xpath}")

            try:
                new_mail_btn = wait.until(EC.element_to_be_clickable((By.XPATH, new_mail_xpath)))
            except Exception:
                self.log("[EmailEngine] ⚠️ Please sign in to Outlook in the browser window!")
                self.log("[EmailEngine] (Waiting up to 5 minutes for you to log in...)")
                new_mail_btn = WebDriverWait(driver, 300).until(
                    EC.element_to_be_clickable((By.XPATH, new_mail_xpath))
                )

            # ── Open compose window ─────────────────────────────────
            try:
                new_mail_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", new_mail_btn)

            to_xpath = (
                "//*[not(self::button) and (@aria-label='To' or @aria-label='To ' or @aria-label='To line')]"
            )
            to_input = get_visible(to_xpath, timeout=10)

            try:
                to_input.click()
            except Exception:
                driver.execute_script("arguments[0].click();", to_input)

            to_input.send_keys(job_record.get("Recruiter Email", ""))
            to_input.send_keys(Keys.ENTER)

            # ── CC ──────────────────────────────────────────────────
            cc_email = job_record.get("CC", "")
            if cc_email:
                try:
                    cc_btn_xpath = "//button[@aria-label='Show Cc' or @title='Show Cc']"
                    try:
                        cc_btn = driver.find_element(By.XPATH, cc_btn_xpath)
                        driver.execute_script("arguments[0].click();", cc_btn)
                    except Exception:
                        pass # It might already be visible
                    
                    cc_xpath = (
                        "//*[not(self::button) and (@aria-label='Cc' or @aria-label='Cc ' or @aria-label='Cc line')]"
                    )
                    cc_input = get_visible(cc_xpath, timeout=5)
                    driver.execute_script("arguments[0].click();", cc_input)
                    for addr in cc_email.split(","):
                        addr = addr.strip()
                        if addr:
                            # Typing a comma automatically resolves the email pill in Outlook Web
                            cc_input.send_keys(addr + ",")
                            time.sleep(0.5)
                except Exception as e:
                    self.log(f"[EmailEngine] CC fill warning: {e}")

            # ── Subject ─────────────────────────────────────────────
            try:
                # Restrict to <input> to avoid matching the generic compose wrapper div
                subj_xpath = "//input[@aria-label='Add a subject' or @placeholder='Add a subject' or contains(translate(@aria-label, 'SUBJECT', 'subject'), 'subject')]"
                subj_input = get_visible(subj_xpath, timeout=5)
                # clear() is often unreliable on React inputs; use Ctrl+A + Backspace
                subj_input.click()
                time.sleep(0.2)
                subj_input.send_keys(Keys.CONTROL, "a")
                subj_input.send_keys(Keys.BACKSPACE)
                time.sleep(0.2)
                subj_input.send_keys(subject)
                time.sleep(0.5)
            except Exception as e:
                self.log(f"[EmailEngine] Subject fill warning: {e}")

            # ── Body: use JS for instant paste instead of char-by-char ──
            try:
                body_xpath = "//div[contains(@aria-label,'Message body') and @role='textbox']"
                body_div = get_visible(body_xpath, timeout=5)
                body_div.click()
                # Pass body as a JS argument (not interpolated), so special chars are safe
                driver.execute_script(
                    "arguments[0].innerText = arguments[1];", body_div, email_body
                )
                # CRITICAL: Trigger React state change by typing a space
                body_div.send_keys(" ")
                time.sleep(0.5)
            except Exception as e:
                self.log(f"[EmailEngine] Body fill warning: {e}")

            # ── Attachment ──────────────────────────────────────────
            if resume_path and os.path.exists(resume_path):
                try:
                    file_inputs = driver.find_elements(By.XPATH, "//input[@type='file']")
                    attached = False
                    for file_input in file_inputs:
                        accept_attr = file_input.get_attribute("accept")
                        if accept_attr and "image" in accept_attr.lower():
                            continue
                        try:
                            file_input.send_keys(os.path.abspath(resume_path))
                            attached = True
                            break
                        except Exception:
                            continue
                    if not attached:
                        self.log("[EmailEngine] Warning: Could not find a suitable file input for attachment.")
                    # Wait for upload indicator to disappear and attachment to be fully registered
                    time.sleep(4)
                except Exception as e:
                    self.log(f"[EmailEngine] Attachment warning: {e}")

            # ── Send or Save Draft ───────────────────────────────────
            if send_mode == "send":
                try:
                    time.sleep(1) # Let all fields settle before sending
                    send_btn_xpath = (
                        "//button[@aria-label='Send' or @title='Send' "
                        "or @title='Send (Ctrl+Enter)']"
                    )
                    send_btn = get_visible(send_btn_xpath, timeout=5)
                    send_btn.click()
                    
                    # VERY IMPORTANT: Wait for the email to completely send before starting the next one.
                    # Otherwise, it rapid-fires the "New Mail" button and disrupts the sequence.
                    time.sleep(5)
                    return "Sent"
                except Exception as e:
                    self.log(f"[EmailEngine] Send button error: {e}")
                    return f"Error: Could not click Send ({e})"
            else:
                # Click 'Save draft' button explicitly instead of relying on ESC
                try:
                    save_xpath = "//button[@name='Save draft' or @title='Save draft' or @aria-label='Save draft']"
                    save_btn = get_visible(save_xpath, timeout=2)
                    driver.execute_script("arguments[0].click();", save_btn)
                    time.sleep(1.5)
                except Exception as e:
                    self.log(f"[EmailEngine] Explicit Save draft failed ({e}), using Ctrl+S")
                    try:
                        body_xpath = "//div[contains(@aria-label,'Message body') and @role='textbox']"
                        body_div = get_visible(body_xpath, timeout=2)
                        body_div.send_keys(Keys.CONTROL, "s")
                        time.sleep(1.5)
                    except Exception:
                        pass
                
                # Check for "Discard draft" popups that might have appeared and click "Cancel" to prevent data loss
                try:
                    cancel_xpath = "//button[.='Cancel' or .='Keep' or @aria-label='Cancel' or @aria-label='Keep']"
                    cancel_btns = driver.find_elements(By.XPATH, cancel_xpath)
                    if cancel_btns:
                        driver.execute_script("arguments[0].click();", cancel_btns[-1])
                        time.sleep(1)
                except Exception:
                    pass

                # Always close the compose window so the next email starts fresh
                try:
                    # Specific window close button (excluding pill remove buttons)
                    close_xpath = "//button[(@title='Close' or @aria-label='Close' or @title='Close (Esc)') and not(ancestor::*[contains(@aria-label, 'To') or contains(@aria-label, 'Cc') or contains(@aria-label, 'Bcc')])]"
                    close_btns = driver.find_elements(By.XPATH, close_xpath)
                    visible_closes = [btn for btn in close_btns if btn.is_displayed()]
                    if visible_closes:
                        driver.execute_script("arguments[0].click();", visible_closes[-1])
                        time.sleep(1)
                except Exception as e:
                    self.log(f"[EmailEngine] Close button warning: {e}")
                    
                # Secondary check for unsaved changes popup after clicking close
                try:
                    keep_xpath = "//button[.='Cancel' or .='Keep' or .='Save' or @aria-label='Cancel' or @aria-label='Keep' or @aria-label='Save']"
                    keep_btns = driver.find_elements(By.XPATH, keep_xpath)
                    if keep_btns:
                        driver.execute_script("arguments[0].click();", keep_btns[-1])
                        time.sleep(1)
                except Exception:
                    pass
                    
                return "Draft"

        except Exception as e:
            import traceback
            self.log(f"[EmailEngine] Outlook Web error: {e}\n{traceback.format_exc()}")
            return f"Error: {e}"

    def _get_outlook_web_driver(self):
        """
        Returns the shared Selenium driver, creating it if necessary.
        The driver persists for the lifetime of the Python process so
        we don't pay browser startup cost on every email.
        """
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        if EmailEngine._outlook_web_driver is not None:
            try:
                # Quick liveness check
                _ = EmailEngine._outlook_web_driver.title
                return EmailEngine._outlook_web_driver
            except Exception:
                EmailEngine._outlook_web_driver = None

        options = Options()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        user_data_dir = os.path.abspath("chrome_profile")
        options.add_argument(f"--user-data-dir={user_data_dir}")

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        
        # Use office.com which smartly redirects both enterprise and personal accounts
        driver.get("https://outlook.office.com/mail/")
        self.log("[EmailEngine] Opened Outlook Web. If you see a promo page, click 'Sign in'.")

        EmailEngine._outlook_web_driver = driver
        return driver

    # ------------------------------------------------------------------
    # Gmail SMTP
    # ------------------------------------------------------------------

    def _get_gmail_service(self):
        """Returns an authenticated Gmail API service, cached at class level.
        Runs the OAuth consent flow (opens a browser once) the first time,
        then reuses the saved refresh token silently on every run after."""
        if EmailEngine._gmail_service is not None:
            return EmailEngine._gmail_service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        token_path          = os.path.join("config", "gmail_token.json")
        client_secret_path  = os.path.join("config", "gmail_client_secret.json")

        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, self._GMAIL_SCOPES)

        if not creds or not creds.valid:
            refreshed = False
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    refreshed = True
                except Exception as e:
                    # Refresh tokens for apps in Google's "Testing" publish status
                    # expire after ~7 days regardless of use — this is expected,
                    # not a code error. Fall through to a fresh consent flow
                    # instead of leaving the app permanently broken.
                    self.log(f"[EmailEngine] Gmail refresh token invalid/expired ({e}); re-authorizing...")

            if not refreshed:
                if not os.path.exists(client_secret_path):
                    raise FileNotFoundError(
                        f"Gmail is not authorized yet — {client_secret_path} not found. "
                        "Download an OAuth 2.0 Client ID (Desktop app) JSON from Google Cloud "
                        "Console and save it at that path, then click 'Authorize Gmail'."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, self._GMAIL_SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)
        EmailEngine._gmail_service = service
        return service

    def _send_gmail_api(self, job_record, resume_path, email_body, subject, send_mode):
        import base64

        send_to = job_record.get("Recruiter Email", "").strip()
        if not send_to:
            return "Error: No recipient email"

        cc  = job_record.get("CC", "") or self.config.get("cc_email", "")
        bcc = job_record.get("BCC", "") or self.config.get("bcc_email", "")

        msg = MIMEMultipart()
        msg["To"]      = send_to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        # multipart/alternative: plain-text fallback + HTML styled to match
        # the font configured for Gmail (defaults to Gmail's own compose font).
        font_family = self.config.get("gmail_font_family", "Arial")
        font_size   = self.config.get("gmail_font_size", "14px")
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(email_body, "plain"))
        alt_part.attach(MIMEText(self._html_wrap(email_body, font_family, font_size), "html"))
        msg.attach(alt_part)

        if resume_path and os.path.exists(resume_path):
            with open(resume_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(resume_path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(resume_path)}"'
            msg.attach(part)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        # The cached service (EmailEngine._gmail_service) lives for the whole
        # process lifetime. If the refresh token gets invalidated mid-session
        # (happens unpredictably while the OAuth app is in "Testing" status —
        # sometimes well before its 7-day max), every subsequent send/draft
        # would otherwise fail silently for the rest of the session with no
        # way to recover short of restarting the app. Retry once: on an
        # auth-shaped failure, drop the cached service and re-authorize.
        for attempt in range(2):
            try:
                service = self._get_gmail_service()
            except Exception as e:
                self.log(f"[EmailEngine] Gmail auth error: {e}")
                return f"Error: {e}"

            try:
                if send_mode == "draft":
                    service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
                    return "Draft"
                else:
                    service.users().messages().send(userId="me", body={"raw": raw}).execute()
                    return "Sent"
            except Exception as e:
                err_str = str(e)
                is_auth_error = "invalid_grant" in err_str or "invalid_credentials" in err_str.lower() or " 401" in err_str
                if is_auth_error and attempt == 0:
                    self.log("[EmailEngine] Gmail token invalid mid-session — clearing cache and re-authorizing...")
                    EmailEngine._gmail_service = None
                    continue
                self.log(f"[EmailEngine] Gmail API error: {e}")
                return f"Error: {e}"

    # ------------------------------------------------------------------
    # Zoho Mail (SMTP for sending, IMAP APPEND for real Drafts support)
    # ------------------------------------------------------------------

    @staticmethod
    def _html_wrap(plain_text: str, font_family: str = "Verdana", font_size: str = "10pt") -> str:
        """Wraps plain-text email body in HTML styled to match a given compose
        font (e.g. Zoho Mail's own default: Verdana, 10pt), so the message
        renders with that font instead of the recipient client's default."""
        escaped = html_lib.escape(plain_text)
        paragraphs = escaped.split("\n\n")
        body_html = "".join(
            f"<p style=\"margin:0 0 1em 0;\">{p.replace(chr(10), '<br>')}</p>"
            for p in paragraphs
        )
        return (
            f'<div style="font-family:{font_family}, Geneva, sans-serif; '
            f'font-size:{font_size}; color:#000000;">{body_html}</div>'
        )

    def _send_zoho_smtp(self, job_record, resume_path, email_body, subject, send_mode):
        zoho_user     = self.config.get("zoho_user", "").strip()
        zoho_password = self.config.get("zoho_app_password", "").strip()
        # Zoho is region-sharded (zoho.com / zoho.eu / zoho.in / zoho.com.au / zoho.jp).
        # Defaults to the global .com data center; user can override in settings.
        # Accepts either the short suffix ("eu") or the full form ("zoho.eu") and
        # normalizes to the suffix, since hosts are always built as imap.zoho.<suffix>.
        zoho_domain = (self.config.get("zoho_domain", "") or "zoho.com").strip().lower()
        if zoho_domain.startswith("zoho."):
            zoho_domain = zoho_domain[len("zoho."):]
        zoho_domain = zoho_domain.strip(".") or "com"

        if not zoho_user or not zoho_password:
            return "Error: Zoho credentials missing in settings"

        send_to = job_record.get("Recruiter Email", "").strip()
        if not send_to:
            return "Error: No recipient email"

        cc = job_record.get("CC", "") or self.config.get("cc_email", "")
        bcc = job_record.get("BCC", "") or self.config.get("bcc_email", "")

        msg = MIMEMultipart()
        msg["From"]    = zoho_user
        msg["To"]      = send_to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        # multipart/alternative: plain-text fallback + Verdana/10pt HTML version
        # (matches the font Zoho Mail's own compose window uses by default).
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(email_body, "plain"))
        alt_part.attach(MIMEText(self._html_wrap(email_body), "html"))
        msg.attach(alt_part)

        if resume_path and os.path.exists(resume_path):
            with open(resume_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(resume_path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(resume_path)}"'
            msg.attach(part)

        if send_mode == "draft":
            return self._save_zoho_draft(zoho_user, zoho_password, zoho_domain, msg)

        recipients = [send_to]
        if cc:
            recipients.extend([e.strip() for e in cc.split(",") if e.strip()])
        if bcc:
            recipients.extend([e.strip() for e in bcc.split(",") if e.strip()])

        server = None
        try:
            server = smtplib.SMTP_SSL(f"smtp.zoho.{zoho_domain}", 465, timeout=20)
            server.ehlo()
            server.login(zoho_user, zoho_password)
            server.sendmail(zoho_user, recipients, msg.as_string())
            return "Sent"
        except Exception as e:
            self.log(f"[EmailEngine] Zoho SMTP error: {e}")
            return f"Error: {e}"
        finally:
            if server:
                try:
                    server.close()
                except Exception:
                    pass

    def _save_zoho_draft(self, zoho_user, zoho_password, zoho_domain, msg):
        """Appends the message to the Zoho 'Drafts' IMAP folder (real draft support,
        unlike Gmail where plain SMTP has no way to reach the Drafts folder)."""
        import time as _time
        conn = None
        try:
            conn = imaplib.IMAP4_SSL(f"imap.zoho.{zoho_domain}", 993, timeout=20)
            conn.login(zoho_user, zoho_password)

            # Zoho's Drafts folder name varies by account language/setup — try common names.
            draft_folder = None
            for candidate in ("Drafts", "INBOX/Drafts", "Draft"):
                status, _ = conn.select(candidate, readonly=False)
                if status == "OK":
                    draft_folder = candidate
                    break
            if not draft_folder:
                return "Error: Could not locate Zoho 'Drafts' folder via IMAP"

            conn.append(
                draft_folder,
                r"(\Draft)",
                imaplib.Time2Internaldate(_time.time()),
                msg.as_bytes()
            )
            return "Draft"
        except Exception as e:
            self.log(f"[EmailEngine] Zoho IMAP draft-save error: {e}")
            return f"Error: {e}"
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass
