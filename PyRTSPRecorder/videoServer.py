import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from functools import partial
from urllib.parse import unquote, quote
import mimetypes
import hashlib

class VideoServer:
    def __init__(self, html_template, port, directory, cache_dir):
        self.html_template = os.path.abspath(html_template)
        self.port = port
        self.directory = os.path.abspath(directory)
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        print(f"Serving directory: {self.directory}")
        print(f"Cache directory: {self.cache_dir}")

        # Проверяем наличие FFmpeg
        if not self.check_ffmpeg():
            print("\n⚠️  WARNING: FFmpeg not found!")
            print("Install FFmpeg for H.265 support:")
            print("  Ubuntu/Debian: sudo apt install ffmpeg")
            print("  MacOS: brew install ffmpeg")
            print("  Windows: download from https://ffmpeg.org/download.html\n")

    def check_ffmpeg(self):
        """Проверяет наличие FFmpeg"""
        try:
            subprocess.run(['ffmpeg', '-version'], 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE, 
                          check=True)
            print("✓ FFmpeg found")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def start(self):
        handler = partial(self.CustomHandler, self.html_template, self.directory, self.cache_dir)
        server = HTTPServer(('', self.port), handler)
        print(f"Starting server on port {self.port}. Visit http://localhost:{self.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")
            server.server_close()

    class CustomHandler(BaseHTTPRequestHandler):
        conversion_status = {}  # Глобальный статус конвертации
        conversion_lock = threading.Lock()

        def __init__(self, html_template, directory, cache_dir, *args, **kwargs):
            self.html_template = html_template
            self.base_directory = directory
            self.cache_dir = cache_dir
            super().__init__(*args, **kwargs)

        def do_HEAD(self):
            """Обрабатывает HEAD-запросы (для проверки статуса)"""
            if self.path.startswith('/videos/'):
                file_path = unquote(self.path[8:].split('?')[0])
                self.check_video_status(file_path)
            else:
                self.send_error(404, "Not Found")

        def do_GET(self):
            """Обрабатывает GET-запросы"""
            if self.path == '/' or self.path.startswith('/?dir='):
                # Получаем текущую директорию из параметра
                current_dir = ''
                if '?dir=' in self.path:
                    current_dir = unquote(self.path.split('?dir=')[1])

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(self._render_main_page(current_dir).encode('utf-8'))
            elif self.path.startswith('/videos/'):
                file_path = unquote(self.path[8:].split('?')[0])
                if '?download=1' in self.path:
                    self.send_download_file(file_path)
                else:
                    self.send_video_file(file_path)
            elif self.path.startswith('/status/'):
                file_path = unquote(self.path[8:])
                self.send_conversion_status(file_path)
            else:
                self.send_error(404, "File Not Found")

        def get_all_video_files(self, start_dir=''):
            """Рекурсивно получает все видеофайлы из директории"""
            full_path = os.path.join(self.base_directory, start_dir) if start_dir else self.base_directory

            items = []
            video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg'}

            try:
                entries = sorted(os.listdir(full_path))

                for entry in entries:
                    if entry.startswith('.'):
                        continue

                    entry_full_path = os.path.join(full_path, entry)
                    entry_rel_path = os.path.join(start_dir, entry) if start_dir else entry

                    if os.path.isdir(entry_full_path):
                        # Это директория
                        items.append({
                            'type': 'directory',
                            'name': entry,
                            'path': entry_rel_path,
                            'full_path': entry_full_path
                        })
                    elif os.path.isfile(entry_full_path):
                        # Проверяем расширение файла
                        ext = os.path.splitext(entry)[1].lower()
                        if ext in video_extensions:
                            items.append({
                                'type': 'file',
                                'name': entry,
                                'path': entry_rel_path,
                                'full_path': entry_full_path,
                                'size': os.path.getsize(entry_full_path)
                            })

            except Exception as e:
                print(f"Error listing directory {full_path}: {e}")

            return items

        def _render_main_page(self, current_dir=''):
            """Создает HTML главной страницы"""
            items = self.get_all_video_files(current_dir)

            # Формируем breadcrumbs (хлебные крошки)
            breadcrumbs = self._generate_breadcrumbs(current_dir)

            video_table_rows = ""

            # Добавляем кнопку "Назад" если не в корневой директории
            if current_dir:
                parent_dir = os.path.dirname(current_dir)
                parent_url = f"/?dir={quote(parent_dir)}" if parent_dir else "/"
                video_table_rows += (
                    f"<tr class='directory-row' onclick=\"window.location.href='{parent_url}'\" style='cursor: pointer;'>"
                    f"<td><span class='folder-icon'>📁</span> <strong>..</strong> (Parent Directory)</td>"
                    f"<td colspan='3'></td>"
                    f"</tr>"
                )

            # Сначала показываем директории
            for item in items:
                if item['type'] == 'directory':
                    dir_url = f"/?dir={quote(item['path'])}"
                    video_table_rows += (
                        f"<tr class='directory-row' onclick=\"window.location.href='{dir_url}'\" style='cursor: pointer;'>"
                        f"<td><span class='folder-icon'>📁</span> <strong>{item['name']}</strong></td>"
                        f"<td colspan='3'><em>Folder</em></td>"
                        f"</tr>"
                    )

            # Затем показываем файлы
            for item in items:
                if item['type'] == 'file':
                    file_url = f"/videos/{quote(item['path'])}"
                    download_url = f"/videos/{quote(item['path'])}?download=1"

                    size_mb = item['size'] / (1024 * 1024)

                    # Определяем кодек
                    codec = self.get_video_codec(item['full_path'])
                    codec_badge = f'<span class="codec-badge codec-{codec.lower().replace(".", "")}">{codec}</span>'

                    video_table_rows += (
                        f"<tr id='row-{quote(item['path'])}' onclick=\"playVideo('{file_url}', '{item['name']}', '{item['path']}', this)\" style='cursor: pointer;'>"
                        f"<td><span class='play-btn'>▶ {item['name']}</span></td>"
                        f"<td>{size_mb:.1f} MB</td>"
                        f"<td>{codec_badge}</td>"
                        f"<td style='text-align: center;' onclick='event.stopPropagation();'><a href='{download_url}' class='download-link'>&#128190;</a></td>"
                        f"</tr>"
                    )

            try:
                with open(self.html_template, 'r', encoding='utf-8') as f:
                    html_content = f.read()
            except Exception as e:
                print(f"Error reading template: {e}")
                return f"<html><body><h1>Error loading template</h1><p>{e}</p></body></html>"

            # Заменяем плейсхолдеры
            html_content = html_content.replace("{{BREADCRUMBS}}", breadcrumbs)
            html_content = html_content.replace("{{VIDEO_TABLE_ROWS}}", video_table_rows)

            return html_content

        def _generate_breadcrumbs(self, current_dir):
            """Генерирует хлебные крошки навигации"""
            if not current_dir:
                return '<a href="/">🏠 Home</a>'

            parts = current_dir.split(os.sep)
            breadcrumbs = '<a href="/">🏠 Home</a>'

            path_accumulator = ''
            for part in parts:
                path_accumulator = os.path.join(path_accumulator, part) if path_accumulator else part
                breadcrumbs += f' / <a href="/?dir={quote(path_accumulator)}">{part}</a>'

            return breadcrumbs

        def get_video_codec(self, file_path):
            """Определяет видео кодек"""
            try:
                result = subprocess.run([
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=codec_name',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    file_path
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)

                codec = result.stdout.strip().lower()
                codec_map = {
                    'hevc': 'H.265',
                    'h265': 'H.265',
                    'h264': 'H.264',
                    'vp9': 'VP9',
                    'vp8': 'VP8'
                }
                return codec_map.get(codec, codec.upper() if codec else 'Unknown')
            except:
                return 'Unknown'

        def needs_conversion(self, file_path):
            """Проверяет нужна ли конвертация"""
            try:
                result = subprocess.run([
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=codec_name',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    file_path
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)

                codec = result.stdout.strip().lower()
                return codec in ['hevc', 'h265']
            except:
                return False

        def get_cache_path(self, original_path):
            """Получает путь к кэшированному файлу"""
            file_hash = hashlib.md5(original_path.encode()).hexdigest()[:8]
            file_stat = os.stat(original_path)
            time_hash = hashlib.md5(str(file_stat.st_mtime).encode()).hexdigest()[:8]
            base_name = os.path.splitext(os.path.basename(original_path))[0]
            return os.path.join(self.cache_dir, f"{base_name}_{file_hash}_{time_hash}_h264.mp4")

        def check_video_status(self, file_path):
            """Проверяет статус видео для HEAD запроса"""
            try:
                original_path = os.path.join(self.base_directory, file_path)

                if not os.path.exists(original_path):
                    self.send_error(404, "File not found")
                    return

                # Проверяем нужна ли конвертация
                if self.needs_conversion(original_path):
                    cache_path = self.get_cache_path(original_path)

                    # Проверяем кэш
                    if os.path.exists(cache_path):
                        # Файл уже сконвертирован
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Content-Length", str(os.path.getsize(cache_path)))
                        self.end_headers()
                    else:
                        # Проверяем статус конвертации
                        with self.conversion_lock:
                            status = self.conversion_status.get(file_path, {})

                            if status.get('converting'):
                                # Конвертация уже идёт
                                self.send_response(202)  # Accepted
                                self.send_header("Content-Type", "application/json")
                                self.end_headers()
                            else:
                                # Нужно начать конвертацию
                                self.conversion_status[file_path] = {
                                    'converting': True,
                                    'progress': 0
                                }

                                # Запускаем конвертацию в фоне
                                threading.Thread(
                                    target=self.convert_video,
                                    args=(original_path, cache_path, file_path),
                                    daemon=True
                                ).start()

                                self.send_response(202)  # Accepted
                                self.send_header("Content-Type", "application/json")
                                self.end_headers()
                else:
                    # Конвертация не нужна
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(os.path.getsize(original_path)))
                    self.end_headers()

            except Exception as e:
                print(f"Error checking video status: {e}")
                self.send_error(500, "Internal Server Error")

        def send_video_file(self, file_path):
            """Отправляет видеофайл (с конвертацией если нужно)"""
            try:
                original_path = os.path.join(self.base_directory, file_path)
                print(f"Attempting to serve: {original_path}")

                if not os.path.exists(original_path):
                    print(f"File not found: {original_path}")
                    self.send_error(404, "File not found")
                    return

                # Проверяем нужна ли конвертация
                if self.needs_conversion(original_path):
                    print(f"H.265 video detected: {file_path}")
                    cache_path = self.get_cache_path(original_path)

                    # Проверяем кэш
                    if os.path.exists(cache_path):
                        print(f"Serving cached H.264 version")
                        video_file_path = cache_path
                    else:
                        # Проверяем статус конвертации
                        with self.conversion_lock:
                            status = self.conversion_status.get(file_path, {})

                            if status.get('converting'):
                                # Конвертация уже идёт
                                print(f"Conversion in progress: {status.get('progress', 0)}%")
                                self.send_json_response(202, {
                                    'status': 'converting',
                                    'progress': status.get('progress', 0),
                                    'message': 'Video is being converted. Please wait...'
                                })
                                return
                            else:
                                # Начинаем конвертацию
                                print(f"Starting conversion to H.264")
                                self.conversion_status[file_path] = {
                                    'converting': True,
                                    'progress': 0
                                }

                        # Запускаем конвертацию в фоне
                        threading.Thread(
                            target=self.convert_video,
                            args=(original_path, cache_path, file_path),
                            daemon=True
                        ).start()

                        self.send_json_response(202, {
                            'status': 'converting',
                            'progress': 0,
                            'message': 'Starting video conversion...'
                        })
                        return
                else:
                    video_file_path = original_path

                # Отправляем файл
                file_size = os.path.getsize(video_file_path)
                mime_type = 'video/mp4'
                range_header = self.headers.get('Range')

                if range_header:
                    self.handle_range_request(video_file_path, file_size, mime_type, range_header)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(file_size))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

                    with open(video_file_path, 'rb') as f:
                        self.copyfile(f, self.wfile)

            except Exception as e:
                print(f"Error sending video: {e}")
                import traceback
                traceback.print_exc()

        def convert_video(self, input_path, output_path, file_path):
            """Конвертирует видео из H.265 в H.264"""
            try:
                print(f"Converting: {file_path}")

                # Получаем длительность для отслеживания прогресса
                duration_cmd = [
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    input_path
                ]
                duration_result = subprocess.run(duration_cmd, stdout=subprocess.PIPE, text=True)
                try:
                    total_duration = float(duration_result.stdout.strip())
                except:
                    total_duration = 0

                # FFmpeg команда
                cmd = [
                    'ffmpeg', '-i', input_path,
                    '-c:v', 'libx264',          # H.264 кодек
                    '-preset', 'fast',          # Быстрая конвертация
                    '-crf', '23',               # Качество
                    '-c:a', 'aac',              # AAC аудио
                    '-b:a', '128k',             # Битрейт аудио
                    '-movflags', '+faststart',  # Оптимизация для веб
                    '-y',                       # Перезаписать
                    output_path
                ]

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )

                # Отслеживаем прогресс
                for line in process.stderr:
                    if 'time=' in line and total_duration > 0:
                        try:
                            time_str = line.split('time=')[1].split()[0]
                            h, m, s = time_str.split(':')
                            current_time = int(h) * 3600 + int(m) * 60 + float(s)
                            progress = min(int((current_time / total_duration) * 100), 99)

                            with self.conversion_lock:
                                if file_path in self.conversion_status:
                                    self.conversion_status[file_path]['progress'] = progress

                            if progress % 10 == 0:  # Логируем каждые 10%
                                print(f"Conversion progress: {progress}%")
                        except:
                            pass

                process.wait()

                if process.returncode == 0:
                    print(f"✓ Conversion completed: {file_path}")
                    with self.conversion_lock:
                        self.conversion_status[file_path] = {
                            'converting': False,
                            'progress': 100,
                            'completed': True
                        }
                else:
                    print(f"✗ Conversion failed: {file_path}")
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    with self.conversion_lock:
                        if file_path in self.conversion_status:
                            del self.conversion_status[file_path]

            except Exception as e:
                print(f"Error during conversion: {e}")
                import traceback
                traceback.print_exc()
                with self.conversion_lock:
                    if file_path in self.conversion_status:
                        del self.conversion_status[file_path]

        def send_conversion_status(self, file_path):
            """Отправляет статус конвертации"""
            with self.conversion_lock:
                status = self.conversion_status.get(file_path, {})

            if status.get('completed'):
                self.send_json_response(200, {
                    'status': 'completed',
                    'progress': 100
                })
            elif status.get('converting'):
                self.send_json_response(200, {
                    'status': 'converting',
                    'progress': status.get('progress', 0)
                })
            else:
                self.send_json_response(200, {
                    'status': 'ready',
                    'progress': 0
                })

        def send_json_response(self, code, data):
            """Отправляет JSON ответ"""
            import json
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def handle_range_request(self, file_path, file_size, mime_type, range_header):
            """Обрабатывает Range запросы"""
            try:
                ranges = range_header.strip().lower().replace('bytes=', '').split('-')
                start = int(ranges[0]) if ranges[0] else 0
                end = int(ranges[1]) if ranges[1] else file_size - 1

                if start >= file_size or start < 0:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return

                if end >= file_size:
                    end = file_size - 1

                content_length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(content_length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                with open(file_path, 'rb') as f:
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
                print(f"Error handling range request: {e}")

        def send_download_file(self, file_path):
            """Отправляет оригинальный файл для скачивания"""
            try:
                full_path = os.path.join(self.base_directory, file_path)

                if not os.path.exists(full_path) or not os.path.isfile(full_path):
                    self.send_error(404, "File not found")
                    return

                file_size = os.path.getsize(full_path)

                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(full_path)}"')
                self.send_header("Content-Length", str(file_size))
                self.end_headers()

                with open(full_path, 'rb') as f:
                    self.copyfile(f, self.wfile)

            except Exception as e:
                print(f"Error sending download: {e}")

        def copyfile(self, source, outputfile):
            """Копирует файл по частям"""
            while True:
                buf = source.read(8192)
                if not buf:
                    break
                outputfile.write(buf)

        def log_message(self, format, *args):
            """Логирование запросов"""
            print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    html_template_path = "./index.html"
    # if not os.path.exists(html_template_path):
    #     with open(html_template_path, "w", encoding="utf-8") as f:
    #         f.write(html_template_content)

    video_dir = "./recordings"
    cache_dir = "./.cache"
    os.makedirs(video_dir, exist_ok=True)

    server = VideoServer(html_template=html_template_path, port=9596, directory=video_dir, cache_dir=cache_dir)
    server.start()