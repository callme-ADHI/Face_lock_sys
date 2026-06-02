import os
os.environ["OPENCV_VIDEOIO_PRIORITY_BACKEND"] = "4"
os.environ["MPLBACKEND"] = "Agg"

import sys
import time
import json
import logging
import numpy as np

# ──────────────────────────────────────────────
# Logging setup — ONLY to file, NEVER to stdout
# ──────────────────────────────────────────────
LOG_FILE = "/root/facial_lock/logs/facial_lock.log"
FALLBACK_LOG = "/tmp/facial_lock_debug.log"

def _build_logger() -> logging.Logger:
    """Try to log to /root/facial_lock/logs/; fall back to /tmp/ if not writable."""
    log = logging.getLogger("facelock")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for path in (LOG_FILE, FALLBACK_LOG):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            h = logging.FileHandler(path)
            h.setFormatter(fmt)
            log.addHandler(h)
            return log
        except Exception:
            continue
    # Last resort: discard all logs (never crash because of logging)
    log.addHandler(logging.NullHandler())
    return log

logger = _build_logger()

# Redirect stdout and stderr to /dev/null — PAM will break if anything is printed
sys.stdout = open(os.devnull, "w")
sys.stderr = open(os.devnull, "w")

# ──────────────────────────────────────────────
# Late imports (after stdout/stderr are silenced)
# ──────────────────────────────────────────────
try:
    import torch
    import cv2
    from facenet_pytorch import MTCNN, InceptionResnetV1
except Exception as e:
    logger.error(f"ERROR - Import failed: {e}")
    sys.exit(1)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
MODELS_DIR        = "/root/facial_lock/models"
FACENET_CACHE_DIR = "/root/facial_lock/facenet_weights"
TIMEOUT_SECONDS   = 12
DEFAULT_THRESHOLD = 0.7
FRAME_W           = 640
FRAME_H           = 480

# ──────────────────────────────────────────────
# Load all identity models from MODELS_DIR
# ──────────────────────────────────────────────
def load_identities(models_dir: str) -> list:
    identities = []
    if not os.path.isdir(models_dir):
        logger.error(f"ERROR - Models directory not found: {models_dir}")
        return identities

    for identity_name in os.listdir(models_dir):
        identity_path = os.path.join(models_dir, identity_name)
        if not os.path.isdir(identity_path):
            continue

        mean_emb_path = os.path.join(identity_path, "mean_embedding.npy")
        meta_path     = os.path.join(identity_path, "metadata.json")

        if not os.path.exists(mean_emb_path):
            logger.info(f"WARN - Skipping {identity_name}: no mean_embedding.npy")
            continue

        mean_emb = np.load(mean_emb_path)
        # Ensure mean embedding is L2 normalised
        norm = np.linalg.norm(mean_emb)
        if norm > 0:
            mean_emb = mean_emb / norm

        threshold = DEFAULT_THRESHOLD
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
                threshold = float(meta.get("threshold", DEFAULT_THRESHOLD))
            except Exception as e:
                logger.info(f"WARN - Could not read metadata for {identity_name}: {e}")

        identities.append({
            "name":           identity_name,
            "mean_embedding": mean_emb,
            "threshold":      threshold,
        })
        logger.info(f"INFO - Loaded identity: {identity_name} (threshold={threshold})")

    return identities


# ──────────────────────────────────────────────
# Open webcam — try /dev/video0 then /dev/video1
# ──────────────────────────────────────────────
def open_camera() -> cv2.VideoCapture:
    for index in (0, 1):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
            logger.info(f"INFO - Camera opened at /dev/video{index}")
            return cap
        cap.release()
    return None


# ──────────────────────────────────────────────
# Main recognition loop
# ──────────────────────────────────────────────
def main():
    logger.info("INFO - PAM invoked recognize_pam.py — starting face auth")
    identities = load_identities(MODELS_DIR)
    if not identities:
        logger.error("ERROR - No valid identity models found")
        sys.exit(1)

    # Set FaceNet weights cache directory
    torch.hub.set_dir(FACENET_CACHE_DIR)

    # Initialise models
    logger.info("INFO - Loading MTCNN + FaceNet models...")
    try:
        mtcnn  = MTCNN(keep_all=False, device="cpu")
        resnet = InceptionResnetV1(pretrained="vggface2").eval()
    except Exception as e:
        logger.error(f"ERROR - Model initialisation failed: {e}")
        sys.exit(1)

    logger.info("INFO - Models loaded. Opening camera...")
    cap = open_camera()
    if cap is None:
        logger.error("ERROR - Could not open any camera device")
        sys.exit(1)

    deadline = time.time() + TIMEOUT_SECONDS

    try:
        while time.time() < deadline:
            ret, frame_bgr = cap.read()
            if not ret or frame_bgr is None:
                time.sleep(0.05)
                continue

            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

            # Face detection
            try:
                boxes, probs = mtcnn.detect(frame_rgb)
            except Exception:
                time.sleep(0.05)
                continue

            if boxes is None or len(boxes) == 0:
                time.sleep(0.05)
                continue

            # Use the first (highest-confidence) detected face
            box = boxes[0]
            x1, y1, x2, y2 = (int(v) for v in box)

            # Clamp to frame boundaries
            h, w = frame_rgb.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            if x2 <= x1 or y2 <= y1:
                continue

            # Face preprocessing
            face_crop    = frame_rgb[y1:y2, x1:x2]
            face_resized = cv2.resize(face_crop, (160, 160))
            face_tensor  = torch.tensor(face_resized).permute(2, 0, 1).float()
            face_tensor  = (face_tensor / 127.5) - 1.0
            face_tensor  = face_tensor.unsqueeze(0)

            # Embedding
            try:
                with torch.no_grad():
                    embedding = resnet(face_tensor).detach().numpy()[0]
            except Exception as e:
                logger.error(f"ERROR - Embedding failed: {e}")
                continue

            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm

            # Compare against all loaded identities
            best_score    = -1.0
            best_identity = None
            for identity in identities:
                similarity = float(np.dot(embedding, identity["mean_embedding"]))
                if similarity > best_score:
                    best_score    = similarity
                    best_identity = identity

            if best_identity is not None and best_score >= best_identity["threshold"]:
                logger.info(
                    f"SUCCESS - {best_identity['name']} matched "
                    f"(similarity: {best_score:.3f})"
                )
                cap.release()
                sys.exit(0)

        # Timeout reached — no match
        logger.info("FAIL - No match found (timeout 5s)")
        cap.release()
        sys.exit(1)

    except Exception as e:
        logger.error(f"ERROR - {e}")
        try:
            cap.release()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
