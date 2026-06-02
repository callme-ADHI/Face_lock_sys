#!/bin/bash
# pam_faciallock.sh — PAM exec wrapper for FaceLock
# Owner: root:root  Permissions: 700
# This script is called by pam_exec.so during authentication.
# MUST produce zero output to stdout/stderr — PAM will break otherwise.

# ── Guard: face lock must be enabled ───────────────────────────────────────
if [ ! -f /var/run/facial_lock.active ]; then
    # Flag file absent → face lock is OFF → tell PAM to continue to next method
    exit 1
fi

# ── Environment for headless OpenCV on Wayland ──────────────────────────────
export OPENCV_VIDEOIO_PRIORITY_BACKEND=4
export MPLBACKEND=Agg
export HOME=/root

# ── Run face recogniser — all output goes to its own log file ───────────────
exec /root/facial_lock/venv/bin/python3 /root/facial_lock/recognize_pam.py \
    >/dev/null 2>/dev/null

# exec replaces this process; the Python script's exit code becomes our exit code.
# If exec itself fails (shouldn't happen), fall through to explicit exit 1:
exit 1
