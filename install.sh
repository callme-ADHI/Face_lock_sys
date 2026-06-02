#!/bin/bash
# install.sh — FaceLock master installer (libpam-python + daemon edition)
# Run as root: sudo bash install.sh
set -euo pipefail

# ── Root check ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "[✗] Error: install.sh must be run as root."
    echo "    Run: sudo bash install.sh"
    exit 1
fi

# ── Helper ───────────────────────────────────────────────────────────────────
ok()   { echo "[✓] $*"; }
info() { echo "[!] $*"; }
fail() { echo "[✗] $*"; exit 1; }

echo ""
echo "════════════════════════════════════════════"
echo "  FACELOCK — Installation Starting"
echo "════════════════════════════════════════════"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 1 — Check prerequisites
# ────────────────────────────────────────────────────────────────────────────
info "STEP 1 — Checking prerequisites..."

[ -e /dev/video0 ] || fail "Camera not found at /dev/video0. Plug in your webcam and retry."

MODEL_SRC="/home/adhi/Desktop/Facenet-training-pipeline/models/ADHI/mean_embedding.npy"
[ -f "$MODEL_SRC" ] || fail "Trained model not found: $MODEL_SRC"

VENV_SRC="/home/adhi/Desktop/Facenet-training-pipeline/venv"
[ -d "$VENV_SRC" ] || fail "Python venv not found: $VENV_SRC"

ok "Prerequisites OK"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 2 — Create system directory
# ────────────────────────────────────────────────────────────────────────────
info "STEP 2 — Creating /root/facial_lock/ directory structure..."
mkdir -p /root/facial_lock/models
mkdir -p /root/facial_lock/logs
mkdir -p /root/facial_lock/facenet_weights
ok "Directories created"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 3 — Copy trained model
# ────────────────────────────────────────────────────────────────────────────
info "STEP 3 — Copying trained model (ADHI)..."
rm -rf /root/facial_lock/models/ADHI
cp -r /home/adhi/Desktop/Facenet-training-pipeline/models/ADHI \
      /root/facial_lock/models/ADHI
ok "Trained model copied"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 4 — Symlink Python venv (avoids copying multi-GB PyTorch to /root)
# ────────────────────────────────────────────────────────────────────────────
info "STEP 4 — Symlinking Python venv (no copy — saves disk space)..."
rm -rf /root/facial_lock/venv
ln -s /home/adhi/Desktop/Facenet-training-pipeline/venv \
      /root/facial_lock/venv
ok "Python venv symlinked → /home/adhi/Desktop/Facenet-training-pipeline/venv"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 5 — Install daemon and pam files
# ────────────────────────────────────────────────────────────────────────────
info "STEP 5 — Installing daemon, PAM module, and standalone recognizer..."
cp /home/adhi/Desktop/Facial_lock_sys/facial_lock_daemon.py /root/facial_lock/facial_lock_daemon.py
cp /home/adhi/Desktop/Facial_lock_sys/facial_pam.py /root/facial_lock/facial_pam.py
cp /home/adhi/Desktop/Facial_lock_sys/recognize_pam.py /root/facial_lock/recognize_pam.py
ok "Python files installed"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 6 — Lock down /root/facial_lock permissions
# ────────────────────────────────────────────────────────────────────────────
info "STEP 6 — Securing /root/facial_lock permissions..."
chown root:root /root/facial_lock
chmod 755 /root/facial_lock
chown -R root:root /root/facial_lock/models /root/facial_lock/facenet_weights 2>/dev/null || true
chmod -R 755 /root/facial_lock/models /root/facial_lock/facenet_weights 2>/dev/null || true
chmod 755 /root/facial_lock/facial_lock_daemon.py
chmod 755 /root/facial_lock/facial_pam.py
chmod 755 /root/facial_lock/recognize_pam.py
touch /root/facial_lock/logs/facial_lock.log
chmod 644 /root/facial_lock/logs/facial_lock.log
chown root:root /root/facial_lock/logs/facial_lock.log
ok "Permissions secured"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 7 — Add root to video group
# ────────────────────────────────────────────────────────────────────────────
info "STEP 7 — Adding root to video group..."
usermod -aG video root
ok "root added to video group"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 8 — Install facial_lock CLI
# ────────────────────────────────────────────────────────────────────────────
info "STEP 8 — Installing facial_lock command..."
cp /home/adhi/Desktop/Facial_lock_sys/facial_lock /usr/local/bin/facial_lock
chmod 755 /usr/local/bin/facial_lock
chown root:root /usr/local/bin/facial_lock
ok "facial_lock command installed at /usr/local/bin/facial_lock"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 9 — Install libpam-python
# ────────────────────────────────────────────────────────────────────────────
info "STEP 9 — Installing libpam-python..."
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y libpam-python >/dev/null 2>&1
ok "libpam-python installed"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 10 — Install systemd service
# ────────────────────────────────────────────────────────────────────────────
info "STEP 10 — Installing systemd service..."
cp /home/adhi/Desktop/Facial_lock_sys/facial_lock.service \
   /etc/systemd/system/facial_lock.service
systemctl daemon-reload
ok "systemd service installed (facial_lock.service)"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 11 — Backup PAM configs
# ────────────────────────────────────────────────────────────────────────────
info "STEP 11 — Backing up PAM configs..."
cp /etc/pam.d/sudo          /etc/pam.d/sudo.facial_lock_backup
cp /etc/pam.d/gdm-password  /etc/pam.d/gdm-password.facial_lock_backup
if [ -f /etc/pam.d/polkit-1 ]; then
    cp /etc/pam.d/polkit-1  /etc/pam.d/polkit-1.facial_lock_backup
    echo "    → /etc/pam.d/polkit-1 backed up"
fi
ok "PAM configs backed up"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 12 — Modify PAM configs
# ────────────────────────────────────────────────────────────────────────────
info "STEP 12 — Injecting face-auth line into PAM configs..."

inject_pam_line() {
    local pam_file="$1"
    python3 - "$pam_file" <<'PYEOF'
import sys

pam_file = sys.argv[1]
face_line = "auth\tsufficient\tpam_python.so\t/root/facial_lock/facial_pam.py\n"

with open(pam_file, "r") as f:
    lines = f.readlines()

# Skip if already injected
if face_line in lines:
    print(f"  → {pam_file}: already patched, skipping")
    sys.exit(0)

insert_at = None
for i, line in enumerate(lines):
    stripped = line.strip()
    # Skip comment lines and blank lines
    if stripped.startswith("#") or stripped == "":
        continue
    # Insert before the first auth line or @include common-auth
    if stripped.startswith("auth") or stripped.startswith("@include common-auth"):
        insert_at = i
        break

if insert_at is not None:
    lines.insert(insert_at, face_line)
    with open(pam_file, "w") as f:
        f.writelines(lines)
    print(f"  → {pam_file}: patched (inserted at line {insert_at + 1})")
else:
    # Fallback: prepend at top (after any leading comments)
    for i, line in enumerate(lines):
        if not line.strip().startswith("#"):
            lines.insert(i, face_line)
            break
    else:
        lines.insert(0, face_line)
    with open(pam_file, "w") as f:
        f.writelines(lines)
    print(f"  → {pam_file}: patched (prepended)")
PYEOF
}

inject_pam_line /etc/pam.d/sudo
inject_pam_line /etc/pam.d/gdm-password

if [ -f /etc/pam.d/polkit-1 ]; then
    inject_pam_line /etc/pam.d/polkit-1
fi

ok "PAM configs modified"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 13 — Startup the service to cache models
# ────────────────────────────────────────────────────────────────────────────
info "STEP 13 — Starting FaceLock service to load and cache models..."
systemctl stop facial_lock.service || true
systemctl enable facial_lock.service >/dev/null 2>&1
systemctl start facial_lock.service
info "         Please wait, models are loading (~25s first time)..."

# Wait for daemon ready
for i in {1..60}; do
    if grep -q "daemon ready" /root/facial_lock/logs/facial_lock.log 2>/dev/null; then
        ok "FaceLock daemon is ready!"
        break
    fi
    sleep 1
done

# ────────────────────────────────────────────────────────────────────────────
# STEP 14 — Run a live face recognition test
# ────────────────────────────────────────────────────────────────────────────
info "STEP 14 — Running face recognition test..."
info "         Look at your webcam for up to 5 seconds..."
echo ""

# We will test the daemon via our python client or CLI
set +e
/root/facial_lock/venv/bin/python3 /root/facial_lock/recognize_pam.py
TEST_CODE=$?
set -e

if [ $TEST_CODE -eq 0 ]; then
    ok "TEST PASSED — Face recognition is working!"
else
    info "TEST NOTE — Face not detected (exit code: $TEST_CODE)."
    info "    This may be OK (bad lighting / angle at install time)."
    info "    Run 'sudo facial_lock test' anytime to retest."
    echo "    Recent log:"
    tail -5 /root/facial_lock/logs/facial_lock.log 2>/dev/null | sed 's/^/    /'
fi
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 15 — Final summary
# ────────────────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════"
echo "  FACELOCK INSTALLATION COMPLETE"
echo "════════════════════════════════════════════"
echo ""
echo "  Run: sudo facial_lock on    → to activate"
echo "  Run: sudo facial_lock test  → to test camera"
echo "  Run: sudo facial_lock off   → to deactivate"
echo "  Run: sudo facial_lock status → to view log"
echo ""
echo "  PAM backups saved at:"
echo "    /etc/pam.d/sudo.facial_lock_backup"
echo "    /etc/pam.d/gdm-password.facial_lock_backup"
echo ""
echo "  If anything breaks, restore with:"
echo "    sudo cp /etc/pam.d/sudo.facial_lock_backup /etc/pam.d/sudo"
echo "    sudo cp /etc/pam.d/gdm-password.facial_lock_backup /etc/pam.d/gdm-password"
echo ""
echo "════════════════════════════════════════════"
