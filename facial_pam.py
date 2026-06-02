"""
facial_pam.py — Parallel face auth + password prompt via pam_python.so

Flow:
  1. Spawns a background thread to wait for face recognition from the daemon.
  2. Main thread prompts the user for their password immediately using standard pamh.conversation().
  3. If face matches, the background thread injects a newline (\n) directly to /dev/tty using TIOCSTI.
  4. This unblocks the conversation prompt immediately (acting as if Enter was pressed).
  5. The main thread checks the face result:
     - If face matched → returns PAM_SUCCESS (logs in with zero user input).
     - If face failed (or user typed password manually) → forwards the password to pam_unix.so.
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
FACE_TIMEOUT  = 12   # max seconds to wait for face result
SOCKET_WAIT   = 30   # seconds to wait for daemon socket on cold start


def _log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    try:
        with open("/tmp/facial_lock_debug.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        os.chmod("/tmp/facial_lock_debug.log", 0o666)
    except Exception:
        pass
    try:
        syslog.syslog(syslog.LOG_AUTH | syslog.LOG_NOTICE, f"facelock: {msg}")
    except Exception:
        pass


def _inject_enter():
    """Inject a newline (\n) into the controlling terminal (/dev/tty) input queue."""
    # Try /dev/tty first (controlling terminal)
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        fcntl.ioctl(fd, termios.TIOCSTI, b"\n")
        os.close(fd)
        _log("INFO - TIOCSTI Enter injected on /dev/tty")
        return True
    except Exception as e:
        _log(f"WARN - TIOCSTI on /dev/tty failed: {e}")

    # Fallback to stdin (fd 0)
    try:
        fcntl.ioctl(0, termios.TIOCSTI, b"\n")
        _log("INFO - TIOCSTI Enter injected on stdin")
        return True
    except Exception as e:
        _log(f"WARN - TIOCSTI on stdin failed: {e}")

    return False


def _face_auth_request():
    """Connect to daemon and get face auth result. Returns True/False."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(FACE_TIMEOUT)
        s.connect(SOCKET_PATH)
        resp = s.recv(16).strip()
        s.close()
        return resp == b"OK"
    except Exception as e:
        _log(f"ERROR - Daemon socket request failed: {e}")
        return False


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

    # Check if we are running in an interactive terminal/TTY context
    is_tty = False
    try:
        is_tty = os.isatty(sys.stdin.fileno()) or os.path.exists("/dev/tty")
    except Exception:
        pass

    if is_tty:
        _log("INFO - Parallel mode: starting face + password auth")
        face_result = [None]
        face_done = threading.Event()

        def _face_thread():
            try:
                ok = _face_auth_request()
                face_result[0] = ok
                _log(f"INFO - Face result: {'OK' if ok else 'FAIL'}")
                if ok:
                    # Face matched! Trigger auto-submit of the password prompt
                    _inject_enter()
            except Exception as e:
                _log(f"ERROR - Face thread exception: {e}")
                face_result[0] = False
            finally:
                face_done.set()

        t = threading.Thread(target=_face_thread, daemon=True)
        t.start()

        # Prompt the user for password using standard PAM conversation helper.
        # This will block until the user types password + Enter, OR the face thread injects \n.
        password = ""
        try:
            resp = pamh.conversation(pamh.Message(pamh.PAM_PROMPT_ECHO_OFF, "Password: "))
            if resp:
                password = resp.resp or ""
        except Exception as e:
            _log(f"WARN - PAM conversation error: {e}")

        # Wait briefly for the face thread to complete and set the result
        face_done.wait(timeout=2.0)

        if face_result[0] is True:
            _log("SUCCESS - Face auth granted (parallel mode, auto-login)")
            return pamh.PAM_SUCCESS

        # Face failed — forward password to pam_unix.so
        if password:
            try:
                # Set password token for the next module (pam_unix.so use_authtok)
                pamh.authtok = password
            except Exception as e:
                _log(f"ERROR - Failed to set pamh.authtok: {e}")
            _log("INFO - Face failed — password forwarded to pam_unix.so")
            return pamh.PAM_IGNORE

        _log("FAIL - Face failed and no password entered")
        return pamh.PAM_AUTH_ERR

    else:
        # Non-TTY / GUI login flow
        _log("INFO - Non-TTY mode: running sequential face auth")
        try:
            if _face_auth_request():
                _log("SUCCESS - Face auth granted (non-TTY mode)")
                return pamh.PAM_SUCCESS
            _log("FAIL - Face not detected — falling back to GUI password")
        except Exception as e:
            _log(f"ERROR - Non-TTY face auth exception: {e}")

        return pamh.PAM_IGNORE


def pam_sm_setcred(pamh, flags, argv):      return pamh.PAM_SUCCESS
def pam_sm_acct_mgmt(pamh, flags, argv):    return pamh.PAM_SUCCESS
def pam_sm_chauthtok(pamh, flags, argv):    return pamh.PAM_SUCCESS
def pam_sm_open_session(pamh, flags, argv): return pamh.PAM_SUCCESS
def pam_sm_close_session(pamh, flags, argv): return pamh.PAM_SUCCESS
