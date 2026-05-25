import os
import json
import logging
import subprocess

from urllib.parse import quote, unquote, urlparse

class LiveStreamRouter:
    def __init__(self, html_template, cameras):
        self.html_template = os.path.abspath(html_template)
        self.cameras = cameras or {}

    def handle_get(self, handler):
        parsed = urlparse(handler.path)
        path = parsed.path

        if path in ("/live", "/live/"):
            self._send_live_page(handler)
            return True

        if path == "/live/cameras.json":
            self._send_cameras_json(handler)
            return True

        if path.startswith("/live/stream/"):
            cam_name = unquote(path[len("/live/stream/"):])
            self._stream_camera(handler, cam_name)
            return True

        return False

    def _send_cameras_json(self, handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        payload = {"cameras": list(self.cameras.keys())}
        handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _send_live_page(self, handler):
        try:
            with open(self.html_template, "r", encoding="utf-8") as f:
                html = f.read()
        except Exception as e:
            handler.send_response(500)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(
                f"Error loading live template: {e}".encode("utf-8")
            )
            return

        cards = []
        for cam_name in self.cameras.keys():
            stream_url = f"/live/stream/{quote(cam_name)}"
            cards.append(
                "<div class='cam-card'>"
                f"<div class='cam-title'>📷 {cam_name}</div>"
                f"<img data-src='{stream_url}' alt='{cam_name}' />"
                "</div>"
            )

        html = html.replace("{{CAMERA_CARDS}}", "".join(cards))

        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(html.encode("utf-8"))

    def _stream_camera(self, handler, camera_name):
        rtsp_url = self.cameras.get(camera_name)
        if not rtsp_url:
            handler.send_error(404, f"Camera '{camera_name}' not found")
            return

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", rtsp_url,
            "-an",
            "-vf", "fps=8,scale=960:-1",
            "-q:v", "7",
            "-f", "mpjpeg",
            "-boundary_tag", "frame",
            "pipe:1"
        ]

        process = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )

            handler.send_response(200)
            handler.send_header(
                "Content-Type",
                "multipart/x-mixed-replace; boundary=frame"
            )
            handler.send_header(
                "Cache-Control",
                "no-store, no-cache, must-revalidate"
            )
            handler.send_header("Pragma", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()

            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                handler.wfile.write(chunk)

        except (BrokenPipeError, ConnectionResetError):
            logging.info("Live client disconnected: %s", camera_name)
        except Exception as e:
            logging.error("Live stream error '%s': %s", camera_name, e)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()