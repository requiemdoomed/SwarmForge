# Author: Swayam Shakya
"""Shared configuration. Environment overrides make isolated test swarms easy."""
import os
from pathlib import PurePath

HOST = os.environ.get("SWARMFORGE_HOST", "127.0.0.1")
TRACKER_HOST = os.environ.get("SWARMFORGE_TRACKER_HOST", HOST)
TRACKER_PORT = int(os.environ.get("SWARMFORGE_TRACKER_PORT", "6000"))
API_BASE_PORT = int(os.environ.get("SWARMFORGE_API_BASE_PORT", "7000"))
CHUNK_SIZE = int(os.environ.get("SWARMFORGE_CHUNK_SIZE", str(64 * 1024)))
SOCKET_TIMEOUT = float(os.environ.get("SWARMFORGE_SOCKET_TIMEOUT", "5"))
MAX_RETRIES = int(os.environ.get("SWARMFORGE_MAX_RETRIES", "3"))
TORRENT_ID = os.environ.get("SWARMFORGE_TORRENT_ID", "swarmforge-demo")
OUTPUT_FILENAME = os.environ.get("SWARMFORGE_OUTPUT_FILENAME", "output.bin")
PIECE_FILES = tuple(name.strip() for name in os.environ.get(
    "SWARMFORGE_PIECE_FILES", "binfile.part1.bin,binfile.part2.bin").split(",") if name.strip())
LOG_LIMIT = int(os.environ.get("SWARMFORGE_LOG_LIMIT", "300"))

# Configured piece/output names must stay inside their chosen directories.
def _is_safe_filename(name):
    return bool(name) and PurePath(name).name == name and "/" not in name and "\\" not in name and name not in {".", ".."}

if not PIECE_FILES or any(not _is_safe_filename(name) for name in PIECE_FILES):
    raise ValueError("SWARMFORGE_PIECE_FILES must contain safe filenames, not paths")
if len(set(PIECE_FILES)) != len(PIECE_FILES):
    raise ValueError("SWARMFORGE_PIECE_FILES cannot contain duplicate filenames")
if not _is_safe_filename(OUTPUT_FILENAME):
    raise ValueError("SWARMFORGE_OUTPUT_FILENAME must be a safe filename, not a path")
if CHUNK_SIZE < 1 or SOCKET_TIMEOUT <= 0 or MAX_RETRIES < 1:
    raise ValueError("Chunk size, socket timeout, and retry count must be positive")
