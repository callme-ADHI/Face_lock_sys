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

# Allow passing the model directory as an argument (e.g., /path/to/models/)
# Or look in current directory "./models"
MODEL_SRC_DIR=""
if [ $# -gt 0 ] && [ -d "$1" ]; then
    MODEL_SRC_DIR="$1"
elif [ -d "./models" ]; then
    MODEL_SRC_DIR="./models"
elif [ -d "/home/adhi/Desktop/Facenet-training-pipeline/models" ]; then
    MODEL_SRC_DIR="/home/adhi/Desktop/Facenet-training-pipeline/models"
fi

if [ -z "$MODEL_SRC_DIR" ]; then
    echo "[✗] Error: No face model directory found."
    echo "    Please place your trained user model folder (containing mean_embedding.npy) inside a folder named 'models/' in this directory,"
    echo "    or specify the path as an argument:"
    echo "    sudo bash install.sh /path/to/models/"
    exit 1
fi

ok "Face model directory found at: $MODEL_SRC_DIR"
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
# STEP 3 — Copy trained models
# ────────────────────────────────────────────────────────────────────────────
info "STEP 3 — Copying trained model templates..."
# Copy all user subdirectories from the model source dir to /root/facial_lock/models/
cp -r "$MODEL_SRC_DIR"/* /root/facial_lock/models/
ok "Trained models copied to /root/facial_lock/models/"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# STEP 4 — Set up Python virtual environment
# ────────────────────────────────────────────────────────────────────────────
info "STEP 4 — Setting up Python virtual environment..."

VENV_SRC=""
if [ -d "./venv" ]; then
    VENV_SRC="./venv"
elif [ -d "/home/adhi/Desktop/Facenet-training-pipeline/venv" ]; then
    VENV_SRC="/home/adhi/Desktop/Facenet-training-pipeline/venv"
fi

rm -rf /root/facial_lock/venv

# Install python3-venv if not present
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y python3-venv python3-pip python3-dev >/dev/null 2>&1 || true

info "Creating clean virtual environment in /root/facial_lock/venv..."
python3 -m venv /root/facial_lock/venv

if [ -n "$VENV_SRC" ]; then
    info "Found existing Python virtual environment at $VENV_SRC."
    info "Copying installed packages (saves download time and disk space)..."
    SRC_LIB_DIR=$(ls -d "$VENV_SRC"/lib/python3.* 2>/dev/null | head -n 1 || true)
    DST_LIB_DIR=$(ls -d /root/facial_lock/venv/lib/python3.* 2>/dev/null | head -n 1 || true)

    if [ -n "$SRC_LIB_DIR" ] && [ -n "$DST_LIB_DIR" ] && [ -d "$SRC_LIB_DIR/site-packages" ]; then
        cp -r "$SRC_LIB_DIR"/site-packages/* "$DST_LIB_DIR"/site-packages/
        ok "Python site-packages copied successfully"
    else
        info "Failed to locate site-packages in $VENV_SRC. Falling back to fresh pip installation..."
        /root/facial_lock/venv/bin/pip install --upgrade pip >/dev/null 2>&1 || true
        /root/facial_lock/venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu >/dev/null 2>&1
        /root/facial_lock/venv/bin/pip install opencv-python facenet-pytorch numpy >/dev/null 2>&1
        ok "Python dependencies installed successfully"
    fi
else
    info "No existing Python virtual environment found. Installing Python dependencies..."
    /root/facial_lock/venv/bin/pip install --upgrade pip >/dev/null 2>&1 || true
    /root/facial_lock/venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu >/dev/null 2>&1
    /root/facial_lock/venv/bin/pip install opencv-python facenet-pytorch numpy >/dev/null 2>&1
    ok "Python dependencies installed successfully"
fi
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

# Skip if already injected (ignoring space vs tab differences)
normalized_lines = [' '.join(l.split()) for l in lines]
normalized_face_line = ' '.join(face_line.split())
if normalized_face_line in normalized_lines:
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
