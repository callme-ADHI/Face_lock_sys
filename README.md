# FaceLock — Linux Facial Authentication System (PAM + Daemon)

This project implements a secure, ultra-fast facial authentication system for Linux (specifically tested on Kali Linux/Debian). It integrates directly with the Pluggable Authentication Modules (PAM) stack to allow passwordless or parallel password-and-face login for `sudo`, GDM3 (lock screens), and display managers.

---

## ── Architecture Overview ──

The system is designed with a **Client-Daemon Architecture** to minimize facial recognition latency to under **1.5 seconds**. 

```mermaid
graph TD
    A[Sudo / Lock Screen / GDM] --> B[libpam-python / facial_pam.py]
    B -->|Unix Socket| C[facial_lock_daemon.py]
    C -->|Loads once at boot| D[MTCNN + FaceNet Models]
    C -->|Accesses Camera| E[/dev/video0]
    C -->|Auth Status OK/FAIL| B
    B -->|Success| F[Granted Access]
    B -->|Fail| G[Fallback to Password]
```

### 1. The Daemon (`facial_lock_daemon.py`)
In traditional facial recognition setups, launching a Python script that imports PyTorch and OpenCV takes 8–10 seconds because the heavy deep learning models must be read from disk on every execution. 
* **The Solution**: Our system runs a persistent systemd service daemon (`facial_lock.service`) that boots once at startup. It loads PyTorch, MTCNN (face detection), and FaceNet (feature extraction) into RAM, keeping them warm.
* **Communication**: It listens on a local Unix socket (`/run/facial_lock.sock`). When a PAM event occurs, it immediately opens the webcam, runs inference in ~1 second, sends the result (`OK` or `FAIL`), and shuts off the camera.

### 2. The PAM Module (`facial_pam.py`)
Loaded directly in-process by PAM using `libpam-python` (`pam_python.so`), bypassing system execution policies and sandboxes.
* **Dual-Mode Operation**:
  * **Interactive Terminal Context (Sudo)**: Uses a custom non-blocking TTY password grabber. It displays the `[sudo] password` prompt immediately while starting the camera in the background. If your face matches, it instantly logs you in (zero keys pressed). If it fails or times out, it captures your typed password and forwards it safely to `pam_unix.so` (so you never wait).
  * **GUI Context (GDM3 / Lock Screen)**: Detects it is a graphical interface. It runs sequential face verification in the background, unlocking the desktop automatically on success, and falling back to the standard password text box on failure.

---

## ── Libraries & Core Technologies ──

### 1. PyTorch (`torch`)
* **Role**: Deep learning backend.
* **Use**: Serves as the compute engine for running FaceNet (InceptionResnetV1) model calculations. Runs on CPU.

### 2. OpenCV (`cv2`)
* **Role**: Image and hardware pipeline.
* **Use**: Grabs raw video frames from the webcam device (`/dev/video0`) and handles colorspace conversions (BGR to RGB) and face cropping/resizing (160x160 pixels).

### 3. MTCNN (`facenet_pytorch.MTCNN`)
* **Role**: Multi-task Cascaded Convolutional Networks.
* **Use**: Detects where a face is located in the camera frame, providing bounding boxes (`x1, y1, x2, y2`) so we can crop out background noise before running verification.

### 4. FaceNet (`facenet_pytorch.InceptionResnetV1`)
* **Role**: Deep Face Recognition Model (pretrained on `vggface2`).
* **Use**: Generates a **512-dimensional vector embedding** (mathematical representation) of the cropped face. 
* **Matching**: We compute the dot product (cosine similarity) between this embedding and your registered face template. If similarity exceeds the user threshold (e.g., `0.70`), access is granted.

---

## ── Downloaded Components & Storage Paths ──

All files are structured under `/root/facial_lock/` to secure them from non-root access:

| Path | Component | Purpose |
| :--- | :--- | :--- |
| `/root/facial_lock/venv/` | Python Virtual Environment | Contains isolated installations of `torch`, `opencv-python`, and `facenet-pytorch`. |
| `/root/facial_lock/facenet_weights/` | Model Weights Cache | Stores the pre-downloaded weights of the FaceNet AI model, avoiding internet lookups. |
| `/root/facial_lock/models/` | User Templates | Folder containing subdirectories for each registered user's face signatures. |
| `/run/facial_lock.sock` | Unix Domain Socket | Communication bridge between GDM/PAM client and the running daemon. |
| `/var/run/facial_lock.active` | Control Flag | Simple file that acts as an enable/disable switch for PAM checks. |

---

## ── Where to Place Your Face Models ──

If you train a new face template (using your Net pipeline), it produces a `mean_embedding.npy` file. You must place it here:

```
/root/facial_lock/models/
└── <USERNAME>/
    ├── mean_embedding.npy
    └── metadata.json
```

### Steps to paste a new model:
1. Ensure the directory matches your Linux username (case-sensitive, e.g., `ADHI` or `adhi`).
2. Copy your NumPy embedding file:
   ```bash
   sudo cp ~/Desktop/Facenet-training-pipeline/models/ADHI/mean_embedding.npy /root/facial_lock/models/ADHI/mean_embedding.npy
   ```
3. Set appropriate root-only permissions:
   ```bash
   sudo chmod 644 /root/facial_lock/models/ADHI/mean_embedding.npy
   ```

*(Optional)* You can add a `metadata.json` next to it to customize the similarity threshold for that specific user:
```json
{
  "threshold": 0.75
}
```

---

## ── Configuration & Troubleshooting ──

### 1. System Control Command
Enable or disable the system at any time with the official CLI:
```bash
sudo facial_lock status  # Check status & last logs
sudo facial_lock on      # Enable facial recognition system-wide
sudo facial_lock off     # Disable facial recognition
sudo facial_lock test    # Runs a quick manual camera test
```

### 2. Manual Log Inspection
To view logs in real-time or diagnose daemon startup issues:
* Daemon Logs: `sudo tail -f /root/facial_lock/logs/facial_lock.log`
* PAM Debug Logs: `cat /tmp/facial_lock_debug.log`
