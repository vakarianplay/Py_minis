import os
import sys
import time
import atexit
import yaml
import signal
import socket
import tempfile
import shutil
import threading
import subprocess
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import psutil
except Exception:
    psutil = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yml")
LAUNCHER_LOG = os.path.join(BASE_DIR, "launcher.log")

RECORDER_PROCESS = None
BROWSER_PROCESS = None
FIREFOX_PROFILE_DIR = None

STOP_EVENT = threading.Event()
STOP_LOCK = threading.Lock()
STOP_DONE = False

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {msg}\n")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def detect_recorder_script():
    for name in ("recorder_main.py", "recorder-main.py"):
        p = os.path.join(BASE_DIR, name)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("Не найден recorder_main.py или recorder-main.py")

def is_port_open(port, host="127.0.0.1", timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        s.close()

def start_recorder_hidden(script_path):
    global RECORDER_PROCESS

    cmd = [sys.executable, script_path]
    out = open(LAUNCHER_LOG, "a", encoding="utf-8")

    kwargs = {
        "cwd": BASE_DIR,
        "stdin": subprocess.DEVNULL,
        "stdout": out,
        "stderr": out,
    }

    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    RECORDER_PROCESS = subprocess.Popen(cmd, **kwargs)
    log(f"Recorder started PID={RECORDER_PROCESS.pid}")

def wait_server_alive(url, timeout_sec=90):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if STOP_EVENT.is_set():
            return False

        if RECORDER_PROCESS and RECORDER_PROCESS.poll() is not None:
            log(f"Recorder exited code={RECORDER_PROCESS.returncode}")
            return False

        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    return True
        except HTTPError as e:
            # 401 = сервер уже работает, просто требует авторизацию
            if e.code == 401:
                return True
        except (URLError, socket.timeout, ConnectionError):
            pass

        time.sleep(0.5)

    log("Server wait timeout")
    return False

def find_firefox():
    for cmd in ("firefox", "firefox-esr"):
        if subprocess.call(
            ["which", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ) == 0:
            return cmd
    return None

def start_firefox_kiosk(url):
    global BROWSER_PROCESS, FIREFOX_PROFILE_DIR

    firefox = find_firefox()
    if not firefox:
        log("Firefox not found")
        return False

    # отдельный временный профиль -> стабильный отдельный процесс
    FIREFOX_PROFILE_DIR = tempfile.mkdtemp(prefix="rtsp_launcher_ff_")

    cmd = [
        firefox,
        "--no-remote",
        "--new-instance",
        "--profile", FIREFOX_PROFILE_DIR,
        "--kiosk",
        "--new-window",
        url
    ]

    BROWSER_PROCESS = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    log(f"Firefox started PID={BROWSER_PROCESS.pid}")
    return True

def kill_process_tree(pid):
    if pid <= 0:
        return

    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)

            for c in children:
                try:
                    c.terminate()
                except Exception:
                    pass

            _, alive = psutil.wait_procs(children, timeout=3)
            for c in alive:
                try:
                    c.kill()
                except Exception:
                    pass

            try:
                parent.terminate()
                parent.wait(timeout=3)
            except Exception:
                try:
                    parent.kill()
                except Exception:
                    pass
            return
        except Exception:
            pass

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        else:
            os.killpg(pid, signal.SIGTERM)
            time.sleep(0.3)
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass
    except Exception:
        pass

def stop_all():
    global STOP_DONE
    global RECORDER_PROCESS, BROWSER_PROCESS, FIREFOX_PROFILE_DIR

    with STOP_LOCK:
        if STOP_DONE:
            return
        STOP_DONE = True

    STOP_EVENT.set()

    if BROWSER_PROCESS and BROWSER_PROCESS.poll() is None:
        try:
            BROWSER_PROCESS.terminate()
            BROWSER_PROCESS.wait(timeout=4)
        except Exception:
            try:
                BROWSER_PROCESS.kill()
            except Exception:
                pass
    BROWSER_PROCESS = None

    if RECORDER_PROCESS is not None:
        try:
            if RECORDER_PROCESS.poll() is None:
                RECORDER_PROCESS.terminate()
                try:
                    RECORDER_PROCESS.wait(timeout=6)
                except Exception:
                    pass

            if RECORDER_PROCESS.poll() is None:
                kill_process_tree(RECORDER_PROCESS.pid)
        except Exception:
            pass
    RECORDER_PROCESS = None

    if FIREFOX_PROFILE_DIR and os.path.isdir(FIREFOX_PROFILE_DIR):
        try:
            shutil.rmtree(FIREFOX_PROFILE_DIR, ignore_errors=True)
        except Exception:
            pass
    FIREFOX_PROFILE_DIR = None

    log("All stopped")

def recorder_watchdog():
    while not STOP_EVENT.is_set():
        time.sleep(1)
        if RECORDER_PROCESS and RECORDER_PROCESS.poll() is not None:
            log(f"Recorder died unexpectedly code={RECORDER_PROCESS.returncode}")
            stop_all()
            break

def main():
    try:
        config = load_config()
        web_cfg = config.get("web_server", {})
        live_cfg = config.get("live_server", {})

        port = int(web_cfg.get("port", 8080))
        live_enabled = bool(live_cfg.get("enabled", False))
        path = "/live" if live_enabled else "/"

        live_url = f"http://127.0.0.1:{port}{path}"
        log("Launcher started")

        if is_port_open(port):
            log(f"Port {port} already in use. Stop old instance first.")
            return

        script = detect_recorder_script()
        start_recorder_hidden(script)

        atexit.register(stop_all)
        try:
            signal.signal(signal.SIGTERM, lambda *_: stop_all())
            signal.signal(signal.SIGINT, lambda *_: stop_all())
        except Exception:
            pass

        threading.Thread(target=recorder_watchdog, daemon=True).start()

        if not wait_server_alive(live_url, timeout_sec=90):
            stop_all()
            return

        if not start_firefox_kiosk(live_url):
            stop_all()
            return

        try:
            # Ждем закрытия Firefox
            BROWSER_PROCESS.wait()
        except KeyboardInterrupt:
            pass
        finally:
            stop_all()

    except Exception as e:
        log(f"Fatal launcher error: {e}")
        stop_all()

if __name__ == "__main__":
    main()