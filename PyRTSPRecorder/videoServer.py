import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from functools import partial
from urllib.parse import unquote
import mimetypes
import hashlib

class VideoServer:
    def __init__(self, html_template, port, directory):
        self.html_template = os.path.abspath(html_template)
        self.port = port
        self.directory = os.path.abspath(directory)
        self.cache_dir = os.path.join(directory, '.cache')
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
                file_name = unquote(self.path[8:].split('?')[0])
                self.check_video_status(file_name)
            else:
                self.send_error(404, "Not Found")

        def do_GET(self):
            """Обрабатывает GET-запросы"""
            if self.path == '/':
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(self._render_main_page().encode('utf-8'))
            elif self.path.startswith('/videos/'):
                file_name = unquote(self.path[8:].split('?')[0])
                if '?download=1' in self.path:
                    self.send_download_file(file_name)
                else:
                    self.send_video_file(file_name)
            elif self.path.startswith('/status/'):
                file_name = unquote(self.path[8:])
                self.send_conversion_status(file_name)
            else:
                self.send_error(404, "File Not Found")

        def check_video_status(self, file_name):
            """Проверяет статус видео для HEAD запроса"""
            try:
                original_path = os.path.join(self.base_directory, file_name)

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
                            status = self.conversion_status.get(file_name, {})

                            if status.get('converting'):
                                # Конвертация уже идёт
                                self.send_response(202)  # Accepted
                                self.send_header("Content-Type", "application/json")
                                self.end_headers()
                            else:
                                # Нужно начать конвертацию
                                self.conversion_status[file_name] = {
                                    'converting': True,
                                    'progress': 0
                                }

                                # Запускаем конвертацию в фоне
                                threading.Thread(
                                    target=self.convert_video,
                                    args=(original_path, cache_path, file_name),
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

        def _render_main_page(self):
            """Создает HTML главной страницы"""
            try:
                files = sorted([
                    f for f in os.listdir(self.base_directory)
                    if os.path.isfile(os.path.join(self.base_directory, f))
                    and not f.startswith('.')
                ])
            except Exception as e:
                print(f"Error listing directory: {e}")
                files = []

            video_table_rows = ""
            for file in files:
                file_url = f"/videos/{file}"
                download_url = f"/videos/{file}?download=1"
                file_path = os.path.join(self.base_directory, file)

                # Размер файла
                file_size = os.path.getsize(file_path)
                size_mb = file_size / (1024 * 1024)

                # Определяем кодек
                codec = self.get_video_codec(file_path)
                codec_badge = f'<span class="codec-badge codec-{codec.lower().replace(".", "")}">{codec}</span>'

                video_table_rows += (
                    f"<tr>"
                    f"<td><a href='javascript:void(0)' onclick=\"playVideo('{file_url}', '{file}')\">{file}</a></td>"
                    f"<td>{size_mb:.1f} MB</td>"
                    f"<td>{codec_badge}</td>"
                    f"<td style='text-align: center;'><a href='{download_url}' class='download-link'>&#128190;</a></td>"
                    f"</tr>"
                )

            try:
                with open(self.html_template, 'r', encoding='utf-8') as f:
                    html_content = f.read()
            except Exception as e:
                print(f"Error reading template: {e}")
                return f"<html><body><h1>Error loading template</h1><p>{e}</p></body></html>"

            return html_content.replace("{{VIDEO_TABLE_ROWS}}", video_table_rows)

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

        def send_video_file(self, file_name):
            """Отправляет видеофайл (с конвертацией если нужно)"""
            try:
                original_path = os.path.join(self.base_directory, file_name)
                print(f"Attempting to serve: {original_path}")

                if not os.path.exists(original_path):
                    print(f"File not found: {original_path}")
                    self.send_error(404, "File not found")
                    return

                # Проверяем нужна ли конвертация
                if self.needs_conversion(original_path):
                    print(f"H.265 video detected: {file_name}")
                    cache_path = self.get_cache_path(original_path)

                    # Проверяем кэш
                    if os.path.exists(cache_path):
                        print(f"Serving cached H.264 version")
                        file_path = cache_path
                    else:
                        # Проверяем статус конвертации
                        with self.conversion_lock:
                            status = self.conversion_status.get(file_name, {})

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
                                self.conversion_status[file_name] = {
                                    'converting': True,
                                    'progress': 0
                                }

                        # Запускаем конвертацию в фоне
                        threading.Thread(
                            target=self.convert_video,
                            args=(original_path, cache_path, file_name),
                            daemon=True
                        ).start()

                        self.send_json_response(202, {
                            'status': 'converting',
                            'progress': 0,
                            'message': 'Starting video conversion...'
                        })
                        return
                else:
                    file_path = original_path

                # Отправляем файл
                file_size = os.path.getsize(file_path)
                mime_type = 'video/mp4'
                range_header = self.headers.get('Range')

                if range_header:
                    self.handle_range_request(file_path, file_size, mime_type, range_header)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", str(file_size))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

                    with open(file_path, 'rb') as f:
                        self.copyfile(f, self.wfile)

            except Exception as e:
                print(f"Error sending video: {e}")
                import traceback
                traceback.print_exc()

        def convert_video(self, input_path, output_path, file_name):
            """Конвертирует видео из H.265 в H.264"""
            try:
                print(f"Converting: {file_name}")

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
                                if file_name in self.conversion_status:
                                    self.conversion_status[file_name]['progress'] = progress

                            if progress % 10 == 0:  # Логируем каждые 10%
                                print(f"Conversion progress: {progress}%")
                        except:
                            pass

                process.wait()

                if process.returncode == 0:
                    print(f"✓ Conversion completed: {file_name}")
                    with self.conversion_lock:
                        self.conversion_status[file_name] = {
                            'converting': False,
                            'progress': 100,
                            'completed': True
                        }
                else:
                    print(f"✗ Conversion failed: {file_name}")
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    with self.conversion_lock:
                        if file_name in self.conversion_status:
                            del self.conversion_status[file_name]

            except Exception as e:
                print(f"Error during conversion: {e}")
                import traceback
                traceback.print_exc()
                with self.conversion_lock:
                    if file_name in self.conversion_status:
                        del self.conversion_status[file_name]

        def send_conversion_status(self, file_name):
            """Отправляет статус конвертации"""
            with self.conversion_lock:
                status = self.conversion_status.get(file_name, {})

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

        def send_download_file(self, file_name):
            """Отправляет оригинальный файл для скачивания"""
            try:
                file_path = os.path.join(self.base_directory, file_name)

                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                    self.send_error(404, "File not found")
                    return

                file_size = os.path.getsize(file_path)

                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(file_path)}"')
                self.send_header("Content-Length", str(file_size))
                self.end_headers()

                with open(file_path, 'rb') as f:
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

# HTML шаблон с поддержкой конвертации
html_template_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Server (H.265 Support)</title>
    <style>
        body { 
            font-family: Arial, sans-serif; 
            margin: 30px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            margin-bottom: 10px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 20px;
            font-size: 14px;
        }
        #videoContainer {
            background-color: #000;
            margin-bottom: 20px;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }
        #videoPlayer {
            width: 100%;
            max-height: 600px;
            display: block;
        }
        #currentFile {
            padding: 10px;
            background-color: #333;
            color: white;
            font-size: 14px;
        }
        table { 
            width: 100%; 
            border-collapse: collapse; 
            margin-top: 20px;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border-radius: 8px;
            overflow: hidden;
        }
        th, td { 
            border: 1px solid #ddd; 
            padding: 12px; 
            text-align: left; 
        }
        th { 
            background-color: #4CAF50;
            color: white;
            font-weight: bold;
        }
        tbody tr:hover {
            background-color: #f5f5f5;
            cursor: pointer;
        }
        a { 
            text-decoration: none; 
            color: #007BFF; 
            cursor: pointer;
        }
        a:hover { 
            text-decoration: underline; 
        }
        .download-link {
            font-size: 20px;
            text-decoration: none;
            transition: transform 0.2s;
        }
        .download-link:hover {
            text-decoration: none;
            transform: scale(1.3);
            display: inline-block;
        }
        .status {
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 4px;
            display: none;
            font-weight: 500;
        }
        .status.error {
            background-color: #ffebee;
            color: #c62828;
            border-left: 4px solid #c62828;
            display: block;
        }
        .status.info {
            background-color: #e3f2fd;
            color: #1565c0;
            border-left: 4px solid #1565c0;
            display: block;
        }
        .status.warning {
            background-color: #fff3e0;
            color: #ef6c00;
            border-left: 4px solid #ef6c00;
            display: block;
        }
        .status.success {
            background-color: #e8f5e9;
            color: #2e7d32;
            border-left: 4px solid #2e7d32;
            display: block;
        }
        .progress-bar {
            width: 100%;
            height: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 10px;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #4CAF50, #81C784);
            width: 0%;
            transition: width 0.3s ease;
        }
        .codec-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
            text-transform: uppercase;
        }
        .codec-h265 {
            background-color: #ff9800;
            color: white;
        }
        .codec-h264 {
            background-color: #4CAF50;
            color: white;
        }
        .codec-unknown {
            background-color: #9e9e9e;
            color: white;
        }
    </style>
</head>
<body>
    <h1>🎬 Video Viewer</h1>
    <div class="subtitle">Automatic H.265 → H.264 conversion for browser compatibility</div>

    <div id="status" class="status"></div>

    <div id="videoContainer">
        <video id="videoPlayer" controls preload="metadata">
            Your browser does not support the video tag.
        </video>
        <div id="currentFile">No video selected</div>
    </div>

    <table>
        <thead>
            <tr>
                <th>File Name</th>
                <th style="width: 100px;">Size</th>
                <th style="width: 100px;">Codec</th>
                <th style="width: 80px; text-align: center;">Download</th>
            </tr>
        </thead>
        <tbody>
            {{VIDEO_TABLE_ROWS}}
        </tbody>
    </table>

    <script>
        const videoPlayer = document.getElementById('videoPlayer');
        const currentFile = document.getElementById('currentFile');
        const statusDiv = document.getElementById('status');
        let statusCheckInterval = null;

        function showStatus(message, type, showProgress = false) {
            statusDiv.innerHTML = message;
            if (showProgress) {
                statusDiv.innerHTML += '<div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>';
            }
            statusDiv.className = 'status ' + type;
        }

        function hideStatus() {
            statusDiv.style.display = 'none';
        }

        function checkConversionStatus(videoUrl, fileName) {
            if (statusCheckInterval) {
                clearInterval(statusCheckInterval);
            }

            statusCheckInterval = setInterval(() => {
                fetch('/status/' + encodeURIComponent(fileName))
                    .then(response => response.json())
                    .then(data => {
                        console.log('Conversion status:', data);

                        if (data.status === 'completed') {
                            clearInterval(statusCheckInterval);
                            statusCheckInterval = null;
                            showStatus('✓ Conversion complete! Loading video...', 'success');
                            setTimeout(() => {
                                loadAndPlayVideo(videoUrl, fileName);
                            }, 1000);
                        } else if (data.status === 'converting') {
                            const progressFill = document.getElementById('progressFill');
                            if (progressFill) {
                                progressFill.style.width = data.progress + '%';
                            }
                            showStatus(`Converting H.265 to H.264: ${data.progress}%`, 'warning', true);
                        }
                    })
                    .catch(err => {
                        console.error('Error checking status:', err);
                    });
            }, 1000);
        }

        function loadAndPlayVideo(videoUrl, fileName) {
            videoPlayer.src = videoUrl;
            currentFile.textContent = '▶ Playing: ' + decodeURIComponent(fileName);
            videoPlayer.load();

            videoPlayer.play()
                .then(() => {
                    console.log('Video playing successfully');
                    setTimeout(hideStatus, 2000);
                })
                .catch(err => {
                    console.error('Error playing video:', err);
                    showStatus('Error playing video: ' + err.message, 'error');
                });
        }

        function playVideo(videoUrl, fileName) {
            console.log('Requesting video:', videoUrl);

            // Останавливаем предыдущую проверку статуса
            if (statusCheckInterval) {
                clearInterval(statusCheckInterval);
                statusCheckInterval = null;
            }

            showStatus('Checking video...', 'info');

            // Останавливаем текущее воспроизведение
            videoPlayer.pause();
            videoPlayer.src = '';

            // Делаем обычный GET запрос вместо HEAD
            fetch(videoUrl)
                .then(response => {
                    if (response.status === 202) {
                        // Видео конвертируется
                        return response.json().then(data => {
                            showStatus('Video requires conversion from H.265 to H.264. Please wait...', 'warning', true);
                            checkConversionStatus(videoUrl, fileName);
                        });
                    } else if (response.ok) {
                        // Видео готово
                        loadAndPlayVideo(videoUrl, fileName);
                    } else {
                        throw new Error('Failed to load video');
                    }
                })
                .catch(err => {
                    console.error('Error:', err);
                    showStatus('Error loading video: ' + err.message, 'error');
                });
        }

        // Обработка ошибок видео
        videoPlayer.addEventListener('error', function(e) {
            console.error('Video element error:', e);
            if (videoPlayer.error) {
                let errorMessage = 'Unknown error';
                switch(videoPlayer.error.code) {
                    case 1: errorMessage = 'Video loading aborted'; break;
                    case 2: errorMessage = 'Network error'; break;
                    case 3: errorMessage = 'Video decoding failed'; break;
                    case 4: errorMessage = 'Video format not supported'; break;
                }
                showStatus('Video Error: ' + errorMessage, 'error');
            }
        });

        // События для отладки
        videoPlayer.addEventListener('loadstart', () => console.log('▶ Load started'));
        videoPlayer.addEventListener('loadedmetadata', () => console.log('▶ Metadata loaded'));
        videoPlayer.addEventListener('canplay', () => console.log('▶ Can play'));

        // Очистка при закрытии страницы
        window.addEventListener('beforeunload', () => {
            if (statusCheckInterval) {
                clearInterval(statusCheckInterval);
            }
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    html_template_path = "template.html"
    if not os.path.exists(html_template_path):
        with open(html_template_path, "w", encoding="utf-8") as f:
            f.write(html_template_content)

    video_directory = "./recordings/camera2"
    os.makedirs(video_directory, exist_ok=True)

    server = VideoServer(html_template=html_template_path, port=9596, directory=video_directory)
    server.start()
