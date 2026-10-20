# Author: Swayam Shakya
"""Python-only torrent metadata helpers; no external .json manifest is required."""
from dataclasses import dataclass, field
from typing import Dict, List, Set

@dataclass
class TorrentMetadata:
    torrent_id: str
    filename: str
    pieces: List[str]
    peers: Set[str] = field(default_factory=set)
    piece_owners: Dict[str, Set[str]] = field(default_factory=dict)
    downloaded: Set[str] = field(default_factory=set)


def create_torrent(torrent_id, filename, pieces):
    pieces = list(pieces)
    return TorrentMetadata(
        torrent_id=torrent_id,
        filename=filename,
        pieces=pieces,
        piece_owners={piece: set() for piece in pieces},
    )


def get_torrent(torrents, torrent_id):
    return torrents.get(torrent_id)


def add_peer(metadata, peer):
    metadata.peers.add(peer)


def add_piece_owner(metadata, piece, peer):
    if piece in metadata.piece_owners:
        metadata.piece_owners[piece].add(peer)


def mark_downloaded(metadata, piece):
    if piece in metadata.piece_owners:
        metadata.downloaded.add(piece)
