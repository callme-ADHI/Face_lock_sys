#!/usr/bin/env python3
"""
FaceLock Daemon — loads MTCNN + FaceNet ONCE, serves auth requests via Unix socket.
Each sudo/login just connects to socket: no model reload, ~2s per auth.
"""
import os, sys, time, socket, json, logging
import numpy as np

os.environ["OPENCV_VIDEOIO_PRIORITY_BACKEND"] = "4"
os.environ["MPLBACKEND"] = "Agg"

SOCKET_PATH = "/run/facial_lock.sock"
FLAG_FILE   = "/var/run/facial_lock.active"
LOG_FILE    = "/root/facial_lock/logs/facial_lock.log"
MODELS_DIR  = "/root/facial_lock/models"
FACENET_DIR = "/root/facial_lock/facenet_weights"
DEFAULT_THR = 0.7
TIMEOUT_SEC = 5

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger("facelock")
sys.stdout = open(os.devnull, "w")
sys.stderr = open(os.devnull, "w")

import torch
import cv2
from facenet_pytorch import MTCNN, InceptionResnetV1


def load_identities():
    ids = []
    for name in os.listdir(MODELS_DIR):
        path = os.path.join(MODELS_DIR, name)
        if not os.path.isdir(path):
            continue
        emb_file = os.path.join(path, "mean_embedding.npy")
        if not os.path.exists(emb_file):
            continue
        emb = np.load(emb_file)
        n = np.linalg.norm(emb)
        if n > 0:
            emb = emb / n
        thr = DEFAULT_THR
        meta = os.path.join(path, "metadata.json")
        if os.path.exists(meta):
            try:
                thr = float(json.load(open(meta)).get("threshold", DEFAULT_THR))
            except Exception:
                pass
        ids.append({"name": name, "emb": emb, "thr": thr})
        logger.info(f"INFO - Loaded identity: {name} (threshold={thr})")
    return ids


def authenticate(mtcnn, resnet, identities):
    cap = None
    for idx in (0, 1):
        c = cv2.VideoCapture(idx)
        if c.isOpened():
            c.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            c.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap = c
            logger.info(f"INFO - Camera opened /dev/video{idx}")
            break
        c.release()

    if cap is None:
        logger.error("ERROR - No camera available")
        return False

    deadline = time.time() + TIMEOUT_SEC
    matched  = False

    try:
        while time.time() < deadline:
            ret, bgr = cap.read()
            if not ret or bgr is None:
                time.sleep(0.05)
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            try:
                boxes, _ = mtcnn.detect(rgb)
            except Exception:
                time.sleep(0.05)
                continue
            if boxes is None or len(boxes) == 0:
                time.sleep(0.05)
                continue

            x1, y1, x2, y2 = (int(v) for v in boxes[0])
            h, w = rgb.shape[:2]
            x1, y1, x2, y2 = max(0,x1), max(0,y1), min(w,x2), min(h,y2)
            if x2 <= x1 or y2 <= y1:
                continue

            face = cv2.resize(rgb[y1:y2, x1:x2], (160, 160))
            ft   = torch.tensor(face).permute(2,0,1).float()
            ft   = (ft / 127.5) - 1.0
            ft   = ft.unsqueeze(0)

            with torch.no_grad():
                emb = resnet(ft).detach().numpy()[0]
            n = np.linalg.norm(emb)
            if n > 0:
                emb = emb / n

            best, best_id = -1.0, None
            for identity in identities:
                s = float(np.dot(emb, identity["emb"]))
                if s > best:
                    best, best_id = s, identity

            if best_id and best >= best_id["thr"]:
                logger.info(f"SUCCESS - {best_id['name']} matched (similarity: {best:.3f})")
                matched = True
                break
    finally:
        cap.release()

    if not matched:
        logger.info("FAIL - No match (timeout)")
    return matched


def main():
    logger.info("INFO - FaceLock daemon starting — loading models...")
    torch.hub.set_dir(FACENET_DIR)
    mtcnn  = MTCNN(keep_all=False, device="cpu")
    resnet = InceptionResnetV1(pretrained="vggface2").eval()
    logger.info("INFO - Models loaded. Daemon ready.")

    identities = load_identities()
    if not identities:
        logger.error("ERROR - No identities found — exiting")
        sys.exit(1)

    # Create socket
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    srv.listen(5)

    # Create flag file NOW — socket is ready, PAM module can connect
    FLAG_FILE = "/var/run/facial_lock.active"
    with open(FLAG_FILE, "w") as f:
        pass
    logger.info(f"INFO - Listening on {SOCKET_PATH} — daemon ready")

    import signal
    def _cleanup(sig, frame):
        try: os.unlink(SOCKET_PATH)
        except Exception: pass
        try: os.unlink(FLAG_FILE)
        except Exception: pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    while True:
        try:
            conn, _ = srv.accept()
            logger.info("INFO - Auth request received")
            ok = authenticate(mtcnn, resnet, identities)
            conn.sendall(b"OK\n" if ok else b"FAIL\n")
            conn.close()
        except Exception as e:
            logger.error(f"ERROR - Daemon loop: {e}")


if __name__ == "__main__":
    main()
