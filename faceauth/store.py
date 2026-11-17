"""Encrypted template store (SQLite). Only face EMBEDDINGS are kept, never photos,
and they are encrypted at rest with a key stored separately (data_dir/secret.key).
Also keeps the audit log and failure counters for lockout."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
from cryptography.fernet import Fernet


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file = data_dir / "secret.key"
        if not key_file.exists():
            key_file.write_bytes(Fernet.generate_key())
            try:
                key_file.chmod(0o600)
            except OSError:
                pass
        self.f = Fernet(key_file.read_bytes())
        self.db = sqlite3.connect(data_dir / "faceauth.db", check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS templates(
                user_id TEXT PRIMARY KEY, emb BLOB NOT NULL, created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS failures(
                user_id TEXT PRIMARY KEY, count INTEGER, last REAL);
            CREATE TABLE IF NOT EXISTS audit(
                ts REAL, project TEXT, user_id TEXT, event TEXT, ok INTEGER, detail TEXT);
            """)
            self.db.commit()

    # --- templates -----------------------------------------------------------------
    def put(self, user_id: str, emb: np.ndarray) -> None:
        blob = self.f.encrypt(emb.astype(np.float32).tobytes())
        now = time.time()
        with self.lock:
            self.db.execute("INSERT INTO templates VALUES(?,?,?,?) ON CONFLICT(user_id) "
                            "DO UPDATE SET emb=excluded.emb, updated=excluded.updated",
                            (user_id, blob, now, now))
            self.db.commit()

    def get(self, user_id: str) -> Optional[np.ndarray]:
        with self.lock:
            row = self.db.execute("SELECT emb FROM templates WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        return np.frombuffer(self.f.decrypt(row[0]), dtype=np.float32).copy()

    def delete(self, user_id: str) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM templates WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM failures WHERE user_id=?", (user_id,))
            self.db.commit()
        return cur.rowcount > 0

    # --- lockout -------------------------------------------------------------------
    def locked_for(self, user_id: str, max_fail: int, lockout_s: int) -> float:
        with self.lock:
            row = self.db.execute("SELECT count,last FROM failures WHERE user_id=?", (user_id,)).fetchone()
        if not row or row[0] < max_fail:
            return 0.0
        return max(0.0, row[1] + lockout_s - time.time())

    def fail(self, user_id: str, lockout_s: int) -> None:
        now = time.time()
        with self.lock:
            row = self.db.execute("SELECT count,last FROM failures WHERE user_id=?", (user_id,)).fetchone()
            count = 1 if (not row or now - row[1] > lockout_s) else row[0] + 1
            self.db.execute("INSERT INTO failures VALUES(?,?,?) ON CONFLICT(user_id) "
                            "DO UPDATE SET count=excluded.count, last=excluded.last", (user_id, count, now))
            self.db.commit()

    def reset_failures(self, user_id: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM failures WHERE user_id=?", (user_id,))
            self.db.commit()

    # --- audit ---------------------------------------------------------------------
    def audit(self, project: str, user_id: str, event: str, ok: bool, detail: dict | str = "") -> None:
        d = detail if isinstance(detail, str) else json.dumps(detail)
        with self.lock:
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)",
                            (time.time(), project, user_id, event, int(ok), d))
            self.db.commit()
