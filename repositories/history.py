from __future__ import annotations
import sqlite3, time
from pathlib import Path

class HistoryRepository:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True); self.path = path
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS tests(id INTEGER PRIMARY KEY, created REAL, mode TEXT, channel TEXT, rms REAL, vpp REAL, frequency REAL, amplitude REAL, file TEXT)")
    def add(self, mode, channel, metrics, file=""):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO tests(created,mode,channel,rms,vpp,frequency,amplitude,file) VALUES(?,?,?,?,?,?,?,?)",
                      (time.time(), mode, channel, metrics.rms, metrics.vpp, metrics.dominant_frequency, metrics.dominant_amplitude, file))
    def rows(self):
        with sqlite3.connect(self.path) as c: return c.execute("SELECT created,mode,channel,rms,vpp,frequency,amplitude,file FROM tests ORDER BY id DESC LIMIT 500").fetchall()
