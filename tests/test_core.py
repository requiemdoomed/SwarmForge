# Author: Swayam Shakya
import hashlib
import socket
import unittest

from torrent import Torrent
from torrent_data import create_torrent
from utils import recv_json_line, send_json_line, sha256_bytes


class UtilityTests(unittest.TestCase):
    def test_sha256_matches_standard_library(self):
        payload = b"SwarmForge test data"
        self.assertEqual(sha256_bytes(payload), hashlib.sha256(payload).hexdigest())

    def test_sha256_detects_changed_piece_data(self):
        original = b"verified piece"
        altered = b"verified Piece"
        self.assertNotEqual(sha256_bytes(original), sha256_bytes(altered))

    def test_json_line_protocol_round_trip(self):
        left, right = socket.socketpair()
        try:
            expected = {"action": "hello", "message": "piece transfer"}
            send_json_line(left, expected)
            self.assertEqual(recv_json_line(right), expected)
        finally:
            left.close()
            right.close()

    def test_torrent_tracks_downloaded_pieces(self):
        pieces = ["a" * 64, "b" * 64]
        torrent = Torrent("unit-test", "result.bin", pieces)
        self.assertEqual(torrent.next_piece(), pieces[0])
        torrent.mark_downloaded(pieces[0])
        self.assertEqual(torrent.missing_pieces(), [pieces[1]])
        self.assertEqual(torrent.progress(), {"downloaded": 1, "total": 2, "progress": 50.0})

    def test_empty_manifest_metadata(self):
        metadata = create_torrent("unit-test", "result.bin", [])
        self.assertEqual(metadata.pieces, [])
        self.assertEqual(metadata.piece_owners, {})

    def test_rarest_first_prefers_piece_with_fewer_owners(self):
        rare, common = "a" * 64, "b" * 64
        torrent = Torrent("unit-test", "result.bin", [common, rare])
        torrent.register_piece_owner(common, "peer-1")
        torrent.register_piece_owner(common, "peer-2")
        torrent.register_piece_owner(rare, "peer-3")
        self.assertEqual(torrent.next_piece(), rare)

    def test_piece_availability_counts_distinct_owners(self):
        piece = "a" * 64
        torrent = Torrent("unit-test", "result.bin", [piece])
        torrent.register_piece_owner(piece, "peer-1")
        torrent.register_piece_owner(piece, "peer-1")
        self.assertEqual(torrent.availability(), {piece: 1})


if __name__ == "__main__":
    unittest.main()
