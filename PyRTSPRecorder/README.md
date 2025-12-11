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



🔧 **Configuration**
Edit the config.yml file:

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
  password_hash: 86790b005b9b7bef99f759204287538f6bdc86889b5362b6ab28c4cc171842cf #sha256 hash from passphrase
  html_page: index.html
```
