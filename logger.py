# Author: Swayam Shakya
"""Thread-safe in-memory logging shared by the node and GUI API."""
from collections import deque
from datetime import datetime
import threading
from config import LOG_LIMIT

_logs = deque(maxlen=LOG_LIMIT)
_lock = threading.Lock()

def log(message, level="INFO"):
    line = f"{datetime.now().strftime('%H:%M:%S')} [{level}] {message}"
    with _lock:
        _logs.append(line)
    print(line, flush=True)
    return line

def get_logs(limit=100):
    with _lock:
        return list(_logs)[-max(1, min(int(limit), LOG_LIMIT)):]
