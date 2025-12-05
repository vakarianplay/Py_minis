import os
import time
import yaml
import logging
import subprocess
from threading import Thread


def setup_global_logging(log_file):
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logging.basicConfig(
        level=logging.INFO, 
        format="%(asctime)s [%(levelname)s] %(message)s",
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
            output_template
        ]

        logging.info(f"Init record '{self.name}' src: '{self.output_folder}'")
        self.running = True
        try:
            subprocess.run(ffmpeg_command, check=True)
        except KeyboardInterrupt:
            logging.warning(f"'{self.name}' stoped")
        except Exception as e:
            logging.error(f"Error from '{self.name}': {e}")
        finally:
            self.running = False
            logging.info(f"Record complete for '{self.name}'")

    def stop_recording(self):
        logging.info(f"Stop record for '{self.name}'")
        self.running = False

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

        logging.info("Start recording")

        for camera in self.config.get("cameras", []):
            name = camera["name"]
            rtsp_url = camera["rtsp_url"]
            output_folder = camera["output_folder"]
            recorder = CameraRecorder(name, rtsp_url, output_folder, self.segment_time)
            self.recorders.append(recorder)
            recorder.start()

    def stop_recording(self):
        logging.info("Stop recording")
        for recorder in self.recorders:
            recorder.stop_recording()

def main():
    CONFIG_FILE = "config.yml"

    try:
        with open(CONFIG_FILE, "r") as f:
            config = yaml.safe_load(f)
        log_file = config.get("log_file", "record_rtsp.log")
    except Exception as e:
        print(f"Error load config.yml: {e}")
        return

    setup_global_logging(log_file)
    recorder_manager = MultiCameraRecorder(CONFIG_FILE)

    try:
        recorder_manager.start_recording()
        while True:
            time.sleep(1) 
    except KeyboardInterrupt:
        logging.info("Stop manually.")
        recorder_manager.stop_recording()

if __name__ == "__main__":
    main()
