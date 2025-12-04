import subprocess
import os
import time

def record_rtsp(rtsp_url, output_folder, segment_time=60):
    """
    Запись RTSP потока с аудио в MP4 файлы длительностью по 1 минуте.

    Аргументы:
        rtsp_url (str): URL RTSP потока.
        output_folder (str): Папка для сохранения видеофайлов.
        segment_time (int): Длительность каждого сегмента в секундах (по умолчанию 60 секунд).
    """
    # Убедимся, что папка существует
    os.makedirs(output_folder, exist_ok=True)

    # Команда ffmpeg
    ffmpeg_command = [
        "ffmpeg",
        "-hide_banner",  # Убираем лишний вывод
        "-loglevel", "error",  # Оставляем только ошибки для логов
        "-i", rtsp_url,  # URL RTSP-потока
        "-c", "copy",    # Копируем видеопоток без перекодирования
        "-c:a", "aac",   # Кодек для аудио (AAC)
        "-f", "segment", # Активация сегментации (разделение на файлы)
        "-segment_time", str(segment_time),  # Длительность сегмента
        "-strftime", "1",  # Использование формата времени в названии файла
        os.path.join(output_folder, "%Y-%m-%d_%H-%M-%S.mp4")  # Шаблон имени файлов
    ]

    print("Запуск записи... Нажмите Ctrl+C для остановки.")
    try:
        # Запускаем ffmpeg
        subprocess.run(ffmpeg_command)
    except KeyboardInterrupt:
        print("Запись завершена. Скрипт остановлен.")

if __name__ == "__main__":
    # Введите URL RTSP поток вашей камеры
    RTSP_URL = "rtsp://stream.url/1/1"

    # Папка для сохранения файлов
    OUTPUT_FOLDER = "./recordings"

    # Длительность сегмента в секундах (1 минута)
    SEGMENT_DURATION = 60

    # Запускаем функцию
    record_rtsp(RTSP_URL, OUTPUT_FOLDER, SEGMENT_DURATION)