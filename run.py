#!/usr/bin/env python3
"""
Dice Auto Apply Bot Runner
==========================

This script serves as the primary entry point for launching the Dice automation bot.
It isolates environment-level configurations before instantiating the Tkinter GUI.

Key Responsibilities:
1. Validates and enforces proper PYTHONPATH configurations so that the `core` 
   modules can be loaded smoothly regardless of where the script is executed from.
2. Intercepts the startup sequence to execute `fix_chromedriver_permissions`. 
   This safely handles OS-level execution blockers that commonly prevent 
   Selenium webdrivers from launching natively on Mac/Linux or restricted Windows setups.
3. Bootstraps the main Tkinter Window loop housed within `app_tkinter.py`.
"""
import os
import sys

def main():
    """
    Main entry point for the application.
    
    Dynamically injects the target directory into sys.path ensuring imports resolve 
    correctly, patches the selenium chromedriver binaries, and handles top-level
    import failures gracefully by printing an explicit traceback to terminal.
    """
    # Add the current directory to Python path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    
    # Fix chromedriver permissions
    try:
        from fix_chromedriver import fix_chromedriver_permissions
        fix_chromedriver_permissions()
    except Exception as e:
        print(f"Warning: Could not fix ChromeDriver permissions: {e}")
    
    # Import and run the main app
    try:
        from app_tkinter import main
        main()
    except ImportError as e:
        import traceback
        print(f"ERROR: Could not import app_tkinter module.")
        print(f"Reason: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
