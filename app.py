import os
import threading
import time
from urllib.parse import urlparse

import cv2
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
        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

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
            
            errors = 0
            ok, buf = cv2.imencode(".jpg", frame)
            if ok:
                with self.lock:
                    self.frame = buf.tobytes()
            time.sleep(0.025)

    def get_frame(self):
        with self.lock:
            return self.frame

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
            "frame": frame_ok,
            "camera_host": CAM_HOST,
            "onvif_port": CAM_ONVIF_PORT,
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, threaded=True)
