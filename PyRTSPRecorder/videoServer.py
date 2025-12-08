import os
from http.server import SimpleHTTPRequestHandler, HTTPServer
from functools import partial
from urllib.parse import unquote

class VideoServer:
    def __init__(self, html_template, port, directory):
        self.html_template = os.path.abspath(html_template)  # Путь к HTML-шаблону
        self.port = port
        self.directory = os.path.abspath(directory)  # Абсолютный путь к директории
        print (self.directory)

    def start(self):
        # Создаем директорию, если ее нет
        os.makedirs(self.directory, exist_ok=True)

        # Создаем обработчик с переданными параметрами
        handler = partial(self.CustomHandler, self.html_template, self.directory)

        # Запускаем HTTP-сервер
        server = HTTPServer(('', self.port), handler)
        print(f"Starting server on port {self.port}. Visit http://localhost:{self.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")
            server.server_close()

    class CustomHandler(SimpleHTTPRequestHandler):
        def __init__(self, html_template, directory, *args, **kwargs):
            self.html_template = html_template  # Связываем шаблон с обработчиком
            self.directory = directory  # Связываем директорию с обработчиком
            super().__init__(*args, **kwargs)

        def translate_path(self, path):
            """
            Переводит URL-путь в путь файловой системы, основываясь на self.directory
            """
            # Базовая директория — это self.directory
            relative_path = unquote(path.lstrip("/"))
            absolute_path = os.path.join(self.directory, relative_path)
            return absolute_path

        def do_GET(self):
            """
            Обрабатывает GET-запросы на рендеринг главной страницы и загрузку файлов
            """
            if self.path == '/':  # Главная страница
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(self._render_main_page().encode('utf-8'))
            elif self.path.startswith('/videos/'):  # Видео или файл для скачивания
                self.send_file(self.path.lstrip('/'))
            else:
                self.send_error(404, "File Not Found")

        def _render_main_page(self):
            """
            Создает HTML-код главной страницы на основе шаблона и содержимого директории
            """
            # Получаем список видеофайлов из директории
            files = [
                f for f in os.listdir("./recordings/camera2")
                if os.path.isfile(os.path.join("./recordings/camera2", f))
            ]
            print (files)

            video_table_rows = ""
            for file in files:
                file_url = f"/videos/{self.directory}{file}"
                download_url = f"/videos/{self.directory}{file}"
                video_table_rows += (
                    f"<tr>"
                    f"<td><a href='#' onclick=\"playVideo('{file_url}')\">{file}</a></td>"
                    f"<td><a href='{download_url}' download>&#128190;</a></td>"
                    f"</tr>"
                )

            # Читаем HTML-шаблон и заполняем его
            with open(self.html_template, 'r', encoding='utf-8') as f:
                html_content = f.read()

            return html_content.replace("{{VIDEO_TABLE_ROWS}}", video_table_rows)

        def send_file(self, file_path):
            """
            Отправляет файл клиенту
            """
            try:
                abs_path = os.path.join(self.directory, file_path.replace("videos/", ""))
                if os.path.exists(abs_path) and os.path.isfile(abs_path):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f"attachment; filename={os.path.basename(abs_path)}")
                    self.send_header("Content-Length", os.path.getsize(abs_path))
                    self.end_headers()
                    with open(abs_path, 'rb') as f:
                        self.wfile.write(f.read())
                else:
                    self.send_error(404, "File not found")
            except Exception as e:
                self.send_error(500, f"Error: {e}")

# Шаблон HTML
html_template_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Server</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 30px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f4f4f4; }
        a { text-decoration: none; color: #007BFF; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <h1>Video Viewer</h1>
    <video id="videoPlayer" controls width="100%" style="margin-bottom: 20px;">
        <source src="" type="video/mp4">
        Your browser does not support the video tag.
    </video>
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
        function playVideo(videoUrl) {
            const videoPlayer = document.getElementById('videoPlayer');
            videoPlayer.src = videoUrl;
            videoPlayer.play();
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    # Создаем HTML-шаблон, если он отсутствует
    html_template_path = "template.html"
    if not os.path.exists(html_template_path):
        with open(html_template_path, "w", encoding="utf-8") as f:
            f.write(html_template_content)

    # Указываем папку с видео
    directory = "./recordings/camera2"
    os.makedirs(directory, exist_ok=True)  # Создаем папку, если ее нет

    # Создаем и запускаем сервер
    server = VideoServer(html_template=html_template_path, port=9596, directory=directory)
    server.start()
