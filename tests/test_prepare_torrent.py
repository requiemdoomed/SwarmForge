# Author: Swayam Shakya
"""Tests for arbitrary-file piece preparation, including repeated/empty content."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_torrent import prepare


class PrepareTorrentTests(unittest.TestCase):
    def test_repeated_pieces_are_kept_as_separate_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.bin"
            source.write_bytes(b"ABCD" * 4)
            _, pieces_dir, names, entries, torrent_id = prepare(source, root / "pieces", 4)
            self.assertEqual(len(names), 4)
            self.assertEqual(len(set(name for name in names)), 4)
            self.assertEqual(len(set(entry[2] for entry in entries)), 1)
            self.assertEqual(b"".join((pieces_dir / name).read_bytes() for name in names), source.read_bytes())
            self.assertTrue(torrent_id)

    def test_empty_file_produces_one_valid_empty_piece(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "empty.bin"
            source.write_bytes(b"")
            _, pieces_dir, names, entries, _ = prepare(source, root / "pieces", 1024)
            self.assertEqual(len(names), 1)
            self.assertEqual(entries[0][1], 0)
            self.assertEqual((pieces_dir / names[0]).read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
