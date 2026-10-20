# Author: Swayam Shakya
"""End-to-end smoke test: seed -> leecher -> leecher, using isolated local ports."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_api(port, env, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", env["SWARMFORGE_API_BASE_PORT"] and int(env["SWARMFORGE_API_BASE_PORT"]) + port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Node API for peer {port} did not start")


def api(port, action, env):
    import json
    api_port = int(env["SWARMFORGE_API_BASE_PORT"]) + port
    with socket.create_connection(("127.0.0.1", api_port), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall((json.dumps({"action": action}) + "\n").encode())
        data = bytearray()
        while not data.endswith(b"\n"):
            block = sock.recv(1)
            if not block:
                break
            data.extend(block)
    return json.loads(data.decode())


def wait_manifest(port, env, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api(port, "stats", env)
        if status.get("manifest_loaded"):
            return status
        time.sleep(0.1)
    raise RuntimeError(f"Peer {port} did not load the canonical manifest within {timeout}s")


def wait_download(port, env, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api(port, "stats", env)
        if status.get("progress") == 100.0 and not status.get("download_running"):
            return status
        if status.get("last_error"):
            raise RuntimeError(status["last_error"])
        time.sleep(0.15)
    raise RuntimeError(f"Peer {port} did not complete within {timeout}s")


def main():
    with tempfile.TemporaryDirectory(prefix="swarmforge-test-") as tmp:
        base = Path(tmp)
        seed = base / "seed"
        partial = base / "partial"
        empty = base / "empty"
        out_partial = base / "out_partial"
        out1 = base / "out1"
        out2 = base / "out2"
        for folder in (seed, partial, empty, out_partial, out1, out2):
            folder.mkdir()
        piece1 = b"SwarmForge piece one\n" * 200
        # Deliberately use identical content in two different piece positions. The
        # manifest must keep them distinct by piece ID or reconstruction would lose data.
        piece2 = piece1
        (seed / "binfile.part1.bin").write_bytes(piece1)
        (seed / "binfile.part2.bin").write_bytes(piece2)
        # A partial seed has only the first piece but must learn the complete manifest.
        (partial / "binfile.part1.bin").write_bytes(piece1)

        tracker_port = free_port()
        api_base = 20000
        peer1, peer2, peer3, peer4 = 5101, 5102, 5103, 5104
        env = os.environ.copy()
        env.update({"SWARMFORGE_TRACKER_PORT": str(tracker_port),
                    "SWARMFORGE_API_BASE_PORT": str(api_base),
                    "SWARMFORGE_TORRENT_ID": f"swarmforge-test-{os.getpid()}"})
        processes = []
        try:
            processes.append(subprocess.Popen([sys.executable, "tracker.py"], cwd=ROOT, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(0.35)
            processes.append(subprocess.Popen([sys.executable, "node.py", str(peer1), "--share-dir", str(seed), "--output-dir", str(base / "seed-out")], cwd=ROOT, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for_api(peer1, env)
            wait_manifest(peer1, env)
            processes.append(subprocess.Popen([sys.executable, "node.py", str(peer4), "--share-dir", str(partial), "--output-dir", str(out_partial)], cwd=ROOT, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for_api(peer4, env)
            wait_manifest(peer4, env)
            if not api(peer4, "download", env).get("ok"):
                raise RuntimeError("Could not start partial-seed download")
            wait_download(peer4, env)
            if (out_partial / "output.bin").read_bytes() != piece1 + piece2:
                raise AssertionError("Partial seed did not discover/download the missing piece")

            processes.append(subprocess.Popen([sys.executable, "node.py", str(peer2), "--share-dir", str(empty), "--output-dir", str(out1)], cwd=ROOT, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for_api(peer2, env)
            wait_manifest(peer2, env)
            result = api(peer2, "download", env)
            if not result.get("ok"):
                raise RuntimeError(f"Could not start first download: {result}")
            wait_download(peer2, env)
            expected = piece1 + piece2
            if (out1 / "output.bin").read_bytes() != expected:
                raise AssertionError("First leecher output does not match original pieces")

            # The first leecher should now be able to seed its verified pieces to another leecher.
            processes.append(subprocess.Popen([sys.executable, "node.py", str(peer3), "--share-dir", str(empty), "--output-dir", str(out2)], cwd=ROOT, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for_api(peer3, env)
            wait_manifest(peer3, env)
            result = api(peer3, "download", env)
            if not result.get("ok"):
                raise RuntimeError(f"Could not start second download: {result}")
            wait_download(peer3, env)
            if (out2 / "output.bin").read_bytes() != expected:
                raise AssertionError("Second leecher output does not match original pieces")
            print("PASS: full seed + partial seed + empty leecher -> second leecher; manifest discovery, SHA-256 checks, reconstruction, and re-seeding work.")
        finally:
            # Prefer API shutdown so nodes exercise their graceful tracker-unregister path.
            for peer_port in (peer3, peer2, peer4, peer1):
                try:
                    api(peer_port, "shutdown", env)
                except Exception:
                    pass
            for process in reversed(processes):
                if process.poll() is None:
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)


if __name__ == "__main__":
    main()
