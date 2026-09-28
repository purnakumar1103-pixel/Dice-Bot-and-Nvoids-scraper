"""
Dice Login Automation
=====================

This module handles the complex process of securely authenticating the bot 
with the Dice.com platform. It features robust waiting mechanisms, dynamic
URL checking, and headless validation routines to bypass Dice's multi-step
login UI and occasional captchas/slow-loading pages.
"""

import os
import time
import getpass
from pathlib import Path
from selenium.webdriver.common.by import By
from utils.timing import smart_sleep as _smart_sleep
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv, set_key, find_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def update_dice_credentials(username, password, update_env=True):
    """
    Persistently stores the successfully verified Dice credentials in the `.env` file
    to bypass needing to re-enter them on subsequent executions.

    Parameters:
        username (str): Dice account email/username
        password (str): Dice account password
        update_env (bool): Whether to update the .env file or not

    Returns:
        bool: True if credentials were updated successfully
    """
    if not username or not password:
        print("Invalid credentials provided. Both username and password are required.")
        return False
    
    try:
        if update_env:
            # Find or create .env file
            dotenv_path = find_dotenv()
            if not dotenv_path:
                dotenv_path = os.path.join(os.getcwd(), '.env')
                Path(dotenv_path).touch(exist_ok=True)
                print(f"Created new .env file at {dotenv_path}")
            
            # Load existing .env file
            load_dotenv(dotenv_path)
            
            # Update credentials in .env file
            set_key(dotenv_path, "DICE_USERNAME", username)
            set_key(dotenv_path, "DICE_PASSWORD", password)
            print("Dice credentials updated in .env file.")
        
        # Set the environment variables for current session
        os.environ["DICE_USERNAME"] = username
        os.environ["DICE_PASSWORD"] = password
        
        return True
    except Exception as e:
        print(f"Error updating credentials: {e}")
        return False

def get_headless_driver():
    """
    Creates a headless WebDriver for credential validation
    
    Returns:
        webdriver: A headless Chrome/Brave WebDriver instance
    """
    try:
        # Import browser detector if available
        try:
            from core.browser_detector import get_browser_path
        except ImportError:
            from browser_detector import get_browser_path
        web_browser_path = get_browser_path()
    except ImportError:
        # Fallback if browser_detector is not available
        web_browser_path = None
    
    options = Options()
    
    # Add headless options
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    
    # Set browser binary location if available
    if web_browser_path:
        options.binary_location = web_browser_path
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def validate_dice_credentials(username, password, headless=True):
    """
    Validates Dice credentials by attempting to log in using a headless browser.
    With enhanced waiting times for slow login processes.
    
    Parameters:
        username (str): Dice account email/username
        password (str): Dice account password
        headless (bool): Whether to use headless mode for validation
        
    Returns:
        bool: True if login was successful, False otherwise
    """
    print(f"Validating credentials for {username}...")
    
    # Create driver (headless or regular)
    if headless:
        driver = get_headless_driver()
    else:
        # Import from main file to get regular driver
        try:
            from core.browser_detector import get_browser_path
        except ImportError:
            from browser_detector import get_browser_path
            
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        
        web_browser_path = get_browser_path()
        options = Options()
        options.binary_location = web_browser_path
        options.add_argument("--start-maximized")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        # Try login with provided credentials
        driver.get("https://www.dice.com/dashboard/login")
        wait = WebDriverWait(driver, 20)       # Increased from 10 to 20
        long_wait = WebDriverWait(driver, 120) # Much longer wait for final verification
        
        # Enter email/username
        email_field = wait.until(EC.presence_of_element_located((By.NAME, "email")))
        email_field.clear()
        email_field.send_keys(username)
        
        # Click continue
        continue_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='sign-in-button']")))
        continue_button.click()
        _smart_sleep(3)  # post-click pause (scaled by speed multiplier)
        
        # Enter password
        password_field = wait.until(EC.presence_of_element_located((By.NAME, "password")))
        password_field.clear()
        password_field.send_keys(password)
        
        # Click login button
        login_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='submit-password']")))
        login_button.click()
        
        # Add a longer pause after clicking login
        _smart_sleep(10)  # post-login pause (scaled by speed multiplier)
        
        # Check for successful login — prefer URL-based detection over brittle class XPaths (#10)
        def _login_succeeded(d):
            url = d.current_url
            if any(k in url for k in ("/dashboard", "/home", "/jobs", "/search")):
                return True
            # Fallback: any element that only appears post-login (data-testid is stable)
            try:
                d.find_element(By.CSS_SELECTOR, '[data-testid="search-bar"], [data-testid="nav-profile-link"], nav[aria-label]')
                return True
            except Exception:
                return False

        try:
            long_wait.until(_login_succeeded)
            print("Login successful with provided credentials!")
            return True
        except Exception:
            current_url = driver.current_url
            if any(k in current_url for k in ("/dashboard", "/home", "/jobs", "/search")):
                print("Login successful with provided credentials!")
                return True
            # Look for an explicit error message
            try:
                err = driver.find_element(By.CSS_SELECTOR, '[data-testid="error-message"], .error-message, .alert-danger')
                print(f"Login failed: {err.text}")
            except Exception:
                print(f"Login failed: Could not verify login result (current URL: {current_url})")
            return False
            
    except Exception as e:
        print(f"Error validating credentials: {e}")
        return False
    finally:
        driver.quit()


def login_to_dice(driver, credentials_from_params=None):
    """
    Logs into Dice using credentials from the .env file or provided parameters.
    With enhanced waiting and retry logic for slow login processes.
    
    Parameters:
        driver (selenium.webdriver): Selenium WebDriver instance.
        credentials_from_params (tuple): Optional (username, password) tuple to use instead of .env
    
    Returns:
        bool: True if login is successful, False otherwise.
    """
    # Load credentials from parameters or environment
    if credentials_from_params and len(credentials_from_params) == 2:
        username, password = credentials_from_params
    else:
        # Load from environment
        load_dotenv()
        username = os.getenv("DICE_USERNAME")
        password = os.getenv("DICE_PASSWORD")
    
    if not username or not password:
        raise Exception("Dice credentials not found. Please set DICE_USERNAME and DICE_PASSWORD in .env file or provide them as parameters.")
    
    # Navigate to login page
    print("Navigating to Dice login page...")
    driver.get("https://www.dice.com/dashboard/login")
    
    # Set up wait objects with increased timeouts
    short_wait = WebDriverWait(driver, 20)  # Increased timeout
    long_wait = WebDriverWait(driver, 120)  # Much longer timeout for final step

    try:
        # Enter email/username
        print("Entering username...")
        email_field = short_wait.until(EC.presence_of_element_located((By.NAME, "email")))
        email_field.clear()
        email_field.send_keys(username)

        # Click continue button
        print("Clicking continue button...")
        continue_button = short_wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='sign-in-button']")))
        continue_button.click()
        _smart_sleep(3)  # post-click pause (scaled by speed multiplier) to ensure page transitions

        # Enter password
        print("Entering password...")
        password_field = short_wait.until(EC.presence_of_element_located((By.NAME, "password")))
        password_field.clear()
        password_field.send_keys(password)

        # Click login button
        print("Clicking login button...")
        login_button = short_wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='submit-password']")))
        login_button.click()
        
        # Add a longer pause after clicking login
        print("Waiting for login to complete (this may take some time)...")
        _smart_sleep(10)  # post-login pause (scaled by speed multiplier) after login click

        # Verify login — URL-based detection is stable across Dice redesigns (#10)
        print("Verifying login success...")

        def _login_succeeded(d):
            url = d.current_url
            if any(k in url for k in ("/dashboard", "/home", "/jobs", "/search")):
                return True
            try:
                d.find_element(By.CSS_SELECTOR, '[data-testid="search-bar"], [data-testid="nav-profile-link"], nav[aria-label]')
                return True
            except Exception:
                return False

        try:
            long_wait.until(_login_succeeded)
            print(f"Login verified! (URL: {driver.current_url})")
            return True
        except Exception:
            current_url = driver.current_url
            if any(k in current_url for k in ("/dashboard", "/home", "/jobs", "/search")):
                print(f"Login verified by URL: {current_url}")
                return True
            try:
                err = driver.find_element(By.CSS_SELECTOR, '[data-testid="error-message"], .error-message, .alert-danger')
                print(f"Login failed: {err.text}")
            except Exception:
                print(f"Login verification failed — current URL: {current_url}")
            return False

    except Exception as e:
        print(f"Login process failed: {e}")
        return False


def setup_credentials_interactive(headless=True):
    """
    Interactive command-line setup for Dice credentials.
    Tests login before saving to .env file.
    
    Parameters:
        headless (bool): Whether to use headless mode for validation
        
    Returns:
        bool: True if credentials were successfully set up
    """
    print("\n=== Dice Credentials Setup ===")
    print("Please enter your Dice.com login information.")
    
    username = input("Email/Username: ").strip()
    password = getpass.getpass("Password: ").strip()  # hidden input — never echoed to terminal (#6)
    
    if not username or not password:
        print("Both username and password are required.")
        return False
    
    # Validate the credentials
    if validate_dice_credentials(username, password, headless=headless):
        # Save to .env file
        update_dice_credentials(username, password)
        return True
    else:
        print("Invalid credentials. Please try again.")
        return False

if __name__ == "__main__":
    # This allows running the file directly for credential setup
    try:
        print("Starting Dice credential setup...")
        success = False
        
        while not success:
            success = setup_credentials_interactive(headless=True)
            if not success:
                retry = input("Would you like to try again? (y/n): ").lower()
                if retry != 'y':
                    break
        
        if success:
            print("Credential setup complete! You can now run the main application.")
        else:
            print("Credential setup was not completed. You will need to set up credentials before using the application.")
    
    except Exception as e:
        print(f"An error occurred during setup: {e}")