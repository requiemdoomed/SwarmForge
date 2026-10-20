# Author: Swayam Shakya
"""Focused tests for rejecting corrupted data received from a peer."""
import hashlib
import socket
import threading
import unittest

from node import PeerNode
from utils import recv_json_line, send_json_line


class PieceTransferTests(unittest.TestCase):
    def test_corrupted_payload_is_rejected_even_when_header_hash_matches_request(self):
        expected_payload = b"trusted piece contents"
        corrupted_payload = b"Trusted piece contents"  # same length, different digest
        requested_hash = hashlib.sha256(expected_payload).hexdigest()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def fake_peer():
            conn, _ = listener.accept()
            with conn:
                recv_json_line(conn)
                send_json_line(conn, {"ok": True, "size": len(corrupted_payload), "sha256": requested_hash})
                conn.sendall(corrupted_payload)
            listener.close()

        server_thread = threading.Thread(target=fake_peer, daemon=True)
        server_thread.start()
        node = PeerNode.__new__(PeerNode)
        node.piece_sizes = {requested_hash: len(expected_payload)}
        node.lock = threading.Lock()
        node.downloaded_bytes = 0

        try:
            with self.assertRaisesRegex(ValueError, "SHA-256 verification failed"):
                node._download_from_peer(requested_hash, ("127.0.0.1", port))
        finally:
            server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
