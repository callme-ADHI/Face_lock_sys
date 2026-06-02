"""
facial_pam.py — Python PAM module loaded by pam_python.so
Connects to the FaceLock daemon socket. Auth completes in ~2 seconds.
"""
import os
import sys
import socket
import time
import syslog

SOCKET_PATH = "/run/facial_lock.sock"
FLAG_FILE   = "/var/run/facial_lock.active"
LOG_FILE    = "/root/facial_lock/logs/facial_lock.log"
TIMEOUT     = 20   # seconds to wait for daemon response


def _log(msg):
    try:
        with open(LOG_FILE, "a") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        syslog.syslog(syslog.LOG_AUTH | syslog.LOG_NOTICE, f"facelock: {msg}")


def pam_sm_authenticate(pamh, flags, argv):
    # Guard: face lock must be enabled
    if not os.path.isfile(FLAG_FILE):
        return pamh.PAM_IGNORE

    # Guard: daemon must be running
    if not os.path.exists(SOCKET_PATH):
        _log("WARN - Daemon socket not found — falling back to password")
        return pamh.PAM_IGNORE

    _log("INFO - PAM connecting to FaceLock daemon...")

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        sock.connect(SOCKET_PATH)
        response = sock.recv(16).strip()
        sock.close()

        if response == b"OK":
            _log("INFO - PAM: daemon returned OK — granting access")
            return pamh.PAM_SUCCESS
        else:
            _log(f"INFO - PAM: daemon returned {response!r} — falling back to password")
            return pamh.PAM_IGNORE

    except Exception as e:
        _log(f"ERROR - PAM socket error: {e} — falling back to password")
        return pamh.PAM_IGNORE


def pam_sm_setcred(pamh, flags, argv):
    return pamh.PAM_SUCCESS

def pam_sm_acct_mgmt(pamh, flags, argv):
    return pamh.PAM_SUCCESS

def pam_sm_chauthtok(pamh, flags, argv):
    return pamh.PAM_SUCCESS

def pam_sm_open_session(pamh, flags, argv):
    return pamh.PAM_SUCCESS

def pam_sm_close_session(pamh, flags, argv):
    return pamh.PAM_SUCCESS
