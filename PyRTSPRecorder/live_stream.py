import os
import json
import time
import logging
import threading
import subprocess

from urllib.parse import quote, unquote, urlparse

class CameraWarmStream:
    SOI = b"\xff\xd8"
    EOI = b"\xff\xd9"

    def __init__(self, camera_name, rtsp_url):
        self.camera_name = camera_name
        self.rtsp_url = rtsp_url

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        self._latest_frame = None
        self._frame_id = 0

        self._stop_event = threading.Event()
        self._thread = None
        self._process = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logging.info("Pre-warm started for camera: %s", self.camera_name)

    def stop(self):
        self._stop_event.set()
        self._terminate_process()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logging.info("Pre-warm stopped for camera: %s", self.camera_name)

    def get_latest_frame(self):
        with self._lock:
            return self._latest_frame, self._frame_id

    def wait_next_frame(self, last_frame_id, timeout=5.0):
        end = time.time() + timeout
        with self._cond:
            while not self._stop_event.is_set():
                if self._latest_frame is not None and self._frame_id != last_frame_id:
                    return self._latest_frame, self._frame_id

                remain = end - time.time()
                if remain <= 0:
                    return None, last_frame_id
                self._cond.wait(timeout=remain)

        return None, last_frame_id

    def _publish_frame(self, frame_bytes):
        with self._cond:
            self._latest_frame = frame_bytes
            self._frame_id += 1
            self._cond.notify_all()

    def _terminate_process(self):
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None

    def _build_ffmpeg_cmd_profiles(self):
        # Быстрый low-latency профиль
        fast_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-i", self.rtsp_url,
            "-an",
            "-sn",
            "-dn",
            "-vf", "fps=8,scale=960:-1",
            "-c:v", "mjpeg",
            "-q:v", "7",
            "-f", "mjpeg",
            "pipe:1"
        ]

        # Безопасный профиль для совместимости
        safe_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-an",
            "-vf", "fps=6,scale=960:-1",
            "-c:v", "mjpeg",
            "-q:v", "8",
            "-f", "mjpeg",
            "pipe:1"
        ]

        return [fast_cmd, safe_cmd]

    def _run(self):
        backoff = 1.0

        while not self._stop_event.is_set():
            started = False
            last_err = ""

            for cmd in self._build_ffmpeg_cmd_profiles():
                if self._stop_event.is_set():
                    break

                try:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        bufsize=0
                    )

                    # Проверяем быстрый "падёж" процесса
                    time.sleep(0.4)
                    if self._process.poll() is not None:
                        err = ""
                        try:
                            err = self._process.stderr.read().decode(
                                "utf-8", errors="ignore"
                            )
                        except Exception:
                            pass

                        last_err = err.strip() or "ffmpeg exited immediately"
                        logging.warning(
                            "Camera %s: ffmpeg quick-exit. err=%s",
                            self.camera_name,
                            last_err[-300:]
                        )
                        self._terminate_process()
                        continue

                    # Успешный старт
                    started = True
                    backoff = 1.0
                    buffer = b""

                    while not self._stop_event.is_set():
                        chunk = self._process.stdout.read(8192)
                        if not chunk:
                            break

                        buffer += chunk

                        # Парсим JPEG кадры по SOI/EOI
                        while True:
                            s = buffer.find(self.SOI)
                            if s < 0:
                                if len(buffer) > 2 * 1024 * 1024:
                                    buffer = buffer[-1024:]
                                break

                            e = buffer.find(self.EOI, s + 2)
                            if e < 0:
                                if s > 0:
                                    buffer = buffer[s:]
                                break

                            frame = buffer[s:e + 2]
                            buffer = buffer[e + 2:]

                            if len(frame) > 1024:
                                self._publish_frame(frame)

                    if self._stop_event.is_set():
                        break

                    err_tail = ""
                    try:
                        err_tail = self._process.stderr.read().decode(
                            "utf-8", errors="ignore"
                        ).strip()
                    except Exception:
                        pass

                    logging.warning(
                        "Camera stream ended: %s, restarting... %s",
                        self.camera_name,
                        err_tail[-200:] if err_tail else ""
                    )
                    break

                except Exception as e:
                    last_err = str(e)
                    logging.error(
                        "Pre-warm error for %s: %s",
                        self.camera_name,
                        e
                    )
                    self._terminate_process()

            self._terminate_process()

            if self._stop_event.is_set():
                break

            if not started and last_err:
                logging.error(
                    "Camera %s: all ffmpeg profiles failed. Last error: %s",
                    self.camera_name,
                    last_err[-300:]
                )

            time.sleep(backoff)
            backoff = min(backoff * 2.0, 10.0)

class LiveStreamRouter:
    def __init__(self, html_template, cameras):
        self.html_template = os.path.abspath(html_template)
        self.cameras = self._normalize_cameras(cameras)

        self._html_cache = None
        self._html_mtime = None

        self.workers = {}
        for cam_name, rtsp_url in self.cameras.items():
            worker = CameraWarmStream(cam_name, rtsp_url)
            self.workers[cam_name] = worker
            worker.start()

        logging.info(
            "LiveStreamRouter initialized. cameras=%s",
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

    def stop(self):
        for worker in self.workers.values():
            worker.stop()

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
        handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _read_template_cached(self):
        try:
            st = os.stat(self.html_template)
            if self._html_cache is not None and self._html_mtime == st.st_mtime:
                return self._html_cache

            with open(self.html_template, "r", encoding="utf-8") as f:
                html = f.read()

            self._html_cache = html
            self._html_mtime = st.st_mtime
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

        # Совместимость с шаблонами, где есть {{CAMERA_CARDS}}
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
        handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(html.encode("utf-8"))

    def _stream_camera(self, handler, camera_name):
        worker = self.workers.get(camera_name)
        if not worker:
            handler.send_error(404, f"Camera '{camera_name}' not found")
            return

        try:
            handler.send_response(200)
            handler.send_header(
                "Content-Type",
                "multipart/x-mixed-replace; boundary=frame"
            )
            handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            handler.send_header("Pragma", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()

            frame, last_id = worker.get_latest_frame()
            if frame:
                self._write_mjpeg_part(handler, frame)

            while True:
                frame, last_id = worker.wait_next_frame(last_id, timeout=10.0)
                if frame is None:
                    continue
                self._write_mjpeg_part(handler, frame)

        except (BrokenPipeError, ConnectionResetError):
            logging.info("Live client disconnected: %s", camera_name)
        except Exception as e:
            logging.error("Live stream error '%s': %s", camera_name, e)

    @staticmethod
    def _write_mjpeg_part(handler, frame):
        handler.wfile.write(b"--frame\r\n")
        handler.wfile.write(b"Content-Type: image/jpeg\r\n")
        handler.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
        handler.wfile.write(frame)
        handler.wfile.write(b"\r\n")