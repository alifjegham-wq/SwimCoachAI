#!/usr/bin/env python3
"""
SwimLens local launcher.

Starts a tiny local web server that:
  * serves swimlens.html in your browser
  * stores your Anthropic API key in swimlens_config.json (next to this file)
  * forwards the analysis request to Anthropic from here (server side), so the
    browser never makes a cross-origin call -> no CORS / file:// problems.

Nothing is sent anywhere except directly to https://api.anthropic.com.
Requires only Python 3 (standard library) -- no pip installs.
"""

import os
import sys
import json
import hashlib
import socket
import base64
import tempfile
import threading
import webbrowser
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(os.path.dirname(HERE), "index.html")  # serve the shared repo-root app
CONFIG_PATH = os.path.join(HERE, "swimlens_config.json")
HISTORY_PATH = os.path.join(HERE, "swimlens_history.json")
PROFILES_PATH = os.path.join(HERE, "swimlens_profiles.json")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
YOLO_MODEL = os.environ.get("SWIMLENS_YOLO_MODEL", "yolo11l-pose.pt")   # pose model: detection + keypoints + tracking in one pass
YOLO_TRACKER = os.environ.get("SWIMLENS_TRACKER", "botsort.yaml")  # BoT-SORT > ByteTrack for identity through turns


# ===================== robust single-swimmer tracking (YOLO + ByteTrack) =====================
# Detects every person with YOLO, assigns persistent IDs with ByteTrack, then follows the ONE
# track the user picked from start to finish. Returns per-frame crops of that swimmer.
#
# Heavy deps (ultralytics -> torch). Installed once via "Install AI Tracking (one-time).bat".
# If unavailable, /api/track returns ok:false and the browser falls back to its in-browser tracker.

def _iter_track_real(path):
    """Yield (frame_idx, image_bgr, [(track_id,(x1,y1,x2,y2)), ...]) using YOLO+ByteTrack."""
    from ultralytics import YOLO  # noqa
    model = YOLO(YOLO_MODEL)
    idx = 0
    for r in model.track(source=path, stream=True, classes=[0],
                         tracker="bytetrack.yaml", persist=True, verbose=False):
        dets = []
        b = getattr(r, "boxes", None)
        if b is not None and b.id is not None:
            xyxy = b.xyxy.cpu().numpy()
            ids = b.id.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), tid in zip(xyxy, ids):
                dets.append((int(tid), (float(x1), float(y1), float(x2), float(y2))))
        yield idx, r.orig_img, dets
        idx += 1


def _iter_track_fake(path):
    """Test-only tracker: follows the brightest blob (no YOLO). Lets us verify the full
    sampling/selection/crop/HTTP pipeline without torch. Enabled via SWIMLENS_FAKE_TRACK=1."""
    import cv2
    cap = cv2.VideoCapture(path)
    idx = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        dets = []
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            dets.append((1, (float(x), float(y), float(x + w), float(y + h))))
        yield idx, img, dets
        idx += 1
    cap.release()


def run_tracking(payload):
    import cv2
    fake = bool(os.environ.get("SWIMLENS_FAKE_TRACK"))
    ref_time = float(payload.get("ref_time", 0) or 0)
    pick = payload.get("pick") or {"x": 0.5, "y": 0.5}
    interval = float(payload.get("interval", 0.2) or 0.2)
    pad = float(payload.get("pad", 0.4) or 0.4)
    maxf = int(payload.get("max_frames", 240) or 240)
    out_w = int(payload.get("width", 480) or 480)

    raw = base64.b64decode(str(payload["video_b64"]).split(",")[-1])
    tf = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tf.write(raw)
    tf.close()
    try:
        cap = cv2.VideoCapture(tf.name)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        W = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1.0
        H = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1.0
        cap.release()
        if not fps or fps != fps:  # NaN guard
            fps = 25.0

        gen = _iter_track_fake(tf.name) if fake else _iter_track_real(tf.name)
        samples = []          # {time, img(downscaled bgr), boxes:{id:(x1,y1,x2,y2)}, scale}
        ref_boxes, ref_dt = {}, 1e18
        ids_seen = set()
        next_t = 0.0
        for idx, img, dets in gen:
            t = idx / fps
            boxes = {tid: box for tid, box in dets}
            ids_seen.update(boxes.keys())
            if abs(t - ref_time) < ref_dt:
                ref_dt = abs(t - ref_time); ref_boxes = boxes
            if t + 1e-6 >= next_t and len(samples) < maxf:
                scale = out_w / float(img.shape[1] or out_w)
                small = cv2.resize(img, (out_w, max(1, int(img.shape[0] * scale))))
                samples.append({"time": round(t, 2), "img": small, "boxes": boxes, "scale": scale})
                next_t += interval
            if len(samples) >= maxf and t > ref_time:
                break

        # choose the target track id from the reference frame using the picked point
        px, py = pick["x"] * W, pick["y"] * H
        target_id, best = None, 1e30
        for tid, (x1, y1, x2, y2) in ref_boxes.items():
            inside = (x1 <= px <= x2) and (y1 <= py <= y2)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            score = (0 if inside else 1e9) + (cx - px) ** 2 + (cy - py) ** 2
            if score < best:
                best, target_id = score, tid
        if target_id is None:
            return {"ok": False, "error": "no_target",
                    "message": "Couldn't find a tracked person at the reference frame. Try a clearer reference frame."}

        frames_out, kept = [], 0
        for s in samples:
            box = s["boxes"].get(target_id)
            if not box:
                continue  # target not detected this frame (occlusion/turn) -> skip rather than risk wrong kid
            sc = s["scale"]
            x1, y1, x2, y2 = [v * sc for v in box]
            bw, bh = (x2 - x1), (y2 - y1)
            cx1 = max(0, x1 - bw * pad); cy1 = max(0, y1 - bh * pad)
            cx2 = min(s["img"].shape[1], x2 + bw * pad); cy2 = min(s["img"].shape[0], y2 + bh * pad)
            crop = s["img"][int(cy1):int(cy2), int(cx1):int(cx2)]
            if crop.size == 0:
                continue
            ok2, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 72])
            if not ok2:
                continue
            frames_out.append({
                "time": s["time"],
                "box": [round(cx1 / s["img"].shape[1], 4), round(cy1 / s["img"].shape[0], 4),
                        round((cx2 - cx1) / s["img"].shape[1], 4), round((cy2 - cy1) / s["img"].shape[0], 4)],
                "crop": base64.b64encode(buf.tobytes()).decode("ascii"),
            })
            kept += 1
        return {"ok": True, "fps": round(fps, 2), "target_id": int(target_id),
                "n_tracks": len(ids_seen), "frames": frames_out, "kept": kept,
                "note": "fake" if fake else "yolo+bytetrack"}
    finally:
        try:
            os.remove(tf.name)
        except Exception:
            pass


# ----- frame-based tracker (current): browser sends downscaled, strided frames -----
# Avoids uploading the whole video, controls resolution/stride, and keeps identity with
# ByteTrack (persist=True per frame) plus position-prediction re-acquisition through turns.
def _dets_fake(decoded):
    import cv2
    boxes_out, kpts_out = [], []
    for _t, img in decoded:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = {}
        if cnts:
            c = max(cnts, key=cv2.contourArea); x, y, w, h = cv2.boundingRect(c)
            boxes[1] = (float(x), float(y), float(x + w), float(y + h))
        boxes_out.append(boxes); kpts_out.append({})
    return boxes_out, kpts_out


def _dets_yolo(decoded, model_name=None, tracker=None):
    from ultralytics import YOLO  # noqa  (raises ImportError if not installed -> handled by route)
    model = YOLO(model_name or YOLO_MODEL)
    trk = tracker or YOLO_TRACKER
    boxes_out, kpts_out = [], []
    for _t, img in decoded:
        H, W = img.shape[:2]
        r = model.track(img, persist=True, classes=[0], tracker=trk, verbose=False)
        res = r[0] if isinstance(r, (list, tuple)) else r
        boxes, kpts = {}, {}
        b = getattr(res, "boxes", None)
        if b is not None and getattr(b, "id", None) is not None:
            xyxy = b.xyxy.cpu().numpy(); ids = b.id.cpu().numpy().astype(int)
            kp = getattr(res, "keypoints", None)
            kp_xy = kp.data.cpu().numpy() if (kp is not None and getattr(kp, "data", None) is not None) else None
            for n, ((x1, y1, x2, y2), tid) in enumerate(zip(xyxy, ids)):
                tid = int(tid)
                boxes[tid] = (float(x1), float(y1), float(x2), float(y2))
                if kp_xy is not None and n < len(kp_xy):
                    kpts[tid] = [[float(px) / W, float(py) / H, float(pc)] for (px, py, pc) in kp_xy[n]]
        boxes_out.append(boxes); kpts_out.append(kpts)
    return boxes_out, kpts_out


def _aspect_excess(box):
    """0 for a swimmer (wide/flat in the water); grows for a tall, upright (standing) person."""
    w = box[2] - box[0]; h = box[3] - box[1]
    if w <= 1:
        return 5.0
    return max(0.0, (h / w) - 1.1)


def _pick_target(ref_boxes, px, py, W):
    target_id, best = None, 1e30
    for tid, (x1, y1, x2, y2) in ref_boxes.items():
        ae = _aspect_excess((x1, y1, x2, y2))
        inside = (x1 <= px <= x2) and (y1 <= py <= y2) and ae < 0.6   # a tall standing box doesn't get the 'inside' shortcut
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        pen = ae * (0.55 * W) ** 2   # push standing people far down the ranking
        score = (0 if inside else 1e9) + (cx - px) ** 2 + (cy - py) ** 2 + pen
        if score < best:
            best, target_id = score, tid
    return target_id


def _nearest(boxes, pt, gate):
    best, bd = None, gate * gate
    for tid, (x1, y1, x2, y2) in boxes.items():
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        d = (cx - pt[0]) ** 2 + (cy - pt[1]) ** 2
        if d < bd:
            bd = d; best = (tid, (x1, y1, x2, y2))
    return best


def _color_sig(img, box):
    """Small HSV histogram (cap/suit colour signature) for appearance re-ID."""
    import cv2
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1 = max(0, min(w - 1, x1)); y1 = max(0, min(h - 1, y1))
    x2 = max(x1 + 1, min(w, x2)); y2 = max(y1 + 1, min(h, y2))
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return hist.flatten()


def _sig_dist(a, b):
    if a is None or b is None:
        return 1.0
    import numpy as np
    return float(np.sum(np.abs(a - b))) / 2.0  # 0 (identical) .. 1 (totally different)


def _reacquire(boxes, pred, gate, img, target_sig, target_y=None, target_h=None, H=1.0):
    """Re-lock the target: filter detections by predicted position, lane (vertical band),
    body size, and swimmer shape, then choose the best colour match."""
    cands = []
    for tid, (x1, y1, x2, y2) in boxes.items():
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        bh = y2 - y1
        if (cx - pred[0]) ** 2 + (cy - pred[1]) ** 2 > gate * gate:
            continue
        if target_y is not None and abs(cy - target_y) > 0.16 * H:      # stay in the swimmer's lane band
            continue
        if target_h and (bh < 0.45 * target_h or bh > 2.2 * target_h):  # similar body size
            continue
        cands.append((tid, (x1, y1, x2, y2)))
    if not cands:
        return None
    flat = [c for c in cands if _aspect_excess(c[1]) < 0.5]   # prefer swimmer-shaped (flat)
    pool = flat if flat else cands
    if target_sig is None or len(pool) == 1:
        return min(pool, key=lambda c: (((c[1][0] + c[1][2]) / 2 - pred[0]) ** 2 + ((c[1][1] + c[1][3]) / 2 - pred[1]) ** 2))
    try:
        return min(pool, key=lambda c: _sig_dist(target_sig, _color_sig(img, c[1])))
    except Exception:
        return pool[0]


def run_tracking_frames(payload):
    import numpy as np, cv2
    fake = bool(os.environ.get("SWIMLENS_FAKE_TRACK"))
    frames_in = payload.get("frames") or []
    ref_time = float(payload.get("ref_time", 0) or 0)
    pick = payload.get("pick") or {"x": 0.5, "y": 0.5}
    pad = float(payload.get("pad", 0.4) or 0.4)
    out_w = int(payload.get("crop_width", 360) or 360)
    model_name = payload.get("yolo_model") or None
    tracker = payload.get("tracker") or None
    if not frames_in:
        return {"ok": False, "error": "no_frames", "message": "No frames were received for tracking."}
    decoded = []
    for fr in frames_in:
        try:
            raw = base64.b64decode(str(fr.get("b64", "")).split(",")[-1])
            arr = np.frombuffer(raw, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is not None:
            decoded.append((float(fr.get("time", 0) or 0), img))
    if not decoded:
        return {"ok": False, "error": "decode_failed", "message": "Could not read the frames."}
    H, W = decoded[0][1].shape[:2]
    dets, kpts_all = _dets_fake(decoded) if fake else _dets_yolo(decoded, model_name, tracker)
    ids_seen = set(); ref_boxes, ref_dt = {}, 1e18
    for (t, _img), boxes in zip(decoded, dets):
        ids_seen.update(boxes.keys())
        if abs(t - ref_time) < ref_dt:
            ref_dt = abs(t - ref_time); ref_boxes = boxes
    target_id = _pick_target(ref_boxes, pick["x"] * W, pick["y"] * H, W)
    if target_id is None:
        return {"ok": False, "error": "no_target",
                "message": "No swimmer detected at the reference frame. Try a clearer reference frame, or use the in-browser tracker."}
    cur_id = target_id; last_c = None; last_v = (0.0, 0.0); reacq = 0; target_sig = None
    target_y = None; target_h = None
    n = len(decoded)
    raw = [None] * n        # per-frame box (x1,y1,x2,y2) for the locked swimmer
    raw_id = [None] * n     # the detection id used at each frame (for correct keypoints)
    # ---- Pass 1: lock onto and follow the target frame by frame ----
    for i, (t, img) in enumerate(decoded):
        boxes = dets[i]; box = boxes.get(cur_id)
        # reject an ID-matched box that jumped out of the lane or changed size implausibly
        if box is not None and target_y is not None:
            cyv = (box[1] + box[3]) / 2.0; bhv = box[3] - box[1]
            if abs(cyv - target_y) > 0.22 * H or (target_h and (bhv < 0.4 * target_h or bhv > 2.5 * target_h)):
                box = None
        if box is None and last_c is not None:        # lost the ID -> predict & re-lock
            pred = (last_c[0] + last_v[0], last_c[1] + last_v[1])
            cand = _reacquire(boxes, pred, 0.25 * W, img, target_sig, target_y, target_h, H)
            if cand:
                cur_id, box = cand[0], cand[1]; reacq += 1
        if box is None:
            continue
        raw[i] = box; raw_id[i] = cur_id
        x1, y1, x2, y2 = box; cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0; bh = y2 - y1
        if last_c is not None:                         # smoothed velocity for prediction
            last_v = (0.6 * last_v[0] + 0.4 * (cx - last_c[0]), 0.6 * last_v[1] + 0.4 * (cy - last_c[1]))
        last_c = (cx, cy)
        target_y = cy if target_y is None else (0.85 * target_y + 0.15 * cy)   # lane band (EMA)
        target_h = bh if target_h is None else (0.85 * target_h + 0.15 * bh)   # body size (EMA)
        try:                                           # keep an up-to-date colour signature
            sig = _color_sig(img, box)
            if sig is not None:
                target_sig = sig if target_sig is None else (0.8 * target_sig + 0.2 * sig)
        except Exception:
            pass
    # ---- Pass 2: bridge short gaps so brief misses still produce frames ----
    known = [i for i in range(n) if raw[i] is not None]
    interp = set()
    for a, b in zip(known, known[1:]):
        if 1 < (b - a) <= 7:
            for k in range(a + 1, b):
                f = (k - a) / float(b - a)
                raw[k] = tuple(raw[a][j] + (raw[b][j] - raw[a][j]) * f for j in range(4))
                interp.add(k)
    # ---- Pass 3: crop & encode along the cleaned trajectory ----
    frames_out = []
    for i, (t, img) in enumerate(decoded):
        box = raw[i]
        if box is None:
            continue
        x1, y1, x2, y2 = box; bw, bh = (x2 - x1), (y2 - y1)
        ax1 = max(0, x1 - bw * pad); ay1 = max(0, y1 - bh * pad)
        ax2 = min(W, x2 + bw * pad); ay2 = min(H, y2 + bh * pad)
        crop = img[int(ay1):int(ay2), int(ax1):int(ax2)]
        if crop.size == 0:
            continue
        sc = out_w / float(max(1, crop.shape[1]))
        if sc < 1:
            crop = cv2.resize(crop, (out_w, max(1, int(crop.shape[0] * sc))))
        ok2, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok2:
            continue
        tkpts = kpts_all[i].get(raw_id[i]) if (raw_id[i] is not None and i < len(kpts_all)) else None
        frames_out.append({"time": round(t, 2),
            "box": [round(ax1 / W, 4), round(ay1 / H, 4), round((ax2 - ax1) / W, 4), round((ay2 - ay1) / H, 4)],
            "crop": base64.b64encode(buf.tobytes()).decode("ascii"),
            "kpts": tkpts})
    return {"ok": True, "target_id": int(target_id), "n_tracks": len(ids_seen),
            "frames": frames_out, "kept": len(frames_out), "reacquired": reacq, "interpolated": len(interp),
            "has_pose": any(f.get("kpts") for f in frames_out),
            "note": "fake" if fake else ("yolo11-pose+" + (tracker or YOLO_TRACKER).replace(".yaml", ""))}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(rows):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def load_profiles():
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"profiles": [], "active": None}


def save_profiles(obj):
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _hash_pass(p):
    return hashlib.sha256(("swimcoachai::" + str(p)).encode("utf-8")).hexdigest()


def find_free_port(preferred=8765):
    for port in [preferred] + list(range(8766, 8800)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


class Handler(BaseHTTPRequestHandler):
    # keep the console quiet
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        return raw

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/swimlens.html"):
            try:
                with open(HTML_PATH, "rb") as f:
                    html = f.read()
            except FileNotFoundError:
                self._send(500, "<h1>swimlens.html not found</h1><p>Keep swimlens.html in the same folder as this launcher.</p>", "text/html")
                return
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/config":
            cfg = load_config()
            self._send(200, {"hasKey": bool(cfg.get("apiKey")), "hasPasscode": bool(cfg.get("passHash"))})
            return
        if path == "/api/track/status":
            try:
                import importlib.util
                ready = importlib.util.find_spec("ultralytics") is not None
            except Exception:
                ready = False
            self._send(200, {"installed": ready})
            return
        if path == "/api/history":
            self._send(200, {"sessions": load_history()})
            return
        if path == "/api/profiles":
            self._send(200, load_profiles())
            return
        self._send(404, {"error": "not found"})

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/key":
            try:
                data = json.loads(self._read_body() or b"{}")
            except Exception:
                self._send(400, {"error": "bad json"})
                return
            key = (data.get("apiKey") or "").strip()
            cfg = load_config()
            if key:
                cfg["apiKey"] = key
            else:
                cfg.pop("apiKey", None)
            try:
                save_config(cfg)
            except Exception as e:
                self._send(500, {"error": "could not save config: %s" % e})
                return
            self._send(200, {"ok": True, "hasKey": bool(cfg.get("apiKey"))})
            return

        if path == "/api/passcode":
            try:
                data = json.loads(self._read_body() or b"{}")
            except Exception:
                self._send(400, {"ok": False, "error": "bad_json"})
                return
            action = (data.get("action") or "").strip()
            cfg = load_config()
            stored = cfg.get("passHash")
            cand = data.get("passcode") or ""
            current = data.get("current") or ""
            if action == "verify":
                self._send(200, {"ok": bool(stored) and _hash_pass(cand) == stored})
                return
            if action == "set":
                if stored and _hash_pass(current) != stored:
                    self._send(200, {"ok": False, "error": "bad_current"})
                    return
                if not str(cand).strip():
                    self._send(200, {"ok": False, "error": "empty"})
                    return
                cfg["passHash"] = _hash_pass(cand)
                try:
                    save_config(cfg)
                except Exception as e:
                    self._send(500, {"ok": False, "error": str(e)})
                    return
                self._send(200, {"ok": True, "hasPasscode": True})
                return
            if action == "clear":
                if stored and _hash_pass(current) != stored:
                    self._send(200, {"ok": False, "error": "bad_current"})
                    return
                cfg.pop("passHash", None)
                try:
                    save_config(cfg)
                except Exception as e:
                    self._send(500, {"ok": False, "error": str(e)})
                    return
                self._send(200, {"ok": True, "hasPasscode": False})
                return
            self._send(400, {"ok": False, "error": "bad_action"})
            return

        if path == "/api/profiles":
            try:
                data = json.loads(self._read_body() or b"{}")
            except Exception:
                self._send(400, {"ok": False, "error": "bad_json"})
                return
            obj = load_profiles()
            if data.get("clear"):
                obj = {"profiles": [], "active": None}
            else:
                if isinstance(data.get("profiles"), list):
                    obj["profiles"] = data["profiles"][:200]
                if "active" in data:
                    obj["active"] = data["active"]
            try:
                save_profiles(obj)
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
                return
            self._send(200, {"ok": True, **obj})
            return

        if path == "/api/history":
            try:
                data = json.loads(self._read_body() or b"{}")
            except Exception:
                self._send(400, {"error": "bad_json"})
                return
            rows = load_history()
            if data.get("clear"):
                rows = []
            elif data.get("session"):
                rows.append(data["session"])
                rows = rows[-200:]
            try:
                save_history(rows)
            except Exception as e:
                self._send(500, {"error": "could not save history: %s" % e})
                return
            self._send(200, {"ok": True, "sessions": rows})
            return

        if path == "/api/track":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception:
                self._send(400, {"ok": False, "error": "bad_json"})
                return
            try:
                result = run_tracking_frames(payload)
                self._send(200, result)
            except ImportError:
                self._send(200, {"ok": False, "error": "not_installed",
                    "message": "Robust AI tracking isn't installed yet. Run 'Install AI Tracking (one-time).bat' on your computer, then try again. Using the in-browser tracker for now."})
            except Exception as e:
                self._send(200, {"ok": False, "error": "track_failed", "message": str(e)})
            return

        if path == "/api/analyze":
            body = self._read_body()
            # key: prefer a per-request header, else the saved config
            key = (self.headers.get("x-swimlens-key") or "").strip()
            if not key:
                key = (load_config().get("apiKey") or "").s