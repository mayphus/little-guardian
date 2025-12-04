import os
import threading
import time
from urllib.parse import urlparse

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request
from onvif import ONVIFCamera
from dotenv import load_dotenv

load_dotenv()

RTSP_URL = os.getenv("RTSP_URL")
if not RTSP_URL:
    raise ValueError("RTSP_URL not set in .env")

parsed = urlparse(RTSP_URL)
CAM_HOST = os.getenv("ONVIF_HOST", parsed.hostname or "")
CAM_RTSP_PORT = parsed.port or 554
CAM_ONVIF_PORT = int(os.getenv("ONVIF_PORT", "80"))
CAM_USER = os.getenv("ONVIF_USER", parsed.username or "")
CAM_PASS = os.getenv("ONVIF_PASS", parsed.password or "")
PTZ_PULSE_SECONDS = float(os.getenv("PTZ_PULSE_SECONDS", "0.4"))

app = Flask(__name__)


class FrameGrabber:
    def __init__(self, url: str):
        self.url = url
        self.frame = None
        self.raw_frame = None
        self.lock = threading.Lock()
        self.running = True
        self.cap = None
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _open_capture(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        # Set timeout to 5 seconds (5000000 us)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "stimeout;5000000"
        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            print(f"Failed to open capture: {self.url}")

    def _loop(self):
        backoff = 1.0
        errors = 0
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                self._open_capture()
                if not self.cap or not self.cap.isOpened():
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue
                backoff = 1.0
                errors = 0

            ok, frame = self.cap.read()
            if not ok or frame is None:
                errors += 1
                time.sleep(0.1)
                if errors > 10:  # Reconnect after 1 second of failures
                    print("Too many read errors, reconnecting...")
                    self._open_capture()
                    errors = 0
                continue
            
            if ok:
                # Store raw frame for tracker
                with self.lock:
                    self.raw_frame = frame.copy()

                # Draw HUD using latest tracker results (non-blocking)
                if tracker.enabled:
                    tracker.draw_overlay(frame)

                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    with self.lock:
                        self.frame = buf.tobytes()
            time.sleep(0.01)

    def get_frame(self):
        with self.lock:
            return self.frame

    def get_raw_frame(self):
        with self.lock:
            return self.raw_frame if self.raw_frame is not None else None

    def stop(self):
        self.running = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass


grabber = FrameGrabber(RTSP_URL)


def mjpeg_stream():
    while True:
        frame = grabber.get_frame()
        if frame:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode()
                + frame
                + b"\r\n"
            )
        else:
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\nno frame\r\n"
        time.sleep(0.05)


def _build_ptz():
    try:
        cam = ONVIFCamera(
            CAM_HOST,
            CAM_ONVIF_PORT,
            CAM_USER,
            CAM_PASS,
        )
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            return None, None, "No ONVIF profiles found"
        token = profiles[0].token
        ptz = cam.create_ptz_service()
        return ptz, token, None
    except Exception as exc:
        return None, None, str(exc)


def _ptz_move(action: str):
    ptz, token, err = _build_ptz()
    if err:
        return False, err
    if action == "home":
        try:
            ptz.GotoHomePosition({"ProfileToken": token})
            return True, "Moved home"
        except Exception as exc:
            return False, str(exc)
    if action == "stop":
        try:
            ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})
            return True, "Stopped"
        except Exception as exc:
            return False, str(exc)

    moves = {
        "left": {"pan": -0.5},
        "right": {"pan": 0.5},
        "up": {"tilt": 0.4},
        "down": {"tilt": -0.4},
        "zoom_in": {"zoom": 0.4},
        "zoom_out": {"zoom": -0.4},
    }
    move = moves.get(action)
    if not move:
        return False, f"Unknown action {action}"

    velocity = {}
    if "pan" in move or "tilt" in move:
        velocity["PanTilt"] = {"x": move.get("pan", 0), "y": move.get("tilt", 0)}
    if "zoom" in move:
        velocity["Zoom"] = {"x": move.get("zoom", 0)}

    try:
        ptz.ContinuousMove({"ProfileToken": token, "Velocity": velocity})
        time.sleep(PTZ_PULSE_SECONDS)
        ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})
        return True, "Moved"
    except Exception as exc:
        err_msg = str(exc)
        if "soap-env:Sender" in err_msg or "400" in err_msg:
             return False, "Cannot move further (Limit reached)"
        return False, "Movement failed"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video.mjpg")
def video():
    return Response(mjpeg_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/ptz", methods=["POST"])
def ptz():
    action = (request.json or {}).get("action")
    ok, message = _ptz_move(action)
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": message}), status


@app.route("/health")
def health():
    frame_ok = grabber.get_frame() is not None
    return jsonify(
        {
            "rtsp_url": RTSP_URL,
        }
    )


from ultralytics import YOLO
from ultralytics.utils.plotting import colors

class Tracker:
    def __init__(self):
        self.enabled = True
        # Load the YOLOv8 model
        self.model = YOLO("yolov8n.pt")
        self.boxes = []

    def draw_hud(self, frame, x1, y1, x2, y2, label="Baby"):
        # Get color for class 0 (Person) from ultralytics
        # colors returns RGB, convert to BGR for OpenCV
        r, g, b = colors(0, True)
        color = (b, g, r)
        
        # Box
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        
        # Label
        font_thickness = 1
        font_scale = 0.6
        text_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)[0]
        
        c2 = x1 + text_size[0], y1 - text_size[1] - 3
        
        cv2.rectangle(frame, (x1, y1), c2, color, -1, cv2.LINE_AA)  # filled
        cv2.putText(frame, label, (x1, y1 - 2), 0, font_scale, (255, 255, 255), font_thickness, lineType=cv2.LINE_AA)

    def draw_overlay(self, frame):
        # Draw all current boxes
        for (x1, y1, x2, y2, label) in self.boxes:
            self.draw_hud(frame, x1, y1, x2, y2, label)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            if not self.enabled:
                time.sleep(1)
                continue

            frame = grabber.get_raw_frame()
            if frame is None:
                time.sleep(0.1)
                continue

            # Resize for inference (maintain aspect ratio)
            height, width = frame.shape[:2]
            scale = 320 / max(height, width)
            if scale < 1:
                inp = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
            else:
                inp = frame

            # Run inference
            results = self.model(inp, verbose=False)

            new_boxes = []
            for result in results:
                boxes = result.boxes
                for box in boxes:
                    # Bounding box coordinates (scaled back)
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    cls = int(box.cls[0].item())
                    label = self.model.names[cls]
                    
                    if scale < 1:
                        x1, y1, x2, y2 = x1/scale, y1/scale, x2/scale, y2/scale
                    
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    new_boxes.append((x1, y1, x2, y2, label))
            
            self.boxes = new_boxes
            time.sleep(0.5) # Run inference only twice per second

tracker = Tracker()
tracker.start()

@app.route("/tracking", methods=["POST"])
def set_tracking():
    data = request.json or {}
    enable = data.get("enable")
    if enable is not None:
        tracker.enabled = bool(enable)
    return jsonify({"enabled": tracker.enabled})

@app.route("/tracking", methods=["GET"])
def get_tracking():
    return jsonify({"enabled": tracker.enabled})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, threaded=True)
