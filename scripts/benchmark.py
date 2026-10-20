#!/usr/bin/env python3
# Author: Swayam Shakya
"""Repeatable local throughput/integrity experiment for SwarmForge.

Usage: python3 scripts/benchmark.py --megabytes 8 --piece-kib 256
This is a functional benchmark, not a statistically rigorous performance claim.
"""
import argparse
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port(low=5200, high=12000):
    for port in range(low, high):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found in benchmark range")


def free_peer_and_api_port(api_base, start=5200):
    for peer_port in range(start, 12000):
        sockets = []
        try:
            for port in (peer_port, api_base + peer_port):
                sock = socket.socket()
                sock.bind(("127.0.0.1", port))
                sockets.append(sock)
            return peer_port
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("No free peer/API port pair found")


def api(peer_port, action, env):
    api_port = int(env["SWARMFORGE_API_BASE_PORT"]) + peer_port
    with socket.create_connection(("127.0.0.1", api_port), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall((json.dumps({"action": action}) + "\n").encode())
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = sock.recv(1)
            if not chunk:
                break
            data.extend(chunk)
    return json.loads(data.decode())


def wait_api(peer_port, env, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api(peer_port, "stats", env)
            return
        except (OSError, ValueError):
            time.sleep(0.1)
    raise RuntimeError(f"Node API did not become ready for peer {peer_port}")


def wait_complete(peer_port, env, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = api(peer_port, "stats", env)
        if state.get("last_error"):
            raise RuntimeError(state["last_error"])
        if state.get("progress") == 100.0 and not state.get("download_running"):
            return state
        time.sleep(0.1)
    raise TimeoutError(f"Peer {peer_port} did not finish in {timeout}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--megabytes", type=int, default=4, help="payload size in MiB (default: 4)")
    parser.add_argument("--piece-kib", type=int, default=256, help="piece size in KiB (default: 256)")
    args = parser.parse_args()
    if args.megabytes < 1 or args.piece_kib < 1:
        parser.error("--megabytes and --piece-kib must be positive")

    with tempfile.TemporaryDirectory(prefix="swarmforge-benchmark-") as temp:
        base = Path(temp)
        seed_dir, empty_dir, output_dir = base / "seed", base / "empty", base / "output"
        for folder in (seed_dir, empty_dir, output_dir):
            folder.mkdir()
        total_bytes = args.megabytes * 1024 * 1024
        payload = random.Random(20261020).randbytes(total_bytes)
        source_name = "benchmark_payload.bin"
        (base / source_name).write_bytes(payload)
        piece_size = args.piece_kib * 1024
        names = []
        for index, offset in enumerate(range(0, len(payload), piece_size)):
            name = f"piece_{index:05d}.bin"
            (seed_dir / name).write_bytes(payload[offset:offset + piece_size])
            names.append(name)

        tracker_port = free_port(15000, 25000)
        api_base = 30000
        peer1 = free_peer_and_api_port(api_base, 5200)
        peer2 = free_peer_and_api_port(api_base, peer1 + 1)
        env = os.environ.copy()
        env.update({"SWARMFORGE_TRACKER_PORT": str(tracker_port),
                    "SWARMFORGE_API_BASE_PORT": str(api_base),
                    "SWARMFORGE_TORRENT_ID": f"benchmark-{os.getpid()}-{time.time_ns()}",
                    "SWARMFORGE_PIECE_FILES": ",".join(names),
                    "SWARMFORGE_OUTPUT_FILENAME": source_name,
                    "SWARMFORGE_SOCKET_TIMEOUT": "8"})
        processes = []
        try:
            processes.append(subprocess.Popen([sys.executable, "tracker.py"], cwd=ROOT, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(0.25)
            processes.append(subprocess.Popen([sys.executable, "node.py", str(peer1), "--share-dir", str(seed_dir),
                                              "--output-dir", str(base / "seed-output")], cwd=ROOT, env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_api(peer1, env)
            deadline = time.time() + 8
            while time.time() < deadline and not api(peer1, "stats", env).get("manifest_loaded"):
                time.sleep(0.1)
            processes.append(subprocess.Popen([sys.executable, "node.py", str(peer2), "--share-dir", str(empty_dir),
                                              "--output-dir", str(output_dir)], cwd=ROOT, env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_api(peer2, env)
            deadline = time.time() + 8
            while time.time() < deadline and not api(peer2, "stats", env).get("manifest_loaded"):
                time.sleep(0.1)
            started = time.perf_counter()
            result = api(peer2, "download", env)
            if not result.get("ok"):
                raise RuntimeError(f"Download did not start: {result}")
            state = wait_complete(peer2, env)
            elapsed = time.perf_counter() - started
            result_path = output_dir / source_name
            actual = result_path.read_bytes()
            if actual != payload:
                raise AssertionError("Downloaded output differs from the original payload")
            mib = total_bytes / (1024 * 1024)
            print("SwarmForge benchmark result")
            print("Author: Swayam Shakya")
            print(f"Payload: {mib:.2f} MiB in {len(names)} pieces ({args.piece_kib} KiB target piece size)")
            print(f"Elapsed: {elapsed:.3f} s")
            print(f"Effective throughput: {mib / max(elapsed, 1e-9):.2f} MiB/s")
            print(f"Node-reported download: {state.get('downloaded_bytes', 0)} bytes")
            print(f"SHA-256 integrity: PASS (byte-for-byte comparison)")
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


if __name__ == "__main__":
    main()
