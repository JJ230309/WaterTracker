#!/usr/bin/env python3
"""Sends a hydration reminder notification via terminal-notifier.
Clicking the notification activates WaterTracker.app (bundle: com.local.hydration-tracker).
Called by launchd (LaunchAgent) on a fixed interval."""

import subprocess, json, os

TERMINAL_NOTIFIER = "/opt/homebrew/bin/terminal-notifier"
BUNDLE_ID         = "com.local.hydration-tracker"
DATA_FILE         = os.path.expanduser("~/.watertracker/data.json")

# ── Build a helpful notification body from today's intake data ────────────────
try:
    with open(DATA_FILE) as f:
        d = json.load(f)
    intake = d.get("intake_ml", 0)
    goal   = d.get("goal_ml", 2000)
    pct    = int(intake / goal * 100) if goal else 0
    if pct >= 100:
        message = f"Goal reached! You've had {intake} ml today. Amazing work!"
    else:
        left    = goal - intake
        message = f"{left} ml to go  ·  {pct}% of today's goal complete."
except Exception:
    message = "Stay hydrated — open WaterTracker to log your intake."

# ── Send notification; clicking it opens WaterTracker.app ─────────────────────
subprocess.run([
    TERMINAL_NOTIFIER,
    "-title",    "💧 Time to Drink Water!",
    "-message",  message,
    "-activate", BUNDLE_ID,
    "-sound",    "Blow",
])
