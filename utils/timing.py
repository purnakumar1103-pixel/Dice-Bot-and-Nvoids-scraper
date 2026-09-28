"""
Shared timing utilities — speed-multiplier-aware sleep and WebDriverWait wrappers.
Import from here so dice_login.py and any other module can scale with GLOBAL_SPEED_MULTIPLIER
without depending on main_script.py.
"""

import time

# Multiplier applied to all sleep/wait durations.
# 1.0 = normal speed; 0.2 = 5x faster; 3.0 = 3x slower (debugging).
# main_script.py sets this at startup via set_speed_multiplier().
_MULTIPLIER: float = 1.0


def set_speed_multiplier(value: float) -> None:
    global _MULTIPLIER
    _MULTIPLIER = max(0.05, float(value))


def get_multiplier() -> float:
    return _MULTIPLIER


def smart_sleep(seconds: float) -> None:
    time.sleep(seconds / _MULTIPLIER)


def get_wait(driver, timeout: float, poll_frequency: float = 0.5):
    from selenium.webdriver.support.ui import WebDriverWait
    return WebDriverWait(driver, timeout / _MULTIPLIER, poll_frequency=poll_frequency)
