# RTSP Recorder

![alt text](https://img.shields.io/badge/compatible%20-linux-blue?style=flat-square&logo=linux)
![alt text](https://img.shields.io/badge/python%20-3.xx-blue?style=flat-square&logo=python)
![alt text](https://img.shields.io/badge/FFmpeg-gray?style=flat-square&logo=ffmpeg)

### Record RTSP streams and CCTV and watch video archive via WebInterface

--------------------

**Requirements:**
- `pip install yaml`
- `pip install threading`
- `apt install ffmpeg`
  
----------------

🌀 **Default Webserver credentials**

Username: `admin`. Pass: `demopassword`. http://localhost:9596

Username and password you can change in `config.yml`. For pass used sha256 hash

-----------------

🎯 **Usage**

```bash
python3 recorder_main.py
```

alternative method with watchdog

```bash
chmod +x recorder_run.sh
./recorder_run.sh
```

------------------------

🔧 **Configuration**

Edit the `config.yml` file:

```yaml  
segment_duration: 30
log_file: ./logs/record_rtsp.log
output_folder: ./recordings
cameras:
  - name: Camera1
    rtsp_url: rtsp://camera.example/stream1  
  - name: Camera2
    rtsp_url: rtsp://camera.example/stream2 
  - name: Camera3
    rtsp_url: rtsp://camera.example/stream3
web_server:
  enabled: true
  port: 8080
  user: admin
  password_hash: 5487cf596c53bf12e05cec7d9e2b719478cba212eb9e146e927900b48825f872 #sha256 hash from passphrase. Default pass: demopassword
  html_page: index.html
```


---------------------

**⚙️ Systemd daeamon**

Create systemd daemon for run recorder as service

- Create systemd daemon `sudo nano /etc/systemd/system/recorder.service`
```ini
[Unit]
Description=RTSP Recorder
After=default.target

[Service]
User=your_user
Restart=on-abort
WorkingDirectory=/path/to/PyRTSPRecorder/
ExecStart=/path/to/PyRTSPRecorder/./recorder_run.sh

[Install]
WantedBy=default.target
```

- Reload systemd `systemctl daemon-reload`
- Run service `service recorder start` or `sudo systemctl start recorder.service`
  
---------------------


<img width="1905" height="844" alt="image" src="https://github.com/user-attachments/assets/d9ee508c-9df6-4557-b605-cdc90c353cd3" /> <img width="1896" height="912" alt="image" src="https://github.com/user-attachments/assets/3c8b74d4-c8ce-4a23-a6ef-6f55d0424c3f" />

