import os
from http.server import SimpleHTTPRequestHandler, HTTPServer
from functools import partial
from urllib.parse import unquote
import mimetypes

class VideoServer:
    def __init__(self, html_template, port, directory):
        self.html_template = os.path.abspath(html_template)
        self.port = port
        self.directory = os.path.abspath(directory)
        print(f"Serving directory: {self.directory}")

    def start(self):
        handler = partial(self.CustomHandler, self.html_template, self.directory)
        server = HTTPServer(('', self.port), handler)
        print(f"Starting server on port {self.port}. Visit http://localhost:{self.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")
            server.server_close()

    class CustomHandler(SimpleHTTPRequestHandler):
        def __init__(self, html_template, directory, *args, **kwargs):
            self.html_template = html_template
            self.base_directory = directory
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self):
            """Обрабатывает GET-запросы"""
            if self.path == '/':
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(self._render_main_page().encode('utf-8'))
            elif self.path.startswith('/videos/'):
                file_name = unquote(self.path[8:])
                # Проверяем параметр download
                if '?download=1' in self.path:
                    self.send_download_file(file_name.replace('?download=1', ''))
                else:
                    self.send_video_file(file_name)
            else:
                self.send_error(404, "File Not Found")

        def _render_main_page(self):
            """Создает HTML главной страницы"""
            try:
                files = sorted([
                    f for f in os.listdir(self.base_directory)
                    if os.path.isfile(os.path.join(self.base_directory, f))
                ])
            except Exception as e:
                print(f"Error listing directory: {e}")
                files = []

            video_table_rows = ""
            for file in files:
                file_url = f"/videos/{file}"
                download_url = f"/videos/{file}?download=1"
                video_table_rows += (
                    f"<tr>"
                    f"<td><a href='#' onclick=\"playVideo('{file_url}'); return false;\">{file}</a></td>"
                    f"<td><a href='{download_url}' class='download-link'>&#128190;</a></td>"
                    f"</tr>"
                )

            try:
                with open(self.html_template, 'r', encoding='utf-8') as f:
                    html_content = f.read()
            except Exception as e:
                print(f"Error reading template: {e}")
                return f"<html><body><h1>Error loading template</h1><p>{e}</p></body></html>"

            return html_content.replace("{{VIDEO_TABLE_ROWS}}", video_table_rows)

        def send_video_file(self, file_name):
            """Отправляет видеофайл для воспроизведения в браузере"""
            try:
                file_path = os.path.join(self.base_directory, file_name)

                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                    self.send_error(404, "File not found")
                    return

                # Определяем MIME-тип
                mime_type, _ = mimetypes.guess_type(file_path)
                if mime_type is None:
                    # Определяем тип по расширению
                    ext = os.path.splitext(file_path)[1].lower()
                    mime_types_map = {
                        '.mp4': 'video/mp4',
                        '.webm': 'video/webm',
                        '.ogg': 'video/ogg',
                        '.mov': 'video/quicktime',
                        '.avi': 'video/x-msvideo',
                        '.mkv': 'video/x-matroska'
                    }
                    mime_type = mime_types_map.get(ext, 'application/octet-stream')

                file_size = os.path.getsize(file_path)
                range_header = self.headers.get('Range')

                if range_header:
                    self.send_range_response(file_path, file_size, mime_type, range_header)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Length", file_size)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()

                    with open(file_path, 'rb') as f:
                        chunk_size = 8192
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self.wfile.write(chunk)

            except BrokenPipeError:
                print("Client disconnected")
            except Exception as e:
                print(f"Error sending file: {e}")
                try:
                    self.send_error(500, f"Internal Server Error: {e}")
                except:
                    pass

        def send_range_response(self, file_path, file_size, mime_type, range_header):
            """Обрабатывает Range-запросы для потокового видео"""
            try:
                byte_range = range_header.replace('bytes=', '').split('-')
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if byte_range[1] else file_size - 1

                if start >= file_size:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return

                if end >= file_size:
                    end = file_size - 1

                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", length)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    chunk_size = 8192
                    while remaining > 0:
                        read_size = min(chunk_size, remaining)
                        chunk = f.read(read_size)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

            except BrokenPipeError:
                print("Client disconnected during range response")
            except Exception as e:
                print(f"Error in range response: {e}")
                try:
                    self.send_error(500, f"Internal Server Error: {e}")
                except:
                    pass

        def send_download_file(self, file_name):
            """Отправляет файл для скачивания"""
            try:
                file_path = os.path.join(self.base_directory, file_name)

                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                    self.send_error(404, "File not found")
                    return

                file_size = os.path.getsize(file_path)

                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(file_path)}"')
                self.send_header("Content-Length", file_size)
                self.end_headers()

                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())

            except Exception as e:
                print(f"Error sending download: {e}")
                self.send_error(500, f"Internal Server Error: {e}")

        def log_message(self, format, *args):
            """Переопределяем для более чистого лога"""
            print(f"{self.address_string()} - {format % args}")

# HTML шаблон
html_template_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Server</title>
    <style>
        body { 
            font-family: Arial, sans-serif; 
            margin: 30px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
        }
        #videoContainer {
            background-color: #000;
            margin-bottom: 20px;
            border-radius: 8px;
            overflow: hidden;
        }
        #videoPlayer {
            width: 100%;
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
        }
        tr:hover {
            background-color: #f5f5f5;
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
        }
        .download-link:hover {
            text-decoration: none;
            transform: scale(1.2);
            display: inline-block;
        }
    </style>
</head>
<body>
    <h1>Video Viewer</h1>
    <div id="videoContainer">
        <video id="videoPlayer" controls preload="metadata">
            Your browser does not support the video tag.
        </video>
        <div id="currentFile" style="display:none;">No video selected</div>
    </div>
    <table>
        <thead>
            <tr>
                <th>File</th>
                <th>Download</th>
            </tr>
        </thead>
        <tbody>
            {{VIDEO_TABLE_ROWS}}
        </tbody>
    </table>
    <script>
        const videoPlayer = document.getElementById('videoPlayer');
        const currentFile = document.getElementById('currentFile');

        function playVideo(videoUrl) {
            console.log('Playing video:', videoUrl);
            videoPlayer.src = videoUrl;
            videoPlayer.load();

            const fileName = videoUrl.split('/').pop();
            currentFile.textContent = 'Playing: ' + decodeURIComponent(fileName);
            currentFile.style.display = 'block';

            videoPlayer.play().catch(err => {
                console.error('Error playing video:', err);
                alert('Error playing video: ' + err.message);
            });
        }

        videoPlayer.addEventListener('error', function(e) {
            console.error('Video error:', e);
            if (videoPlayer.error) {
                console.error('Error code:', videoPlayer.error.code);
                console.error('Error message:', videoPlayer.error.message);
            }
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    html_template_path = "template.html"
    

    video_directory = "./recordings/camera2"
    os.makedirs(video_directory, exist_ok=True)

    server = VideoServer(html_template=html_template_path, port=9596, directory=video_directory)
    server.start()
