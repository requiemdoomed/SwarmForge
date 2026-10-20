# Author: Swayam Shakya
"""Tracker protocol tests run in-process without binding network ports."""
import hashlib
import socket
import threading
import unittest

import tracker
from utils import recv_json_line, send_json_line


class TrackerProtocolTests(unittest.TestCase):
    def setUp(self):
        with tracker._lock:
            tracker._registry.clear()
            tracker._manifests.clear()

    def request(self, payload):
        client, server = socket.socketpair()
        try:
            worker = threading.Thread(target=tracker._handle, args=(server, ("127.0.0.1", 40000)), daemon=True)
            worker.start()
            send_json_line(client, payload)
            response = recv_json_line(client)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "tracker request handler did not finish")
            return response
        finally:
            client.close()
            server.close()

    def test_health_endpoint_reports_tracker_service(self):
        response = self.request({"action": "health", "torrent_id": "health-check"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["service"], "SwarmForge tracker")
        self.assertIn("timestamp", response)

    def test_register_manifest_then_get_manifest(self):
        payload = b"piece contents"
        digest = hashlib.sha256(payload).hexdigest()
        manifest = [{"name": "piece.bin", "sha256": digest, "size": len(payload)}]
        registered = self.request({"action": "register", "torrent_id": "test-swarm", "host": "127.0.0.1",
                                   "port": 5000, "pieces": [digest], "manifest": manifest})
        self.assertTrue(registered["ok"])
        fetched = self.request({"action": "get_manifest", "torrent_id": "test-swarm"})
        self.assertEqual(fetched["manifest"], manifest)

    def test_heartbeat_refreshes_and_unregister_removes_peer(self):
        digest = "a" * 64
        manifest = [{"name": "piece.bin", "sha256": digest, "size": 1}]
        self.request({"action": "register", "torrent_id": "test-swarm", "host": "127.0.0.1",
                      "port": 5000, "pieces": [digest], "manifest": manifest})
        heartbeat = self.request({"action": "heartbeat", "torrent_id": "test-swarm", "host": "127.0.0.1",
                                  "port": 5000, "pieces": [digest]})
        self.assertTrue(heartbeat["ok"])
        peers = self.request({"action": "get_peers", "torrent_id": "test-swarm", "piece": digest})
        self.assertEqual(peers["peers"], [{"host": "127.0.0.1", "port": 5000}])
        removed = self.request({"action": "unregister", "torrent_id": "test-swarm", "host": "127.0.0.1", "port": 5000})
        self.assertTrue(removed["ok"])
        peers_after = self.request({"action": "get_peers", "torrent_id": "test-swarm", "piece": digest})
        self.assertEqual(peers_after["peers"], [])

    def test_duplicate_content_hashes_can_have_distinct_piece_ids(self):
        digest = hashlib.sha256(b"repeated chunk").hexdigest()
        ids = [f"p00000-{digest}", f"p00001-{digest}"]
        manifest = [
            {"id": ids[0], "name": "piece_0.bin", "sha256": digest, "size": len(b"repeated chunk")},
            {"id": ids[1], "name": "piece_1.bin", "sha256": digest, "size": len(b"repeated chunk")},
        ]
        response = self.request({"action": "register", "torrent_id": "duplicate-swarm", "host": "127.0.0.1",
                                 "port": 5000, "pieces": ids, "manifest": manifest})
        self.assertTrue(response["ok"], response)
        fetched = self.request({"action": "get_manifest", "torrent_id": "duplicate-swarm"})
        self.assertEqual([item["id"] for item in fetched["manifest"]], ids)
        self.assertEqual([item["sha256"] for item in fetched["manifest"]], [digest, digest])

    def test_expired_peer_lease_is_not_returned(self):
        digest = "c" * 64
        manifest = [{"name": "piece.bin", "sha256": digest, "size": 1}]
        self.request({"action": "register", "torrent_id": "lease-swarm", "host": "127.0.0.1",
                      "port": 5000, "pieces": [digest], "manifest": manifest})
        with tracker._lock:
            tracker._registry["lease-swarm"][digest][("127.0.0.1", 5000)] = 0
        peers = self.request({"action": "get_peers", "torrent_id": "lease-swarm", "piece": digest})
        self.assertEqual(peers["peers"], [])

    def test_invalid_manifest_is_rejected_without_publishing(self):
        response = self.request({"action": "register", "torrent_id": "bad-swarm", "host": "127.0.0.1",
                                 "port": 5000, "pieces": ["not-a-hash"],
                                 "manifest": [{"name": "../escape", "sha256": "not-a-hash", "size": 1}]})
        self.assertFalse(response["ok"])
        with tracker._lock:
            self.assertNotIn("bad-swarm", tracker._manifests)


if __name__ == "__main__":
    unittest.main()
