"""
facial_pam.py — Parallel face auth + TTY password grabber via pam_python.so

Algorithm:
  - If TTY is available (e.g. terminal sudo):
    - Starts the camera daemon in the background.
    - Prompts the user for their password on /dev/tty (with echo off) in a non-blocking loop.
    - If the face is matched, it immediately exits the loop, restores the terminal, and logs in (zero user input).
    - If the user types their password, it collects it and forwards it to pam_unix.so via PAM_AUTHTOK.
  - If TTY is not available (e.g. GUI login):
    - Performs sequential face auth (silently waits for face).
    - If face matches → log in.
    - If face fails → falls back to GUI password prompt.
"""
import os
import sys
import time
import socket
import threading
import select
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


def _get_tty_password(prompt, timeout_secs, face_result_func):
    """
    Prompt the user for a password on /dev/tty, masking input.
    Periodically checks face_result_func().
    Returns (True, None) if face matched.
    Returns (False, password_str) if user typed password.
    Returns (False, None) on timeout/error.
    """
    tty_fd = None
    old_settings = None
    try:
        # Open the actual terminal tty device to prevent stdout redirection issues
        tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except Exception as e:
        _log(f"WARN - Could not open /dev/tty ({e}), falling back to stdin")
        try:
            tty_fd = sys.stdin.fileno()
        except Exception:
            return False, None

    try:
        # Print prompt
        os.write(tty_fd, prompt.encode("utf-8", errors="ignore"))

        # Disable terminal echo and canonical mode (line buffering)
        try:
            old_settings = termios.tcgetattr(tty_fd)
            new_settings = termios.tcgetattr(tty_fd)
            new_settings[3] = new_settings[3] & ~termios.ECHO & ~termios.ICANON
            termios.tcsetattr(tty_fd, termios.TCSANOW, new_settings)
        except Exception as e:
            _log(f"WARN - Termios setup failed: {e}")

        password = []
        start_time = time.time()

        while time.time() - start_time < timeout_secs:
            # Check background face auth state
            if face_result_func() is True:
                return True, None

            # Poll input with a short timeout
            r, _, _ = select.select([tty_fd], [], [], 0.1)
            if r:
                char_bytes = os.read(tty_fd, 1)
                if not char_bytes:
                    break
                char = char_bytes.decode("utf-8", errors="ignore")
                if char in ("\n", "\r"):
                    break
                elif char in ("\x7f", "\x08"):  # Backspace / Ctrl-H
                    if password:
                        password.pop()
                elif char == "\x03":  # Ctrl-C
                    raise KeyboardInterrupt()
                else:
                    password.append(char)
        else:
            return False, None

        return False, "".join(password)

    finally:
        # Restore terminal settings and print newline
        if old_settings is not None:
            try:
                termios.tcsetattr(tty_fd, termios.TCSANOW, old_settings)
            except Exception:
                pass
        try:
            os.write(tty_fd, b"\n")
        except Exception:
            pass
        if tty_fd is not None and tty_fd != sys.stdin.fileno():
            try:
                os.close(tty_fd)
            except Exception:
                pass



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
        _log(f"ERROR - Socket communication failed: {e}")
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
        _log("INFO - Interactive TTY mode: starting parallel face + password auth")
        face_result = [None]
        face_done = threading.Event()

        def _face_thread():
            try:
                ok = _face_auth_request()
                face_result[0] = ok
                _log(f"INFO - Face result: {'OK' if ok else 'FAIL'}")
            except Exception as e:
                _log(f"ERROR - Face thread exception: {e}")
                face_result[0] = False
            finally:
                face_done.set()

        t = threading.Thread(target=_face_thread, daemon=True)
        t.start()

        # Get prompt string
        user = "root"
        try:
            user = pamh.get_user(None) or "user"
        except Exception:
            pass
        prompt = f"[sudo] password for {user}: "

        # Start custom password loop
        face_matched, password = _get_tty_password(prompt, FACE_TIMEOUT, lambda: face_result[0])

        if face_matched:
            _log("SUCCESS - Face auth granted (parallel mode, auto-login)")
            return pamh.PAM_SUCCESS

        if password is not None:
            try:
                pamh.set_item(pamh.PAM_AUTHTOK, password)
            except Exception as e:
                _log(f"ERROR - Failed to set PAM_AUTHTOK: {e}")
            _log("INFO - Face failed — password forwarded to pam_unix.so")
            return pamh.PAM_IGNORE

        _log("FAIL - Face failed and no password entered")
        return pamh.PAM_AUTH_ERR

    else:
        # Non-TTY / GUI login flow (e.g. GDM, lock screen)
        # We just wait for the face auth request sequentially.
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
