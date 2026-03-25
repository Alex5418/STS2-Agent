"""Auto-restart: detect menu state and start a new run via UI automation.

This module uses pyautogui to click through the STS2 menu when the agent
detects the run has ended (death/victory → menu screen).

IMPORTANT: You need to calibrate the button positions for your screen resolution.
Run `python auto_restart.py --calibrate` to find the coordinates.

Dependencies:
    pip install pyautogui
"""

import time
import pyautogui

# ──────────────────────────────────────────────
# Button positions — CALIBRATE THESE for your screen
# ──────────────────────────────────────────────
# Run `python auto_restart.py --calibrate` and hover over each button
# to find the correct coordinates for your setup.

BUTTONS = {
    # Death/victory screen → click to continue
    "continue": (960, 800),
    # Main menu → "Play" or "Singleplayer"
    "play": (960, 500),
    # Character select → Ironclad (usually first/leftmost)
    "ironclad": (400, 400),
    # Confirm / Embark button
    "embark": (960, 700),
}

# Delay between clicks (seconds) — give the game time to animate
CLICK_DELAY = 2.0


def start_new_run():
    """Click through the menu to start a new Ironclad run.

    Call this when the game is in the menu/death screen.
    Returns after the clicks are done — the agent loop should
    then poll for the game state to change from 'menu'.
    """
    print("[AutoRestart] Starting new run...")

    steps = ["continue", "play", "ironclad", "embark"]

    for step_name in steps:
        x, y = BUTTONS[step_name]
        print(f"  Clicking '{step_name}' at ({x}, {y})")
        pyautogui.click(x, y)
        time.sleep(CLICK_DELAY)

    print("[AutoRestart] Clicks done. Waiting for run to start...")
    time.sleep(3)


def calibrate():
    """Interactive calibration: prints mouse position every second."""
    print("=== Auto-Restart Calibration ===")
    print("Hover your mouse over each button and note the coordinates.")
    print("Press Ctrl+C to stop.\n")
    print("Buttons to calibrate:")
    for name in BUTTONS:
        print(f"  - {name}")
    print()

    try:
        while True:
            x, y = pyautogui.position()
            print(f"  Mouse: ({x}, {y})    ", end="\r")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\nUpdate the BUTTONS dict in auto_restart.py with your coordinates.")


if __name__ == "__main__":
    import sys
    if "--calibrate" in sys.argv:
        calibrate()
    else:
        print("Usage:")
        print("  python auto_restart.py --calibrate    # Find button positions")
        print()
        print("Then update BUTTONS dict and use start_new_run() from agent.py")
