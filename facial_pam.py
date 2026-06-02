"""
facial_pam.py — Parallel face auth + password prompt via pam_python.so

Flow:
  1. Camera starts (daemon) in background thread
  2. Password prompt appears IMMEDIATELY
  3. If face matches → access granted, no password needed
  4. If user types password → it's stored for pam_unix.so to verify (no second prompt)
  5. If both fail → deny
"""
import os
import sys
import time
import socket
import threading
import syslog

SOCKET_PATH = "/run/facial_lock.sock"
FLAG_FILE   = "/var/run/facial_lock.active"
LOG_FILE    = "/root/facial_lock/logs/facial_lock.log"
FACE_TIMEOUT = 15  # max seconds to wait for face after user presses Enter


def _log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        syslog.syslog(syslog.LOG_AUTH | syslog.LOG_NOTICE, f"facelock: {msg}")


def _run_face_auth(result_box, done_event):
    """Background thread: connect to daemon, get face auth result."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(FACE_TIMEOUT + 2)
        s.connect(SOCKET_PATH)
        resp = s.recv(16).strip()
        s.close()
        result_box[0] = (resp == b"OK")
        _log(f"INFO - Face auth result: {resp.decode(errors='replace')}")
    except Exception as e:
        _log(f"ERROR - Face auth thread: {e}")
        result_box[0] = False
    finally:
        done_event.set()


def pam_sm_authenticate(pamh, flags, argv):
    # Skip if face lock is off or daemon not running
    if not os.path.isfile(FLAG_FILE):
        return pamh.PAM_IGNORE

    # Flag exists → daemon started. Wait for socket (models may still be loading)
    if not os.path.exists(SOCKET_PATH):
        _log("INFO - Daemon loading models, waiting for socket...")
        for _ in range(60):           # wait up to 30 seconds (0.5s steps)
            time.sleep(0.5)
            if os.path.exists(SOCKET_PATH):
                break
        if not os.path.exists(SOCKET_PATH):
            _log("WARN - Daemon not ready, falling back to password")
            return pamh.PAM_IGNORE


    _log("INFO - Parallel auth: starting face + password simultaneously")

    face_result = [None]   # True / False / None
    done_event  = threading.Event()

    # ── Start face auth in background (camera light turns on NOW) ──────────
    face_thread = threading.Thread(
        target=_run_face_auth,
        args=(face_result, done_event),
        daemon=True
    )
    face_thread.start()

    # ── Show password prompt immediately (user sees camera + prompt at once) ─
    password = ""
    try:
        msg  = pamh.Message(pamh.PAM_PROMPT_ECHO_OFF, "Password: ")
        resp = pamh.conversation(msg)
        if resp:
            password = resp.resp or ""
    except Exception as e:
        _log(f"WARN - PAM conversation error: {e}")

    # ── User pressed Enter. Check face result ─────────────────────────────
    # If face auth not done yet, wait for remaining time
    if not done_event.is_set():
        _log("INFO - Waiting for face auth to complete...")
        done_event.wait(timeout=FACE_TIMEOUT)

    # ── Decision ──────────────────────────────────────────────────────────
    if face_result[0] is True:
        _log("SUCCESS - Face auth granted access (parallel mode)")
        return pamh.PAM_SUCCESS

    if password:
        # Pass the typed password to pam_unix.so via PAM_AUTHTOK
        # pam_unix.so will verify it without showing a second prompt
        try:
            pamh.set_item(pamh.PAM_AUTHTOK, password)
        except Exception:
            pass
        _log("INFO - Face failed, forwarding password to pam_unix.so")
        return pamh.PAM_IGNORE   # pam_unix.so handles it

    _log("FAIL - Face failed and no password entered")
    return pamh.PAM_AUTH_ERR


def pam_sm_setcred(pamh, flags, argv):    return pamh.PAM_SUCCESS
def pam_sm_acct_mgmt(pamh, flags, argv):  return pamh.PAM_SUCCESS
def pam_sm_chauthtok(pamh, flags, argv):  return pamh.PAM_SUCCESS
def pam_sm_open_session(pamh, flags, argv): return pamh.PAM_SUCCESS
def pam_sm_close_session(pamh, flags, argv): return pamh.PAM_SUCCESS
