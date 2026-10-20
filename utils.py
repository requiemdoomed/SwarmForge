# Author: Swayam Shakya
"""Small networking and hashing helpers."""
import hashlib
import json
import socket


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def send_json_line(sock: socket.socket, payload: dict) -> None:
    sock.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))


def recv_json_line(sock: socket.socket, max_bytes=1024 * 1024) -> dict:
    data = bytearray()
    while len(data) < max_bytes:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("Connection closed before a complete message arrived")
        if byte == b"\n":
            break
        data.extend(byte)
    else:
        raise ValueError("Protocol message exceeded maximum size")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object message")
    return value
