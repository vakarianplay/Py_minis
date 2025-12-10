import os
import time
import yaml
import logging
import subprocess
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
    def __init__(self, name, rtsp_url, output_folder, segment_time):
        super().__init__()
        self.name = name
        self.rtsp_url = rtsp_url
        self.output_folder = output_folder
        self.segment_time = segment_time
        self.running = False
        os.makedirs(self.output_folder, exist_ok=True)

    def run(self):
        unix_time = int(time.time())
        output_template = os.path.join(
            self.output_folder,
            f"{unix_time}+%Y-%m-%d_%H-%M-%S.mp4"
        )

        # ffmpeg run command
        ffmpeg_command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",      # error logs ffmpeg
            "-rtsp_transport", "tcp",  # use tcp
            "-i", self.rtsp_url,       # rtsp type
            "-c", "copy",              # no encoding
            "-c:a", "aac",             # audio codec
            "-f", "segment",           # segmentation
            "-segment_time", str(self.segment_time),  # segment duration
            "-strftime", "1",          # Time in filename
            "-reset_timestamps", "1",  # Reset timestamps for containers
            "-metadata", f"description={self.name} {unix_time}",
            "-metadata", f"creation_time={unix_time}",
            output_template
        ]

        logging.info(f"Сamera '{self.name}' Thread ID: {current_thread().ident}")
        self.running = True
        try:
            subprocess.run(ffmpeg_command, check=True)
        except KeyboardInterrupt:
            logging.warning(f"Recording for camera '{self.name}' stopped manually.")
        except Exception as e:
            logging.error(f"Error from camera '{self.name}': {e}")
        finally:
            self.running = False
            logging.info(f"Thread completed for camera '{self.name}' with ID: {current_thread().ident}")

    def stop_recording(self):
        logging.info(f"Stopping recording for camera '{self.name}'")
        self.running = False  # Update internal state if required

class MultiCameraRecorder:
    def __init__(self, config_file):
        self.config_file = config_file
        self.config = self.load_config()
        self.segment_time = self.config.get("segment_duration", 60)
        self.recorders = []

    def load_config(self):
        with open(self.config_file, "r") as f:
            config = yaml.safe_load(f)
        return config

    def start_recording(self):
        logging.info(f"Starting recording for all cameras. Main Thread ID: {current_thread().ident}")

        for camera in self.config.get("cameras", []):
            name = camera["name"]
            rtsp_url = camera["rtsp_url"]
            output_folder = self.config.get("output_folder", "cam") + "/" + name
            recorder = CameraRecorder(name, rtsp_url, output_folder, self.segment_time)
            self.recorders.append(recorder)
            recorder.start()

    def stop_recording(self):
        logging.info("Stopping recording for all cameras.")
        for recorder in self.recorders:
            recorder.stop_recording()

class WebServer(Thread):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        

        server_port = self.config.get("webserver", {}).get("port", 8080)
        print(self.config.get("web_server").get("enabled"))
        
        if eval(str(self.config.get("web_server").get("enabled"))):
            port = self.config.get("web_server").get("port")
            user = self.config.get("web_server").get("user")
            pass_hash = self.config.get("web_server").get("password_hash")
            page_path = self.config.get("web_server").get("html_page")
            dir_path = self.config.get("output_folder")
            
            server = VideoServer(html_template=page_path, port=int(port), directory=dir_path, username=user, password_hash=pass_hash)
            
            logging.info(f"Webserver thread ID: {current_thread().ident}. Port: {port}  User: {user}, Page: {page_path}")
            
            server.start()
        else:
            logging.warning("Webserver disabled")


        while True:
            time.sleep(1)
            
            

def main():
    CONFIG_FILE = "config.yml"
    
    try:
        with open(CONFIG_FILE, "r") as f:
            config = yaml.safe_load(f)
        log_file = config.get("log_file", "record_rtsp.log")
    except Exception as e:
        print(f"Error loading config.yml: {e}")
        return

    # Setup logging
    setup_global_logging(log_file)
    recorder_manager = MultiCameraRecorder(CONFIG_FILE)

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
