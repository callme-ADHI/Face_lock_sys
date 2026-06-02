"""
facial_pam.py — Face-first auth via pam_python.so

Algorithm:
  1. Try face auth silently (camera ON, no prompt shown yet)
     - If face matched in time → grant immediately, NO user input ever
  2. If face fails/timeout → pam_unix.so shows password prompt normally

For "parallel" UX (camera + password at same time), we try TIOCSTI to
auto-inject Enter when face is detected while password prompt is shown.
If TIOCSTI is blocked by the kernel, we fall back to sequential mode.
"""
import os
import sys
import time
import socket
import threading
import fcntl
import termios
import syslog

SOCKET_PATH   = "/run/facial_lock.sock"
FLAG_FILE     = "/var/run/facial_lock.active"
LOG_FILE      = "/root/facial_lock/logs/facial_lock.log"
FACE_TIMEOUT  = 8    # seconds face auth waits before falling back to password
SOCKET_WAIT   = 30   # seconds to wait for daemon socket on cold start


def _log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        syslog.syslog(syslog.LOG_AUTH | syslog.LOG_NOTICE, f"facelock: {msg}")


def _tiocsti_available():
    """Check if TIOCSTI is allowed on this kernel."""
    try:
        val = open("/proc/sys/kernel/tiocsti_restrict").read().strip()
        return val == "0"
    except Exception:
        return True  # assume allowed if sysctl not present


def _try_inject_enter():
    """Try to inject Enter keystroke into terminal input via TIOCSTI."""
    try:
        # Try on stdin (fd 0) first — most reliable in PAM context
        fcntl.ioctl(0, termios.TIOCSTI, b"\n")
        _log("INFO - TIOCSTI Enter injected on stdin")
        return True
    except Exception:
        pass
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
        fcntl.ioctl(fd, termios.TIOCSTI, b"\n")
        os.close(fd)
        _log("INFO - TIOCSTI Enter injected on /dev/tty")
        return True
    except Exception as e:
        _log(f"WARN - TIOCSTI unavailable ({e}) — using sequential mode")
        return False


def _face_auth_request():
    """Connect to daemon and get face auth result. Returns True/False."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(FACE_TIMEOUT + 2)
    s.connect(SOCKET_PATH)
    resp = s.recv(16).strip()
    s.close()
    return resp == b"OK"


def pam_sm_authenticate(pamh, flags, argv):
    # Skip if face lock is off
    if not os.path.isfile(FLAG_FILE):
        return pamh.PAM_IGNORE

    # Wait for daemon socket if models still loading on cold start
    if not os.path.exists(SOCKET_PATH):
        _log("INFO - Waiting for daemon socket...")
        for _ in range(SOCKET_WAIT * 2):
            time.sleep(0.5)
            if os.path.exists(SOCKET_PATH):
                break
        if not os.path.exists(SOCKET_PATH):
            _log("WARN - Daemon not ready — password only")
            return pamh.PAM_IGNORE

    tiocsti_ok = _tiocsti_available()

    if tiocsti_ok:
        # ── PARALLEL MODE: password prompt + camera at same time ──────────
        # Camera starts in background, password prompt shown immediately.
        # When face matches, Enter is injected to auto-submit the prompt.
        _log("INFO - Parallel mode (TIOCSTI available)")
        face_result = [None]
        face_done   = threading.Event()

        def _face_thread():
            try:
                ok = _face_auth_request()
                face_result[0] = ok
                _log(f"INFO - Face result: {'OK' if ok else 'FAIL'}")
                if ok:
                    _try_inject_enter()
            except Exception as e:
                _log(f"ERROR - Face thread: {e}")
                face_result[0] = False
            finally:
                face_done.set()

        t = threading.Thread(target=_face_thread, daemon=True)
        t.start()

        # Show password prompt immediately (camera already running)
        password = ""
        try:
            resp = pamh.conversation(
                pamh.Message(pamh.PAM_PROMPT_ECHO_OFF, "Password: ")
            )
            if resp:
                password = resp.resp or ""
        except Exception as e:
            _log(f"WARN - Conversation error: {e}")

        # Wait for face result (brief — should already be set)
        face_done.wait(timeout=2.0)

        if face_result[0] is True:
            _log("SUCCESS - Face auth granted (parallel mode)")
            return pamh.PAM_SUCCESS

        # Face failed — forward password to pam_unix.so (no second prompt)
        if password:
            try:
                pamh.set_item(pamh.PAM_AUTHTOK, password)
            except Exception:
                pass
            _log("INFO - Face failed — password forwarded to pam_unix.so")
            return pamh.PAM_IGNORE

        return pamh.PAM_AUTH_ERR

    else:
        # ── SEQUENTIAL MODE: face first (silent), then password if needed ──
        # Camera on for up to FACE_TIMEOUT seconds silently.
        # If face matches → grant instantly, user presses NOTHING.
        # If face fails → pam_unix.so shows password prompt.
        _log("INFO - Sequential mode (TIOCSTI restricted)")
        try:
            ok = _face_auth_request()
            if ok:
                _log("SUCCESS - Face auth granted (sequential mode)")
                return pamh.PAM_SUCCESS
            _log("FAIL - Face not detected — falling through to password")
        except socket.timeout:
            _log("FAIL - Face auth timed out — falling through to password")
        except Exception as e:
            _log(f"ERROR - Face auth: {e} — falling through to password")

        return pamh.PAM_IGNORE   # pam_unix.so shows "Password:" prompt


def pam_sm_setcred(pamh, flags, argv):      return pamh.PAM_SUCCESS
def pam_sm_acct_mgmt(pamh, flags, argv):    return pamh.PAM_SUCCESS
def pam_sm_chauthtok(pamh, flags, argv):    return pamh.PAM_SUCCESS
def pam_sm_open_session(pamh, flags, argv): return pamh.PAM_SUCCESS
def pam_sm_close_session(pamh, flags, argv): return pamh.PAM_SUCCESS
