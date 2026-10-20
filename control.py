# Author: Swayam Shakya
"""Small terminal controller for a running node's local GUI API."""
import argparse
import json
from config import API_BASE_PORT, HOST
from utils import send_json_line, recv_json_line
import socket


def main():
    parser = argparse.ArgumentParser(description="Control a running mini-torrent node")
    parser.add_argument("port", type=int, help="peer port, such as 5000")
    parser.add_argument("action", choices=("stats", "logs", "download", "register", "shutdown"))
    args = parser.parse_args()
    api_port = API_BASE_PORT + args.port
    try:
        with socket.create_connection((HOST, api_port), timeout=3) as sock:
            sock.settimeout(3)
            send_json_line(sock, {"action": args.action, "limit": 100})
            response = recv_json_line(sock)
        print(json.dumps(response, indent=2))
        if not response.get("ok"):
            raise SystemExit(1)
    except OSError as exc:
        raise SystemExit(f"Could not reach node API on port {api_port}: {exc}\nIs node.py running?")

if __name__ == "__main__":
    main()
