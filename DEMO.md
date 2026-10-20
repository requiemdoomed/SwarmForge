# SwarmForge Demo Checklist

**Author: Swayam Shakya**

## Before presenting

1. Run `python3 -m py_compile *.py scripts/*.py tests/*.py`.
2. Run `python3 -m unittest discover -s tests -v`.
3. Run `python3 scripts/smoke_test.py` and capture its PASS output.
4. Run `python3 scripts/benchmark.py --megabytes 2 --piece-kib 128` and explain that the result is environment-specific.
5. Open `python3 gui.py` and verify the GUI on the computer you will present from.

## 3–5 minute live demo

1. Start the tracker. Explain that it coordinates peer discovery, not file-byte transfer.
2. Start a seed peer on port `5000`.
3. Start a peer with an empty share directory on port `5001`.
4. Use the GUI or run `python3 control.py 5001 download`.
5. Show the manifest discovery, piece downloads, SHA-256 verification, progress, and reconstructed output.
6. Start another empty peer on port `5002` and download again. Explain that the first leecher now advertises its verified pieces and can seed the second leecher.
7. Show `ARCHITECTURE.md` and discuss one limitation: the centralized tracker is a single point of failure.

## Suggested explanation

“SwarmForge separates peer discovery from data transfer. A centralized tracker returns peer endpoints and an ordered manifest, while peers exchange the actual pieces directly over TCP. Each piece has a position-aware ID and a SHA-256 digest that is verified before it is announced to the swarm. A completed leecher can therefore become another source, which demonstrates swarm propagation.”

## Be transparent

This is a small educational P2P implementation, not the official BitTorrent protocol. The default configuration is localhost-only, uses a centralized tracker, and has no encryption, DHT, NAT traversal, or authentication. The included pieces are demonstration data; replace them with your assignment data only if your assignment rules allow it.


## Optional: use your own file

Run `python3 scripts/prepare_torrent.py ./your-file.zip --pieces-dir ./your-file-pieces`. Copy the printed environment-variable exports into every tracker/node terminal, start a seed with `--share-dir ./your-file-pieces`, then download from an empty peer. The reconstructed output keeps the original filename.

## Updated GUI walkthrough (2.1.0)

- **Overview:** use the topology view and session details to explain the selected peer and its successful peer history.
- **Pieces & peers:** switch between All pieces, Missing, and Verified to inspect the local piece inventory.
- **Activity console:** search logs, filter errors/warnings/transfers, and export the visible peer log.
- **Theme:** use the Theme control to switch between dark and light mode (`Ctrl+Shift+T` also works).
- **Refresh:** use Refresh now or press `F5`; `Ctrl+L` opens the activity console.
- **Peer lifecycle:** use Stop peer to request a graceful shutdown of a node launched by this dashboard.
- **Responsiveness:** briefly demonstrate that the window remains interactive while a peer is unreachable; network requests execute in worker threads.
