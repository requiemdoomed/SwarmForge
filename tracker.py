# Author: Swayam Shakya
"""Multithreaded TCP tracker. Tracks peers by torrent and SHA-256 piece."""
import socket
import threading
import time
import re
from pathlib import Path
from config import HOST, TRACKER_PORT, SOCKET_TIMEOUT
from utils import recv_json_line, send_json_line
from logger import log

_lock = threading.RLock()
# torrent_id -> piece_id -> {(host, port, last_seen)}
_registry = {}
# torrent_id -> ordered piece manifest (first complete seed establishes it)
_manifests = {}
PEER_TTL_SECONDS = 180
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PIECE_ID_RE = re.compile(r"^(?:[0-9a-f]{64}|p[0-9]{5,}-[0-9a-f]{64})$")


def _valid_endpoint(host, port):
    return isinstance(host, str) and bool(host.strip()) and len(host) <= 255 and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535


def _prune_stale(now=None):
    """Remove expired endpoints and empty piece buckets from every swarm."""
    now = time.time() if now is None else now
    for torrent_id, swarm in list(_registry.items()):
        for piece, owners in list(swarm.items()):
            for endpoint, seen in list(owners.items()):
                if now - seen > PEER_TTL_SECONDS:
                    del owners[endpoint]
            if not owners:
                del swarm[piece]
        if not swarm and torrent_id not in _manifests:
            _registry.pop(torrent_id, None)


def _handle(conn, address):
    with conn:
        conn.settimeout(SOCKET_TIMEOUT)
        try:
            request = recv_json_line(conn)
            action = request.get("action")
            torrent_id = str(request.get("torrent_id", ""))
            if not torrent_id:
                send_json_line(conn, {"ok": False, "error": "torrent_id is required"})
                return
            with _lock:
                swarm = _registry.setdefault(torrent_id, {})
                if action == "register":
                    host = str(request.get("host", address[0]))
                    raw_port = request["port"]
                    if isinstance(raw_port, bool):
                        raise ValueError("Invalid peer port")
                    port = int(raw_port)
                    pieces = request.get("pieces", [])
                    if not _valid_endpoint(host, port):
                        raise ValueError("Invalid peer endpoint")
                    if not isinstance(pieces, list) or any(not isinstance(piece, str) or not _PIECE_ID_RE.fullmatch(piece) for piece in pieces):
                        raise ValueError("pieces must be a list of valid piece IDs")
                    if len(set(pieces)) != len(pieces):
                        raise ValueError("pieces contains duplicate piece IDs")
                    # A complete seed may publish the canonical ordered manifest. Validate
                    # it fully before mutating registry state, so bad requests are atomic.
                    manifest = request.get("manifest")
                    proposed_manifest = None
                    if manifest is not None:
                        if not isinstance(manifest, list) or not manifest:
                            raise ValueError("manifest must be a non-empty list")
                        if not all(isinstance(item, dict) and
                                   isinstance(item.get("name"), str) and item["name"] and
                                   Path(item["name"]).name == item["name"] and "/" not in item["name"] and "\\" not in item["name"] and item["name"] not in {".", ".."} and
                                   isinstance(item.get("sha256"), str) and _SHA256_RE.fullmatch(item["sha256"]) and
                                   isinstance(item.get("size"), int) and not isinstance(item.get("size"), bool) and item["size"] >= 0 and
                                   (item.get("id", item.get("sha256")) == item.get("sha256") or
                                    isinstance(item.get("id"), str) and _PIECE_ID_RE.fullmatch(item["id"]) and item["id"].endswith("-" + item["sha256"]))
                                   for item in manifest):
                            raise ValueError("Invalid manifest entries")
                        ids = [item.get("id", item["sha256"]) for item in manifest]
                        names = [item["name"] for item in manifest]
                        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
                            raise ValueError("Manifest piece IDs and filenames must be unique")
                        if ids != pieces:
                            raise ValueError("Manifest piece IDs must match the registering peer's ordered piece list")
                        proposed_manifest = manifest
                    existing = _manifests.get(torrent_id)
                    if existing is not None:
                        allowed_ids = {item.get("id", item["sha256"]) for item in existing}
                        if not set(pieces).issubset(allowed_ids):
                            raise ValueError("Peer advertised pieces outside the established manifest")
                        if proposed_manifest is not None and existing != proposed_manifest:
                            raise ValueError("Torrent manifest conflicts with the established manifest")
                    elif proposed_manifest is not None:
                        _manifests[torrent_id] = proposed_manifest
                    for piece in pieces:
                        swarm.setdefault(piece, {})[(host, port)] = time.time()
                    send_json_line(conn, {"ok": True, "registered_pieces": len(pieces),
                                          "manifest_available": torrent_id in _manifests})
                    log(f"Registered peer {host}:{port} for {len(pieces)} piece(s)")
                elif action == "heartbeat":
                    host = request.get("host", address[0])
                    port = request.get("port")
                    pieces = request.get("pieces", [])
                    if not _valid_endpoint(host, port):
                        raise ValueError("Invalid peer endpoint")
                    if not isinstance(pieces, list) or any(not isinstance(piece, str) or not _PIECE_ID_RE.fullmatch(piece) for piece in pieces):
                        raise ValueError("pieces must be a list of valid piece IDs")
                    manifest = _manifests.get(torrent_id)
                    if manifest is not None and not set(pieces).issubset({item.get("id", item["sha256"]) for item in manifest}):
                        raise ValueError("Heartbeat contains pieces outside the manifest")
                    now = time.time()
                    endpoint = (host, port)
                    for piece in pieces:
                        swarm.setdefault(piece, {})[endpoint] = now
                    send_json_line(conn, {"ok": True, "refreshed_pieces": len(pieces)})
                elif action == "unregister":
                    host = request.get("host", address[0])
                    port = request.get("port")
                    if not _valid_endpoint(host, port):
                        raise ValueError("Invalid peer endpoint")
                    endpoint = (host, port)
                    removed = 0
                    for owners in swarm.values():
                        if endpoint in owners:
                            del owners[endpoint]
                            removed += 1
                    send_json_line(conn, {"ok": True, "removed_piece_owners": removed})
                elif action == "health":
                    _prune_stale()
                    send_json_line(conn, {"ok": True, "service": "SwarmForge tracker", "timestamp": time.time(),
                                          "torrents": len(_manifests), "registered_piece_ids": sum(len(s) for s in _registry.values())})
                elif action == "get_manifest":
                    manifest = _manifests.get(torrent_id)
                    if manifest is None:
                        send_json_line(conn, {"ok": False, "error": "Torrent manifest is not available yet"})
                    else:
                        send_json_line(conn, {"ok": True, "manifest": manifest})
                elif action == "get_peers":
                    piece = str(request.get("piece", ""))
                    now = time.time()
                    peers = []
                    for (host, port), seen in list(swarm.get(piece, {}).items()):
                        if now - seen > PEER_TTL_SECONDS:
                            del swarm[piece][(host, port)]
                        else:
                            peers.append({"host": host, "port": port})
                    send_json_line(conn, {"ok": True, "peers": peers})
                elif action == "announce":
                    host = str(request.get("host", address[0]))
                    raw_port = request["port"]
                    if isinstance(raw_port, bool):
                        raise ValueError("Invalid peer port")
                    port = int(raw_port)
                    piece = str(request["piece"])
                    if not _PIECE_ID_RE.fullmatch(piece):
                        raise ValueError("Invalid piece ID")
                    manifest = _manifests.get(torrent_id)
                    if manifest is None or piece not in {item.get("id", item["sha256"]) for item in manifest}:
                        raise ValueError("Cannot announce a piece outside the established manifest")
                    if not _valid_endpoint(host, port):
                        raise ValueError("Invalid peer endpoint")
                    swarm.setdefault(piece, {})[(host, port)] = time.time()
                    send_json_line(conn, {"ok": True})
                elif action == "stats":
                    _prune_stale()
                    peers = set()
                    pieces = 0
                    now = time.time()
                    for owners in swarm.values():
                        stale = [endpoint for endpoint, seen in owners.items() if now - seen > PEER_TTL_SECONDS]
                        for endpoint in stale:
                            del owners[endpoint]
                        if owners:
                            pieces += 1
                            peers.update(owners)
                    send_json_line(conn, {"ok": True, "torrent_id": torrent_id,
                                          "unique_peers": len(peers), "tracked_pieces": pieces})
                else:
                    send_json_line(conn, {"ok": False, "error": f"Unknown action: {action}"})
        except Exception as exc:
            try:
                send_json_line(conn, {"ok": False, "error": str(exc)})
            except Exception:
                pass
            log(f"Request from {address} failed: {exc}", "ERROR")


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, TRACKER_PORT))
    server.listen(64)
    log(f"Tracker listening on {HOST}:{TRACKER_PORT}")
    try:
        while True:
            conn, address = server.accept()
            threading.Thread(target=_handle, args=(conn, address), daemon=True).start()
    except KeyboardInterrupt:
        log("Tracker shutting down")
    finally:
        server.close()

if __name__ == "__main__":
    main()
