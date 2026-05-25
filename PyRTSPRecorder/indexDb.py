import os
import sqlite3
import threading
import subprocess
import logging

class VideoIndexDB:
    VIDEO_EXTENSIONS = {".mp4", ".avi"}

    def __init__(self, db_path, base_directory, scan_interval=30):
        self.db_path = os.path.abspath(db_path)
        self.base_directory = os.path.abspath(base_directory)
        self.scan_interval = int(scan_interval)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._ready = False

        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock, self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    rel_path TEXT PRIMARY KEY,
                    dir_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    full_path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    codec TEXT NOT NULL,
                    needs_conversion INTEGER NOT NULL DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS directories (
                    rel_path TEXT PRIMARY KEY,
                    parent_path TEXT NOT NULL,
                    name TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_videos_dir ON videos(dir_path)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dirs_parent ON directories(parent_path)"
            )

    @property
    def ready(self):
        return self._ready

    def _norm_rel(self, rel_path):
        rel_path = rel_path.replace("\\", "/")
        if rel_path in (".", "./"):
            return ""
        return rel_path.strip("/")

    def _probe_codec(self, full_path):
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    full_path
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )

            codec_raw = result.stdout.strip().lower()
            codec_map = {
                "hevc": "H.265",
                "h265": "H.265",
                "h264": "H.264",
                "vp9": "VP9",
                "vp8": "VP8"
            }
            codec = codec_map.get(
                codec_raw,
                codec_raw.upper() if codec_raw else "Unknown"
            )
            needs = 1 if codec_raw in ("hevc", "h265") else 0
            return codec, needs
        except Exception:
            return "Unknown", 0

    def scan_once(self):
        # Текущие записи (чтобы не дергать ffprobe для неизмененных файлов)
        old = {}
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT rel_path, size, mtime, codec, needs_conversion
                FROM videos
                """
            ).fetchall()

        for row in rows:
            old[row["rel_path"]] = (
                int(row["size"]),
                float(row["mtime"]),
                row["codec"],
                int(row["needs_conversion"])
            )

        dir_rows = [("", "", ".")]
        video_rows = []

        for root, dirs, files in os.walk(self.base_directory):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files = [f for f in files if not f.startswith(".")]

            rel_root = self._norm_rel(
                os.path.relpath(root, self.base_directory)
            )

            for d in dirs:
                d_rel = self._norm_rel(os.path.join(rel_root, d))
                parent = self._norm_rel(os.path.dirname(d_rel))
                dir_rows.append((d_rel, parent, d))

            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in self.VIDEO_EXTENSIONS:
                    continue

                full_path = os.path.join(root, f)
                rel_path = self._norm_rel(os.path.join(rel_root, f))
                dir_path = self._norm_rel(os.path.dirname(rel_path))

                try:
                    st = os.stat(full_path)
                    size = int(st.st_size)
                    mtime = float(st.st_mtime)
                except OSError:
                    continue

                prev = old.get(rel_path)
                if prev and prev[0] == size and abs(prev[1] - mtime) < 1e-6:
                    codec, needs = prev[2], prev[3]
                else:
                    codec, needs = self._probe_codec(full_path)

                video_rows.append(
                    (
                        rel_path,
                        dir_path,
                        f,
                        full_path,
                        size,
                        mtime,
                        codec,
                        needs
                    )
                )

        with self._lock, self.conn:
            self.conn.execute("DELETE FROM directories")
            self.conn.executemany(
                """
                INSERT INTO directories(rel_path, parent_path, name)
                VALUES (?, ?, ?)
                """,
                dir_rows
            )

            self.conn.execute("DELETE FROM videos")
            self.conn.executemany(
                """
                INSERT INTO videos(
                    rel_path, dir_path, name, full_path, size,
                    mtime, codec, needs_conversion
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                video_rows
            )

        self._ready = True
        logging.info(
            "Index updated: dirs=%s videos=%s",
            len(dir_rows),
            len(video_rows)
        )

    def start_background(self):
        if self._thread and self._thread.is_alive():
            return

        def _loop():
            while not self._stop_event.is_set():
                try:
                    self.scan_once()
                except Exception as e:
                    logging.error("Index scan error: %s", e)
                self._stop_event.wait(self.scan_interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def close(self):
        with self._lock:
            self.conn.close()

    def list_items(self, current_dir=""):
        current_dir = self._norm_rel(current_dir)

        with self._lock:
            dirs = self.conn.execute(
                """
                SELECT rel_path, name
                FROM directories
                WHERE parent_path = ?
                ORDER BY name COLLATE NOCASE
                """,
                (current_dir,)
            ).fetchall()

            files = self.conn.execute(
                """
                SELECT rel_path, name, full_path, size, codec, needs_conversion
                FROM videos
                WHERE dir_path = ?
                ORDER BY name COLLATE NOCASE
                """,
                (current_dir,)
            ).fetchall()

        items = []

        for d in dirs:
            if d["rel_path"] == "":
                continue
            items.append(
                {
                    "type": "directory",
                    "name": d["name"],
                    "path": d["rel_path"],
                    "full_path": os.path.join(
                        self.base_directory,
                        d["rel_path"]
                    )
                }
            )

        for f in files:
            items.append(
                {
                    "type": "file",
                    "name": f["name"],
                    "path": f["rel_path"],
                    "full_path": f["full_path"],
                    "size": int(f["size"]),
                    "codec": f["codec"],
                    "needs_conversion": int(f["needs_conversion"])
                }
            )

        return items

    def get_file(self, rel_path):
        rel_path = self._norm_rel(rel_path)

        with self._lock:
            row = self.conn.execute(
                """
                SELECT rel_path, full_path, size, codec, needs_conversion
                FROM videos
                WHERE rel_path = ?
                """,
                (rel_path,)
            ).fetchone()

        if not row:
            return None

        return {
            "rel_path": row["rel_path"],
            "full_path": row["full_path"],
            "size": int(row["size"]),
            "codec": row["codec"],
            "needs_conversion": int(row["needs_conversion"])
        }