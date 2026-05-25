import os
import json
import base64
import hashlib
import logging
import subprocess
import threading

from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote, unquote

from indexDb import VideoIndexDB

class VideoServer:
    def __init__(
        self,
        html_template,
        port,
        directory,
        username=None,
        password_hash=None,
        index_db_path=None,
        index_scan_interval=30
    ):
        self.html_template = os.path.abspath(html_template)
        self.port = int(port)
        self.directory = os.path.abspath(directory)
        self.cache_dir = os.path.abspath(".cache_recorder")
        self.username = username
        self.password_hash = password_hash

        os.makedirs(self.cache_dir, exist_ok=True)

        if not index_db_path:
            index_db_path = os.path.join(self.cache_dir, "video_index.db")

        self.index_db = VideoIndexDB(
            db_path=index_db_path,
            base_directory=self.directory,
            scan_interval=index_scan_interval
        )

        logging.info("Serving directory: %s", self.directory)
        logging.info("Cache directory: %s", self.cache_dir)
        logging.info("Index DB: %s", os.path.abspath(index_db_path))

        if not self.check_ffmpeg():
            logging.warning("FFmpeg not found!")

    def check_ffmpeg(self):
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            logging.info("FFmpeg found")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def start(self):
        self.index_db.start_background()

        handler = partial(
            self.CustomHandler,
            self.html_template,
            self.directory,
            self.cache_dir,
            self.username,
            self.password_hash,
            self.index_db
        )

        server = HTTPServer(("", self.port), handler)
        logging.info(
            "Starting server on port %s. http://localhost:%s",
            self.port,
            self.port
        )

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logging.info("Server stopped")
        finally:
            server.server_close()
            self.index_db.stop_background()
            self.index_db.close()

    class CustomHandler(BaseHTTPRequestHandler):
        conversion_status = {}
        conversion_lock = threading.Lock()

        def __init__(
            self,
            html_template,
            directory,
            cache_dir,
            username,
            password_hash,
            index_db,
            *args,
            **kwargs
        ):
            self.html_template = html_template
            self.base_directory = os.path.abspath(directory)
            self.cache_dir = cache_dir
            self.auth_username = username
            self.auth_password_hash = password_hash
            self.index_db = index_db
            super().__init__(*args, **kwargs)

        def check_authentication(self):
            if not self.auth_username or not self.auth_password_hash:
                return True

            auth_header = self.headers.get("Authorization")
            if auth_header is None:
                return False

            try:
                auth_type, auth_string = auth_header.split(" ", 1)
                if auth_type.lower() != "basic":
                    return False

                decoded = base64.b64decode(auth_string).decode("utf-8")
                username, password = decoded.split(":", 1)
                password_hash = hashlib.sha256(password.encode()).hexdigest()

                return (
                    username == self.auth_username
                    and password_hash == self.auth_password_hash
                )
            except Exception:
                return False

        def require_authentication(self):
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                'Basic realm="RTSP ARCHIVE"'
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>401 Unauthorized</h1>"
                b"<p>Access denied. Please provide valid credentials.</p>"
                b"</body></html>"
            )

        def do_HEAD(self):
            if not self.check_authentication():
                self.require_authentication()
                return

            if self.path.startswith("/videos/"):
                rel_path = unquote(self.path[8:].split("?")[0])
                self.check_video_status(rel_path)
            else:
                self.send_error(404, "Not Found")

        def do_GET(self):
            if not self.check_authentication():
                self.require_authentication()
                return

            if self.path == "/" or self.path.startswith("/?dir="):
                current_dir = ""
                if "?dir=" in self.path:
                    current_dir = unquote(self.path.split("?dir=", 1)[1])

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    self._render_main_page(current_dir).encode("utf-8")
                )
                return

            if self.path.startswith("/videos/"):
                rel_path = unquote(self.path[8:].split("?")[0])
                if "?download=1" in self.path:
                    self.send_download_file(rel_path)
                else:
                    self.send_video_file(rel_path)
                return

            if self.path.startswith("/status/"):
                rel_path = unquote(self.path[8:])
                self.send_conversion_status(rel_path)
                return

            self.send_error(404, "File Not Found")

        def _norm_rel(self, rel_path):
            rel_path = rel_path.replace("\\", "/")
            rel_path = rel_path.strip("/")
            return rel_path

        def _resolve_video_path(self, rel_path):
            rel_path = self._norm_rel(rel_path)

            meta = self.index_db.get_file(rel_path)
            if meta:
                full_path = meta.get("full_path")
                if full_path and os.path.exists(full_path):
                    return rel_path, full_path, meta

            # Fallback, если файл еще не попал в индекс
            full_path = os.path.abspath(
                os.path.join(self.base_directory, rel_path)
            )
            if not full_path.startswith(self.base_directory):
                return rel_path, None, None

            if not os.path.exists(full_path) or not os.path.isfile(full_path):
                return rel_path, None, None

            return rel_path, full_path, None

        def get_all_video_files(self, start_dir=""):
            try:
                return self.index_db.list_items(start_dir)
            except Exception as e:
                logging.info("Error reading index for dir %s: %s", start_dir, e)
                return []

        def _render_main_page(self, current_dir=""):
            current_dir = self._norm_rel(current_dir)
            items = self.get_all_video_files(current_dir)
            breadcrumbs = self._generate_breadcrumbs(current_dir)

            video_table_rows = ""

            if current_dir:
                parent_dir = os.path.dirname(current_dir).replace("\\", "/")
                parent_url = f"/?dir={quote(parent_dir)}" if parent_dir else "/"
                video_table_rows += (
                    "<tr class='directory-row' "
                    f"onclick=\"window.location.href='{parent_url}'\" "
                    "style='cursor: pointer;'>"
                    "<td><span class='folder-icon'>📁</span> "
                    "<strong>..</strong> (Parent Directory)</td>"
                    "<td colspan='3'></td>"
                    "</tr>"
                )

            for item in items:
                if item["type"] == "directory":
                    dir_url = f"/?dir={quote(item['path'])}"
                    video_table_rows += (
                        "<tr class='directory-row' "
                        f"onclick=\"window.location.href='{dir_url}'\" "
                        "style='cursor: pointer;'>"
                        "<td><span class='folder-icon'>📁</span> "
                        f"<strong>{item['name']}</strong></td>"
                        "<td colspan='3'><em>Folder</em></td>"
                        "</tr>"
                    )

            for item in items:
                if item["type"] == "file":
                    file_url = f"/videos/{quote(item['path'])}"
                    download_url = f"/videos/{quote(item['path'])}?download=1"
                    size_mb = item["size"] / (1024 * 1024)
                    codec = item.get("codec", "Unknown")
                    css_codec = codec.lower().replace(".", "")

                    codec_badge = (
                        f"<span class='codec-badge codec-{css_codec}'>"
                        f"{codec}</span>"
                    )

                    video_table_rows += (
                        f"<tr id='row-{quote(item['path'])}' "
                        "onclick=\"playVideo("
                        f"'{file_url}', '{item['name']}', "
                        f"'{item['path']}', this)\" "
                        "style='cursor: pointer;'>"
                        f"<td><span class='play-btn'>▶ {item['name']}</span></td>"
                        f"<td>{size_mb:.1f} MB</td>"
                        f"<td>{codec_badge}</td>"
                        "<td style='text-align: center;' "
                        "onclick='event.stopPropagation();'>"
                        f"<a href='{download_url}' class='download-link'>"
                        "&#128190;</a></td>"
                        "</tr>"
                    )

            if not items and not self.index_db.ready:
                video_table_rows += (
                    "<tr><td colspan='4'>"
                    "<em>Индексация архива... обновите страницу через "
                    "несколько секунд.</em>"
                    "</td></tr>"
                )

            try:
                with open(self.html_template, "r", encoding="utf-8") as f:
                    html_content = f.read()
            except Exception as e:
                logging.info("Error reading template: %s", e)
                return (
                    "<html><body><h1>Error loading template</h1>"
                    f"<p>{e}</p></body></html>"
                )

            html_content = html_content.replace(
                "{{BREADCRUMBS}}",
                breadcrumbs
            )
            html_content = html_content.replace(
                "{{VIDEO_TABLE_ROWS}}",
                video_table_rows
            )
            return html_content

        def _generate_breadcrumbs(self, current_dir):
            if not current_dir:
                return '<a href="/">🏠 Home</a>'

            parts = [p for p in current_dir.replace("\\", "/").split("/") if p]
            breadcrumbs = '<a href="/">🏠 Home</a>'
            path_acc = ""

            for part in parts:
                path_acc = f"{path_acc}/{part}" if path_acc else part
                breadcrumbs += (
                    f' / <a href="/?dir={quote(path_acc)}">{part}</a>'
                )
            return breadcrumbs

        def needs_conversion(self, file_path, rel_path=None):
            if rel_path:
                meta = self.index_db.get_file(rel_path)
                if meta is not None:
                    return bool(meta.get("needs_conversion", 0))

            try:
                result = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        file_path
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                codec = result.stdout.strip().lower()
                return codec in ["hevc", "h265"]
            except Exception:
                return False

        def get_cache_path(self, original_path):
            file_hash = hashlib.md5(original_path.encode()).hexdigest()[:8]
            file_stat = os.stat(original_path)
            time_hash = hashlib.md5(
                str(file_stat.st_mtime).encode()
            ).hexdigest()[:8]
            base_name = os.path.splitext(os.path.basename(original_path))[0]
            cache_name = f"{base_name}_{file_hash}_{time_hash}_h264.mp4"
            return os.path.join(self.cache_dir, cache_name)

        def check_video_status(self, rel_path):
            try:
                rel_path, original_path, _ = self._resolve_video_path(rel_path)
                if not original_path:
                    self.send_error(404, "File not found")
                    return

                if self.needs_conversion(original_path, rel_path):
                    cache_path = self.get_cache_path(original_path)

                    if os.path.exists(cache_path):
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header(
                            "Content-Length",
                            str(os.path.getsize(cache_path))
                        )
                        self.end_headers()
                        return

                    with self.conversion_lock:
                        status = self.conversion_status.get(rel_path, {})

                        if status.get("converting"):
                            self.send_response(202)
                            self.send_header(
                                "Content-Type",
                                "application/json"
                            )
                            self.end_headers()
                            return

                        self.conversion_status[rel_path] = {
                            "converting": True,
                            "progress": 0
                        }

                    threading.Thread(
                        target=self.convert_video,
                        args=(original_path, cache_path, rel_path),
                        daemon=True
                    ).start()

                    self.send_response(202)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    return

                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header(
                    "Content-Length",
                    str(os.path.getsize(original_path))
                )
                self.end_headers()

            except Exception as e:
                logging.info("check_video_status error: %s", e)
                self.send_error(500, "Internal Server Error")

        def send_video_file(self, rel_path):
            try:
                rel_path, original_path, _ = self._resolve_video_path(rel_path)
                if not original_path:
                    self.send_error(404, "File not found")
                    return

                if self.needs_conversion(original_path, rel_path):
                    cache_path = self.get_cache_path(original_path)

                    if os.path.exists(cache_path):
                        video_file_path = cache_path
                    else:
                        with self.conversion_lock:
                            status = self.conversion_status.get(rel_path, {})

                            if status.get("converting"):
                                self.send_json_response(
                                    202,
                                    {
                                        "status": "converting",
                                        "progress": status.get("progress", 0),
                                        "message": (
                                            "Video is being converted. "
                                            "Please wait..."
                                        )
                                    }
                                )
                                return

                            self.conversion_status[rel_path] = {
                                "converting": True,
                                "progress": 0
                            }

                        threading.Thread(
                            target=self.convert_video,
                            args=(original_path, cache_path, rel_path),
                            daemon=True
                        ).start()

                        self.send_json_response(
                            202,
                            {
                                "status": "converting",
                                "progress": 0,
                                "message": "Starting video conversion..."
                            }
                        )
                        return
                else:
                    video_file_path = original_path

                file_size = os.path.getsize(video_file_path)
                mime_type = "video/mp4"
                range_header = self.headers.get("Range")

                if range_header:
                    self.handle_range_request(
                        video_file_path,
                        file_size,
                        mime_type,
                        range_header
                    )
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(file_size))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

                    with open(video_file_path, "rb") as f:
                        self.copyfile(f, self.wfile)

            except Exception as e:
                logging.info("Error sending video: %s", e)

        def convert_video(self, input_path, output_path, rel_path):
            try:
                duration_cmd = [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    input_path
                ]
                duration_result = subprocess.run(
                    duration_cmd,
                    stdout=subprocess.PIPE,
                    text=True
                )

                try:
                    total_duration = float(duration_result.stdout.strip())
                except Exception:
                    total_duration = 0.0

                cmd = [
                    "ffmpeg", "-i", input_path,
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-movflags", "+faststart",
                    "-y",
                    output_path
                ]

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )

                for line in process.stderr:
                    if "time=" in line and total_duration > 0:
                        try:
                            time_str = line.split("time=")[1].split()[0]
                            h, m, s = time_str.split(":")
                            current = (
                                int(h) * 3600 + int(m) * 60 + float(s)
                            )
                            progress = min(
                                int((current / total_duration) * 100),
                                99
                            )

                            with self.conversion_lock:
                                if rel_path in self.conversion_status:
                                    self.conversion_status[rel_path][
                                        "progress"
                                    ] = progress
                        except Exception:
                            pass

                process.wait()

                if process.returncode == 0:
                    with self.conversion_lock:
                        self.conversion_status[rel_path] = {
                            "converting": False,
                            "progress": 100,
                            "completed": True
                        }
                else:
                    logging.info("Conversion failed: %s", rel_path)
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    with self.conversion_lock:
                        self.conversion_status.pop(rel_path, None)

            except Exception as e:
                logging.info("Error during conversion: %s", e)
                with self.conversion_lock:
                    self.conversion_status.pop(rel_path, None)

        def send_conversion_status(self, rel_path):
            rel_path = self._norm_rel(rel_path)
            with self.conversion_lock:
                status = self.conversion_status.get(rel_path, {})

            if status.get("completed"):
                self.send_json_response(
                    200, {"status": "completed", "progress": 100}
                )
            elif status.get("converting"):
                self.send_json_response(
                    200,
                    {
                        "status": "converting",
                        "progress": status.get("progress", 0)
                    }
                )
            else:
                self.send_json_response(
                    200, {"status": "ready", "progress": 0}
                )

        def send_json_response(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def handle_range_request(
            self,
            file_path,
            file_size,
            mime_type,
            range_header
        ):
            try:
                ranges = (
                    range_header.strip().lower()
                    .replace("bytes=", "")
                    .split("-")
                )
                start = int(ranges[0]) if ranges[0] else 0
                end = int(ranges[1]) if len(ranges) > 1 and ranges[1] else (
                    file_size - 1
                )

                if start >= file_size or start < 0:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return

                if end >= file_size:
                    end = file_size - 1

                content_length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime_type)
                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{end}/{file_size}"
                )
                self.send_header("Content-Length", str(content_length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length

                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)

            except Exception as e:
                logging.info("Error handling range request: %s", e)

        def send_download_file(self, rel_path):
            try:
                _, full_path, _ = self._resolve_video_path(rel_path)
                if not full_path:
                    self.send_error(404, "File not found")
                    return

                file_size = os.path.getsize(full_path)

                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/octet-stream"
                )
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{os.path.basename(full_path)}"'
                )
                self.send_header("Content-Length", str(file_size))
                self.end_headers()

                with open(full_path, "rb") as f:
                    self.copyfile(f, self.wfile)

            except Exception as e:
                logging.info("Error sending download: %s", e)

        def copyfile(self, source, outputfile):
            while True:
                buf = source.read(8192)
                if not buf:
                    break
                outputfile.write(buf)

if __name__ == "__main__":
    print("It's server class")
    print(
        "Create object VideoServer("
        "html_template=html_template_path, "
        "port=9596, "
        "directory=video_directory, "
        "username=user, "
        "password_hash=password_hash)"
    )
    print("run by .start() function")