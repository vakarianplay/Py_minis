import os
import time
import yaml
import logging
import subprocess
import threading

from threading import Thread, current_thread
from videoServer import VideoServer

def setup_global_logging(log_file):
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [Thread-%(thread)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

class CameraRecorder(Thread):
    def __init__(
        self,
        name,
        rtsp_url,
        output_folder,
        segment_time,
        reconnect_interval_sec=5,
        reconnect_max_interval_sec=60
    ):
        super().__init__(daemon=True)
        self.name = name
        self.rtsp_url = rtsp_url
        self.output_folder = output_folder
        self.segment_time = int(segment_time)
        self.reconnect_interval_sec = int(reconnect_interval_sec)
        self.reconnect_max_interval_sec = int(reconnect_max_interval_sec)

        self.stop_event = threading.Event()
        self.process = None

        os.makedirs(self.output_folder, exist_ok=True)

    def _build_ffmpeg_command(self):
        unix_time = int(time.time())
        output_template = os.path.join(
            self.output_folder,
            f"{unix_time}+%Y-%m-%d_%H-%M-%S.mp4"
        )

        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-c", "copy",
            "-c:a", "aac",
            "-f", "segment",
            "-segment_time", str(self.segment_time),
            "-strftime", "1",
            "-reset_timestamps", "1",
            "-metadata", f"description={self.name} {unix_time}",
            "-metadata", f"creation_time={unix_time}",
            output_template
        ]

    def _stop_process(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None

    def run(self):
        logging.info(
            "Camera '%s' thread started. Thread ID: %s",
            self.name,
            current_thread().ident
        )

        backoff = self.reconnect_interval_sec

        while not self.stop_event.is_set():
            cmd = self._build_ffmpeg_command()
            logging.info("Camera '%s': connecting to RTSP...", self.name)

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )

                # Ждем завершения ffmpeg
                _, err = self.process.communicate()
                rc = self.process.returncode

                if self.stop_event.is_set():
                    break

                # Если дошли сюда — ffmpeg завершился сам (ошибка/разрыв/и т.д.)
                short_err = (err or "").strip().splitlines()
                short_err = short_err[-1] if short_err else "no error output"

                logging.warning(
                    "Camera '%s': ffmpeg stopped (code=%s). Reason: %s",
                    self.name,
                    rc,
                    short_err
                )

            except Exception as e:
                if self.stop_event.is_set():
                    break
                logging.error(
                    "Camera '%s': launch error: %s",
                    self.name,
                    e
                )
            finally:
                self._stop_process()


            if self.stop_event.is_set():
                break

            logging.info(
                "Camera '%s': reconnect in %s sec",
                self.name,
                backoff
            )
            self.stop_event.wait(backoff)
            backoff = min(backoff * 2, self.reconnect_max_interval_sec)

            # После успешного долгого запуска backoff можно сбрасывать ниже.
            # Тут оставим простой вариант: сбросим при каждом новом цикле.
            # Это не мешает, потому что wait вызывается только при падении.
            if backoff > self.reconnect_interval_sec:
                backoff = self.reconnect_interval_sec

        self._stop_process()
        logging.info("Camera '%s' thread stopped", self.name)

    def stop_recording(self):
        self.stop_event.set()
        self._stop_process()

class MultiCameraRecorder:
    def __init__(self, config_file):
        self.config_file = config_file
        self.config = self.load_config()
        self.segment_time = int(self.config.get("segment_duration", 60))
        self.recorders = []

        rec_cfg = self.config.get("recorder", {})
        self.reconnect_interval_sec = int(rec_cfg.get("reconnect_interval_sec", 5))
        self.reconnect_max_interval_sec = int(rec_cfg.get("reconnect_max_interval_sec", 60))

    def load_config(self):
        with open(self.config_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def start_recording(self):
        logging.info(
            "Starting recording for all cameras. Main Thread ID: %s",
            current_thread().ident
        )

        for camera in self.config.get("cameras", []):
            name = camera["name"]
            rtsp_url = camera["rtsp_url"]
            output_folder = os.path.join(
                self.config.get("output_folder", "cam"),
                name
            )

            recorder = CameraRecorder(
                name=name,
                rtsp_url=rtsp_url,
                output_folder=output_folder,
                segment_time=self.segment_time,
                reconnect_interval_sec=self.reconnect_interval_sec,
                reconnect_max_interval_sec=self.reconnect_max_interval_sec
            )
            self.recorders.append(recorder)
            recorder.start()

    def stop_recording(self):
        logging.info("Stopping recording for all cameras.")
        for recorder in self.recorders:
            recorder.stop_recording()

        for recorder in self.recorders:
            recorder.join(timeout=6)

class WebServer(Thread):
    def __init__(self, config):
        super().__init__(daemon=True)
        self.config = config

    def run(self):
        web_cfg = self.config.get("web_server", {})
        if not bool(web_cfg.get("enabled", False)):
            logging.warning("Webserver disabled")
            return

        port = int(web_cfg.get("port", 8080))
        user = web_cfg.get("user")
        pass_hash = web_cfg.get("password_hash")
        page_path = web_cfg.get("html_page", "index.html")
        dir_path = self.config.get("output_folder", "./recordings")

        db_cfg = self.config.get("DB", {})
        index_db = db_cfg.get("index_db", "./video_index.db")
        index_interval = int(db_cfg.get("index_scan_interval_sec", 30))

        live_cfg = self.config.get("live_server", {})
        live_enabled = bool(live_cfg.get("enabled", False))
        live_template = (
            live_cfg.get("html_page", "live.html")
            if live_enabled else None
        )

        cameras_map = {}
        for cam in self.config.get("cameras", []):
            name = cam.get("name")
            rtsp = cam.get("rtsp_url")
            if name and rtsp:
                cameras_map[name] = rtsp

        server = VideoServer(
            html_template=page_path,
            port=port,
            directory=dir_path,
            username=user,
            password_hash=pass_hash,
            index_db_path=index_db,
            index_scan_interval=index_interval,
            live_template=live_template,
            cameras=cameras_map
        )

        logging.info(
            "Webserver thread ID: %s | port=%s | archive_page=%s | live=%s",
            current_thread().ident,
            port,
            page_path,
            live_enabled
        )

        server.start()

def main():
    config_file = "config.yml"

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config.yml: {e}")
        return

    setup_global_logging(config.get("log_file", "record_rtsp.log"))

    recorder_manager = MultiCameraRecorder(config_file)

    try:
        recorder_manager.start_recording()

        webserver = WebServer(config)
        webserver.start()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Stopping recording manually.")
        recorder_manager.stop_recording()

if __name__ == "__main__":
    main()