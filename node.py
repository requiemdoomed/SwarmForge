# Author: Swayam Shakya
"""A peer node that serves verified pieces, downloads from peers, and exposes a local GUI API."""
import argparse
import os
import socket
import threading
import time
from pathlib import Path

from config import (HOST, TRACKER_HOST, TRACKER_PORT, API_BASE_PORT, CHUNK_SIZE,
                    SOCKET_TIMEOUT, MAX_RETRIES, TORRENT_ID, OUTPUT_FILENAME, PIECE_FILES)
from logger import log, get_logs
from torrent import Torrent
from utils import sha256_file, sha256_bytes, send_json_line, recv_json_line

class PeerNode:
    def __init__(self, port, share_dir=".", output_dir="."):
        self.host = HOST
        self.port = int(port)
        self.api_port = API_BASE_PORT + self.port
        if self.api_port > 65535:
            raise ValueError(f"Peer port {self.port} makes API port {self.api_port} invalid")
        self.share_dir = Path(share_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Piece identity is position + digest, not digest alone. Two positions may
        # legitimately contain identical bytes, so using SHA-256 as the sole key
        # would collapse repeated chunks and corrupt reconstruction.
        self.files_by_hash = {}  # piece_id -> verified path (historical name retained)
        self.piece_sizes = {}
        self.piece_digests = {}
        for index, name in enumerate(PIECE_FILES):
            path = self.share_dir / name
            if path.is_file():
                digest = sha256_file(str(path))
                piece_id = f"p{index:05d}-{digest}"
                self.files_by_hash[piece_id] = path
                self.piece_sizes[piece_id] = path.stat().st_size
                self.piece_digests[piece_id] = digest
        # Local pieces are those currently available to upload. The canonical ordered
        # piece list is obtained from the tracker's manifest, so a pure leecher can join.
        self.local_pieces = list(self.files_by_hash.keys())
        self.pieces = list(self.local_pieces)
        self.piece_names = {digest: path.name for digest, path in self.files_by_hash.items()}
        self.torrent = Torrent(TORRENT_ID, OUTPUT_FILENAME, self.pieces)
        self.lock = threading.RLock()
        self.download_lock = threading.Lock()
        self.download_running = False
        self.manifest_loaded = False
        self.stop_event = threading.Event()
        self.heartbeat_interval = max(10.0, min(45.0, SOCKET_TIMEOUT * 6))
        self.downloaded_bytes = 0
        self.uploaded_bytes = 0
        self.uploaded_pieces = 0
        self.downloaded_piece_count = 0
        self.download_started = None
        self.current_piece = None
        self.last_error = ""
        self.downloaded_paths = {}  # piece ID -> verified local path
        self.peer_history = set()
        self.server_socket = None
        self.api_socket = None
        if not self.local_pieces:
            log(f"No seed pieces found in {self.share_dir}; this node will need a tracker manifest and remote peers", "INFO")

    def _tracker_request(self, payload):
        with socket.create_connection((TRACKER_HOST, TRACKER_PORT), timeout=SOCKET_TIMEOUT) as sock:
            sock.settimeout(SOCKET_TIMEOUT)
            send_json_line(sock, payload)
            response = recv_json_line(sock)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Tracker request failed"))
        return response

    def register(self):
        manifest = None
        # Publish a canonical manifest only when every configured seed piece exists.
        # This prevents a partial peer from accidentally defining an incomplete torrent.
        if len(self.local_pieces) == len(PIECE_FILES):
            manifest = []
            for name in PIECE_FILES:
                path = self.share_dir / name
                digest = sha256_file(str(path))
                manifest.append({"id": f"p{len(manifest):05d}-{digest}", "name": name,
                                 "sha256": digest, "size": path.stat().st_size})
        payload = {"action": "register", "torrent_id": TORRENT_ID,
                   "host": self.host, "port": self.port, "pieces": self.local_pieces}
        if manifest is not None:
            payload["manifest"] = manifest
        response = self._tracker_request(payload)
        for piece in self.local_pieces:
            self.torrent.register_piece_owner(piece, f"{self.host}:{self.port}")
        log(f"Registered {response.get('registered_pieces', 0)} local piece(s) with tracker")
        if not self.local_pieces:
            log("Joined as a leecher with no seed pieces", "INFO")

    def _refresh_manifest(self):
        response = self._tracker_request({"action": "get_manifest", "torrent_id": TORRENT_ID})
        manifest = response.get("manifest", [])
        if not manifest:
            raise RuntimeError("Tracker returned an empty manifest")
        # Validate the tracker response defensively before trusting it for reconstruction.
        for item in manifest:
            if not isinstance(item, dict):
                raise RuntimeError("Manifest contains a malformed entry")
            name, digest, size = item.get("name"), item.get("sha256"), item.get("size")
            piece_id = item.get("id", digest)  # legacy manifests used the digest as the piece ID
            if not isinstance(name, str) or not name or (Path(name).name != name or "/" in name or "\\" in name or name in {".", ".."}):
                raise RuntimeError("Manifest contains an unsafe piece filename")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise RuntimeError("Manifest contains an invalid SHA-256 digest")
            if not isinstance(piece_id, str) or not (piece_id == digest or piece_id.endswith("-" + digest) and piece_id.startswith("p") and piece_id[1:piece_id.index("-")].isdigit()):
                raise RuntimeError("Manifest contains an invalid piece ID")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise RuntimeError("Manifest contains an invalid piece size")
        names = [item["name"] for item in manifest]
        ids = [item.get("id", item["sha256"]) for item in manifest]
        if len(set(names)) != len(names):
            raise RuntimeError("Manifest contains duplicate piece filenames")
        if len(set(ids)) != len(ids):
            raise RuntimeError("Manifest contains duplicate piece IDs")
        self.pieces = ids
        self.manifest_loaded = True
        self.piece_names = {item.get("id", item["sha256"]): item["name"] for item in manifest}
        self.piece_sizes.update({item.get("id", item["sha256"]): item["size"] for item in manifest})
        self.piece_digests.update({item.get("id", item["sha256"]): item["sha256"] for item in manifest})
        self.torrent = Torrent(TORRENT_ID, OUTPUT_FILENAME, self.pieces)
        # Treat local pieces as complete only when their digest and size match the
        # canonical manifest. This fixes progress for seeds and lets partial seeds
        # participate in a complete torrent rather than mistaking their local list
        # for the entire torrent.
        for piece in self.pieces:
            path = self.files_by_hash.get(piece)
            if path is not None and path.is_file():
                expected_size = self.piece_sizes[piece]
                if path.stat().st_size == expected_size and sha256_file(str(path)) == self.piece_digests[piece]:
                    self.downloaded_paths[piece] = path
                    self.torrent.mark_downloaded(piece)
                    self.torrent.register_piece_owner(piece, f"{self.host}:{self.port}")
                else:
                    self.files_by_hash.pop(piece, None)
                    self.downloaded_paths.pop(piece, None)
                    log(f"Ignored local piece {piece[:12]}… because it did not match the manifest", "WARNING")
        log(f"Loaded torrent manifest: {len(self.pieces)} ordered piece(s); {len(self.downloaded_paths)} verified locally")

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(64)
        self.server_socket.settimeout(1.0)
        threading.Thread(target=self._peer_accept_loop, daemon=True).start()
        self.api_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.api_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.api_socket.bind((self.host, self.api_port))
        self.api_socket.listen(32)
        self.api_socket.settimeout(1.0)
        threading.Thread(target=self._api_accept_loop, daemon=True).start()
        try:
            self.register()
            try:
                self._refresh_manifest()
            except Exception as exc:
                log(f"Manifest not available yet: {exc}", "WARNING")
        except Exception as exc:
            log(f"Could not register with tracker: {exc}", "ERROR")
        threading.Thread(target=self._heartbeat_loop, daemon=True, name=f"heartbeat-{self.port}").start()
        log(f"Node ready at {self.host}:{self.port}; GUI API at {self.host}:{self.api_port}")

    def _heartbeat_loop(self):
        """Refresh tracker leases so healthy peers do not expire from an idle swarm."""
        while not self.stop_event.wait(self.heartbeat_interval):
            try:
                with self.lock:
                    pieces = list(self.files_by_hash.keys())
                self._tracker_request({"action": "heartbeat", "torrent_id": TORRENT_ID,
                                       "host": self.host, "port": self.port, "pieces": pieces})
            except Exception as exc:
                log(f"Tracker heartbeat failed: {exc}", "WARNING")
                # A missed heartbeat is recoverable: try a fresh registration on the next cycle.
                try:
                    self.register()
                except Exception as register_exc:
                    log(f"Tracker re-registration failed: {register_exc}", "WARNING")

    def _peer_accept_loop(self):
        while not self.stop_event.is_set():
            try:
                conn, address = self.server_socket.accept()
                threading.Thread(target=self._handle_peer, args=(conn, address), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_peer(self, conn, address):
        with conn:
            conn.settimeout(SOCKET_TIMEOUT)
            try:
                request = recv_json_line(conn)
                if request.get("action") != "get_piece" or request.get("torrent_id") != TORRENT_ID:
                    send_json_line(conn, {"ok": False, "error": "Unsupported request"})
                    return
                piece = request.get("piece", "")
                path = self.files_by_hash.get(piece)
                if path is None or not path.is_file():
                    send_json_line(conn, {"ok": False, "error": "Piece not available"})
                    return
                size = path.stat().st_size
                send_json_line(conn, {"ok": True, "size": size, "piece_id": piece,
                                      "sha256": self.piece_digests.get(piece, piece)})
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
                        conn.sendall(block)
                        with self.lock:
                            self.uploaded_bytes += len(block)
                with self.lock:
                    self.uploaded_pieces += 1
                log(f"Uploaded piece {piece[:12]}… to {address[0]}:{address[1]}")
            except Exception as exc:
                log(f"Peer request error from {address}: {exc}", "WARNING")

    def _api_accept_loop(self):
        while not self.stop_event.is_set():
            try:
                conn, address = self.api_socket.accept()
                threading.Thread(target=self._handle_api, args=(conn, address), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_api(self, conn, address):
        with conn:
            conn.settimeout(SOCKET_TIMEOUT)
            try:
                request = recv_json_line(conn)
                action = request.get("action")
                shutdown_after_response = False
                if action == "stats":
                    response = self.stats()
                elif action == "logs":
                    response = {"ok": True, "logs": get_logs(int(request.get("limit", 100)))}
                elif action == "download":
                    started = self.start_download()
                    response = {"ok": started, "message": "Download started" if started else "A download is already running"}
                elif action == "register":
                    self.register()
                    try:
                        self._refresh_manifest()
                    except Exception as exc:
                        log(f"Manifest refresh deferred: {exc}", "WARNING")
                    response = {"ok": True, "message": "Registration refreshed"}
                elif action == "shutdown":
                    response = {"ok": True, "message": "Shutdown requested"}
                    shutdown_after_response = True
                else:
                    response = {"ok": False, "error": f"Unknown API action: {action}"}
                send_json_line(conn, response)
                if shutdown_after_response:
                    self.stop_event.set()
            except Exception as exc:
                try:
                    send_json_line(conn, {"ok": False, "error": str(exc)})
                except Exception:
                    pass

    def start_download(self):
        with self.lock:
            if self.download_running:
                return False
            self.download_running = True
            self.download_started = time.time()
            self.downloaded_bytes = 0
            self.last_error = ""
        threading.Thread(target=self.download, daemon=True).start()
        return True

    def _get_peers(self, piece):
        response = self._tracker_request({"action": "get_peers", "torrent_id": TORRENT_ID, "piece": piece})
        peers = [(p["host"], int(p["port"])) for p in response.get("peers", [])]
        # Avoid requesting a piece from this node itself.
        return [peer for peer in peers if not (peer[0] == self.host and peer[1] == self.port)]

    def _download_from_peer(self, piece, peer):
        host, port = peer
        with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT) as sock:
            sock.settimeout(SOCKET_TIMEOUT)
            send_json_line(sock, {"action": "get_piece", "torrent_id": TORRENT_ID, "piece": piece})
            header = recv_json_line(sock)
            if not header.get("ok"):
                raise RuntimeError(header.get("error", "Peer rejected request"))
            expected_digest = getattr(self, "piece_digests", {}).get(piece, piece)
            if header.get("piece_id", piece) != piece:
                raise ValueError("Peer response piece ID does not match the requested piece")
            if header.get("sha256") != expected_digest:
                raise ValueError("Peer response digest does not match the manifest")
            size = int(header["size"])
            expected_size = self.piece_sizes.get(piece)
            if size < 0 or size > 256 * 1024 * 1024:
                raise ValueError("Peer reported an invalid or oversized piece")
            if expected_size is None or size != expected_size:
                raise ValueError(f"Peer reported {size} bytes; manifest expects {expected_size}")
            data = bytearray()
            while len(data) < size:
                block = sock.recv(min(CHUNK_SIZE, size - len(data)))
                if not block:
                    raise ConnectionError("Peer disconnected before piece completed")
                data.extend(block)
                with self.lock:
                    self.downloaded_bytes += len(block)
            if sha256_bytes(data) != expected_digest:
                raise ValueError("SHA-256 verification failed")
            return bytes(data)

    def download(self):
        log("Download started (rarest-first among known piece owners)")
        try:
            # Always refresh: a peer with one local piece must still learn the full
            # canonical manifest. Otherwise a partial seed could incorrectly believe
            # its local subset is the whole torrent.
            self._refresh_manifest()
            missing = [piece for piece in self.pieces if piece not in self.downloaded_paths]
            while missing and not self.stop_event.is_set():
                piece = self._choose_piece(missing)
                with self.lock:
                    self.current_piece = piece
                try:
                    peers = self._get_peers(piece)
                except Exception as exc:
                    log(f"Tracker lookup failed: {exc}", "WARNING")
                    peers = []
                success = False
                for attempt in range(MAX_RETRIES):
                    for peer in peers:
                        try:
                            data = self._download_from_peer(piece, peer)
                            path = self.output_dir / f"{piece}.part"
                            # The bytes have already passed SHA-256 and manifest-size checks.
                            # Write to a temporary path and atomically publish the verified piece.
                            temporary_piece = path.with_name(path.name + ".tmp")
                            with temporary_piece.open("wb") as piece_file:
                                piece_file.write(data)
                                piece_file.flush()
                                os.fsync(piece_file.fileno())
                            temporary_piece.replace(path)
                            # Completed pieces become uploadable: this turns a downloader into
                            # a useful swarm participant rather than a dead-end client.
                            with self.lock:
                                self.downloaded_paths[piece] = path
                                self.files_by_hash[piece] = path
                                self.piece_sizes[piece] = len(data)
                                self.downloaded_piece_count += 1
                            try:
                                self._tracker_request({"action": "announce", "torrent_id": TORRENT_ID,
                                                       "host": self.host, "port": self.port, "piece": piece})
                            except Exception as announce_exc:
                                log(f"Piece verified locally but tracker announce failed: {announce_exc}", "WARNING")
                            self.torrent.mark_downloaded(piece)
                            self.torrent.add_peer(f"{peer[0]}:{peer[1]}")
                            self.torrent.register_piece_owner(piece, f"{peer[0]}:{peer[1]}")
                            with self.lock:
                                self.peer_history.add(f"{peer[0]}:{peer[1]}")
                            log(f"Downloaded and verified piece {piece[:12]}… from {peer[0]}:{peer[1]}")
                            success = True
                            break
                        except Exception as exc:
                            log(f"Peer {peer[0]}:{peer[1]} failed for piece {piece[:12]}…: {exc}", "WARNING")
                    if success:
                        break
                    time.sleep(0.25 * (attempt + 1))
                    try:
                        peers = self._get_peers(piece)
                    except Exception:
                        peers = []
                if not success:
                    raise RuntimeError(f"No available peer could provide piece {piece[:12]}…")
                missing = [p for p in self.pieces if p not in self.downloaded_paths]
            if not missing:
                output = self.output_dir / OUTPUT_FILENAME
                temporary_output = output.with_name(output.name + ".tmp")
                with temporary_output.open("wb") as out:
                    for piece in self.pieces:
                        path = self.downloaded_paths.get(piece)
                        if path is None or not path.is_file():
                            raise FileNotFoundError(f"Missing downloaded piece {piece}")
                        out.write(path.read_bytes())
                    out.flush()
                    os.fsync(out.fileno())
                temporary_output.replace(output)
                log(f"File reconstructed successfully: {output}")
        except Exception as exc:
            self.last_error = str(exc)
            log(f"Download failed: {exc}", "ERROR")
        finally:
            with self.lock:
                self.download_running = False
                self.current_piece = None

    def _choose_piece(self, missing):
        # Ask tracker for known availability and select the rarest piece among currently missing pieces.
        counts = {}
        for piece in missing:
            try:
                counts[piece] = len(self._get_peers(piece))
            except Exception:
                counts[piece] = 0
        return min(missing, key=lambda p: counts.get(p, 0))

    def stats(self):
        with self.lock:
            elapsed = max(0.001, time.time() - self.download_started) if self.download_started else 0.0
            total = len(self.pieces)
            done = len(self.downloaded_paths)
            progress = (100.0 * done / total) if total else 0.0
            speed = self.downloaded_bytes / elapsed / 1024 if elapsed else 0.0
            return {"ok": True, "author": "Swayam Shakya", "host": self.host, "port": self.port, "api_port": self.api_port,
                    "torrent_id": TORRENT_ID, "manifest_loaded": self.manifest_loaded,
                    "total_pieces": total, "downloaded_pieces": done,
                    "progress": round(progress, 2), "piece_status": [piece in self.downloaded_paths for piece in self.pieces],
                    "pieces": [piece[:12] for piece in self.pieces], "speed_kbps": round(speed, 2),
                    "downloaded_bytes": self.downloaded_bytes, "uploaded_bytes": self.uploaded_bytes,
                    "uploaded_pieces": self.uploaded_pieces, "heartbeat_interval_seconds": self.heartbeat_interval,
                    "download_running": self.download_running, "current_piece": self.current_piece[:12] if self.current_piece else None,
                    "peers_used": sorted(self.peer_history), "last_error": self.last_error,
                    "share_dir": str(self.share_dir), "output_file": str(self.output_dir / OUTPUT_FILENAME)}

    def run_forever(self, auto_download=False):
        self.start()
        if auto_download:
            # Give other locally-started peers a moment to register before discovery.
            threading.Timer(1.0, self.start_download).start()
        try:
            while not self.stop_event.wait(0.5):
                pass
        except KeyboardInterrupt:
            log("Node interrupted")
        finally:
            try:
                self._tracker_request({"action": "unregister", "torrent_id": TORRENT_ID,
                                       "host": self.host, "port": self.port})
                log(f"Unregistered peer {self.host}:{self.port} from tracker")
            except Exception as exc:
                log(f"Could not unregister from tracker during shutdown: {exc}", "WARNING")
            for sock in (self.server_socket, self.api_socket):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            log(f"Node on port {self.port} stopped")


def main():
    parser = argparse.ArgumentParser(description="Mini BitTorrent-style peer node")
    parser.add_argument("port", nargs="?", type=int, help="peer TCP port (e.g. 5000)")
    parser.add_argument("--share-dir", default=".", help="directory containing binfile.part1.bin and binfile.part2.bin")
    parser.add_argument("--output-dir", default=".", help="directory for output.bin and downloaded pieces")
    parser.add_argument("--download", action="store_true", help="start a download shortly after node startup")
    args = parser.parse_args()
    port = args.port
    if port is None:
        try:
            port = int(input("Enter node port (e.g. 5000): ").strip())
        except (ValueError, EOFError):
            parser.error("A valid node port is required; use python node.py 5000")
    if not 1 <= port <= 65535:
        parser.error("Port must be between 1 and 65535")
    PeerNode(port, args.share_dir, args.output_dir).run_forever(auto_download=args.download)

if __name__ == "__main__":
    main()
