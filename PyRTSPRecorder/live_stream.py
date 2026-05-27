import os
import json
import logging
import subprocess
import time

from urllib.parse import quote, unquote, urlparse

class LiveStreamRouter:
    def __init__(self, html_template, cameras):
        self.html_template = os.path.abspath(html_template)
        self.cameras = self._normalize_cameras(cameras)

        self._html_cache = None
        self._html_mtime = None

        logging.info(
            "LiveStreamRouter initialized. template=%s, cameras=%s",
            self.html_template,
            list(self.cameras.keys())
        )

    def _normalize_cameras(self, cameras):
        if not cameras:
            return {}

        if isinstance(cameras, dict):
            out = {}
            for name, url in cameras.items():
                if name and url:
                    out[str(name)] = str(url)
            return out

        if isinstance(cameras, list):
            out = {}
            for item in cameras:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                url = item.get("rtsp_url")
                if name and url:
                    out[str(name)] = str(url)
            return out

        return {}

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
        payload = {"cameras": list(self.cameras.keys())}
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate"
        )
        handler.end_headers()
        handler.wfile.write(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )

    def _read_template_cached(self):
        try:
            st = os.stat(self.html_template)
            mtime = st.st_mtime

            if self._html_cache is not None and self._html_mtime == mtime:
                return self._html_cache

            with open(self.html_template, "r", encoding="utf-8") as f:
                html = f.read()

            self._html_cache = html
            self._html_mtime = mtime
            return html
        except Exception as e:
            logging.error("Failed to read live template: %s", e)
            return None

    def _send_live_page(self, handler):
        html = self._read_template_cached()
        if html is None:
            handler.send_response(500)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(b"Error loading live template")
            return

        if "{{CAMERA_CARDS}}" in html:
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
        handler.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate"
        )
        handler.end_headers()
        handler.wfile.write(html.encode("utf-8"))

    def _build_ffmpeg_cmds(self, rtsp_url):
        # 1) Быстрый профиль
        cmd_fast = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32768",
            "-analyzeduration", "100000",
            "-i", rtsp_url,
            "-an",
            "-sn",
            "-dn",
            "-vf", "fps=8,scale=960:-1",
            "-c:v", "mjpeg",
            "-q:v", "7",
            "-f", "mpjpeg",
            "-flush_packets", "1",
            "pipe:1"
        ]

        # 2) Максимально совместимый fallback
        cmd_safe = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-an",
            "-vf", "fps=6,scale=960:-1",
            "-c:v", "mjpeg",
            "-q:v", "8",
            "-f", "mpjpeg",
            "pipe:1"
        ]

        return [cmd_fast, cmd_safe]

    def _start_ffmpeg_with_fallback(self, rtsp_url):
        last_err = ""
        for cmd in self._build_ffmpeg_cmds(rtsp_url):
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0
                )

                # Даем процессу чуть стартануть
                time.sleep(0.35)
                if proc.poll() is None:
                    return proc

                err = ""
                try:
                    err = proc.stderr.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass

                last_err = err.strip() or "ffmpeg exited immediately"
                logging.error("FFmpeg start failed. cmd=%s err=%s", cmd, last_err)

            except Exception as e:
                last_err = str(e)
                logging.error("FFmpeg launch exception: %s", e)

        return None

    def _stream_camera(self, handler, camera_name):
        rtsp_url = self.cameras.get(camera_name)
        if not rtsp_url:
            handler.send_error(404, f"Camera '{camera_name}' not found")
            return

        process = self._start_ffmpeg_with_fallback(rtsp_url)
        if process is None:
            handler.send_error(502, "Unable to start ffmpeg stream")
            return

        try:
            # Для mpjpeg у ffmpeg обычно boundary=ffmpeg по умолчанию
            handler.send_response(200)
            handler.send_header(
                "Content-Type",
                "multipart/x-mixed-replace; boundary=ffmpeg"
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
            logging.error("Live stream error for '%s': %s", camera_name, e)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()