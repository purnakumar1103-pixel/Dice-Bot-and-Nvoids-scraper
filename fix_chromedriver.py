# dice_auto_apply/fix_chromedriver.py

"""
ChromeDriver Permission Fixer
=============================

This utility script is responsible for automatically resolving OS-level permission 
issues that commonly prevent Selenium from launching the webdriver manager binaries.

Particularly on macOS and strict Linux environments, downloaded binaries lack 
the `+x` execution flag or are trapped by Apple's Gatekeeper (quarantine).
"""

import os
import stat
import glob
import platform
import subprocess

def fix_chromedriver_permissions():
    """
    Locates cached ChromeDriver binaries within the `~/.wdm/` directory 
    and forcefully patches their OS-level permissions.
    
    Operations performed:
    - Adds global execute (`+x`) permissions via `os.chmod`.
    - (macOS only) Deletes the `com.apple.quarantine` extended attribute to 
      bypass Gatekeeper 'unidentified developer' popups.
      
    Returns:
        bool: True if at least one driver was found and successfully patched, False otherwise.
    """
    print("Fixing ChromeDriver permissions...")
    
    # Get user's home directory
    home_dir = os.path.expanduser("~")
    
    # Look for chromedriver in .wdm directory
    chromedriver_pattern = os.path.join(home_dir, ".wdm", "drivers", "chromedriver", "**", "chromedriver*")
    
    # Find all chromedriver files
    chromedriver_files = glob.glob(chromedriver_pattern, recursive=True)
    
    if not chromedriver_files:
        print("No ChromeDriver files found.")
        return False
        
    success = False
    
    for driver_path in chromedriver_files:
        if os.path.isfile(driver_path) and not os.path.islink(driver_path):
            try:
                print(f"Setting permissions for: {driver_path}")
                
                # Add execute permissions (chmod +x)
                os.chmod(driver_path, os.stat(driver_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                # On macOS, also strip ALL extended attributes (quarantine, provenance, etc.)
                # and re-sign ad-hoc. Newer macOS versions SIGKILL (-9) unsigned/ad-hoc
                # driver binaries at launch even when no quarantine flag is present, and
                # `codesign --force` re-establishes a valid local signature so Gatekeeper
                # lets the process actually run.
                if platform.system() == "Darwin":
                    try:
                        subprocess.run(["xattr", "-cr", driver_path],
                                      stderr=subprocess.DEVNULL)
                    except:
                        pass  # Ignore if xattr command fails
                    try:
                        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", driver_path],
                                      stderr=subprocess.DEVNULL)
                    except:
                        pass  # Ignore if codesign command fails

                print(f"Fixed permissions for {os.path.basename(driver_path)}")
                success = True
            except Exception as e:
                print(f"Error setting permissions for {driver_path}: {e}")
    
    return success

if __name__ == "__main__":
    # This allows running the script directly to fix permissions
    fixed = fix_chromedriver_permissions()
    
    if fixed:
        print("Successfully fixed ChromeDriver permissions.")
    else:
        print("Could not fix ChromeDriver permissions. Please check manually.")
