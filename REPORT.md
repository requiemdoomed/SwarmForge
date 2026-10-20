# SwarmForge Advanced Edition: Educational Peer-to-Peer File Distribution

**Author: Swayam Shakya**  
**Course:** Distributed Systems  
**Edition:** 2.1.0  
**Implementation:** Python TCP sockets, threads, SHA-256, Tkinter

## 1. Objective

SwarmForge is a small peer-to-peer (P2P) file-sharing system built to explore ideas from distributed systems. A tracker helps peers find one another, but the file data moves directly between peers. Files are split into ordered pieces, checked for integrity, and shared again after they have been verified. Keeping the implementation focused makes it easier to follow the network flow and investigate what happens when a peer disappears or a transfer fails.

## 2. Architecture

The system has four main layers:

1. **Tracker control plane:** stores in-memory manifests and piece-owner endpoints, serves peer discovery, reports health, refreshes peer leases, prunes stale owners, and supports graceful unregister.
2. **Peer data plane:** serves piece requests over TCP, discovers candidate peers, selects rare pieces first, retries failed transfers, validates the size and SHA-256 digest, and advertises verified pieces.
3. **Metadata and integrity:** stores an ordered manifest with a piece ID, filename, expected size, and digest for every piece. Piece IDs combine a sequence position and digest (for example `p00000-<sha256>`). The ID is distinct from the digest so repeated chunks can have identical bytes without collapsing two positions in the reconstructed file.
4. **Control and visualization:** a local node API supports status, logs, registration refresh, download start, and shutdown. The Tkinter dashboard queries actual node state; `control.py` offers terminal access.

The tracker handles coordination, while peers exchange the actual file data with one another. This is the main difference from downloading everything from a central server. In version 2.1.0, the dashboard sends network work to background threads and updates Tkinter through its main event loop. That design helps the window stay responsive while a peer is slow or unreachable.

## 3. Network protocol

Control messages use newline-delimited JSON over TCP. Tracker operations include `register`, `get_manifest`, `get_peers`, `announce`, `heartbeat`, `unregister`, `health`, and `stats`. A peer requests a piece using its piece ID. The uploader sends a JSON header containing the piece ID, expected digest, and byte size, followed by raw bytes. The receiver checks the header, expected size, and SHA-256 digest before publishing the piece to its local upload map.

A peer periodically sends a heartbeat listing its verified piece IDs. The tracker refreshes the peer's lease and removes stale owners after a timeout. During normal shutdown, the node attempts to unregister. Tracker state is in memory and does not survive a tracker restart.

## 4. Data integrity and reconstruction

Each manifest entry records the ordered position, safe piece filename, SHA-256 digest, and byte count. A downloaded piece is accepted only if its reported size matches the manifest and its digest matches the expected digest. Pieces are written to temporary files and renamed after verification. When every piece is available, the node concatenates the verified pieces in manifest order into the requested output filename and atomically replaces the output file.

Keeping the piece ID separate from the digest matters when a file contains repeated blocks. Two pieces can have identical bytes and therefore the same digest, but they still occupy different positions in the original file. Position-aware IDs preserve both pieces and allow the file to be rebuilt in the right order.

## 5. Distributed-systems concepts

- **Peer-to-peer roles:** each node can request and serve pieces.
- **Control plane versus data plane:** tracker messages locate owners; file bytes flow directly between peers.
- **Concurrency:** tracker and peer listeners use threads for simultaneous connections.
- **Shared mutable state:** tracker registry changes are protected by a lock.
- **Partial failure:** socket timeouts, retries, alternate peers, and stale-owner leases handle common failure cases.
- **Integrity:** SHA-256 and size checks reject incomplete or altered payloads.
- **Rarest-first selection:** the downloader prefers pieces with fewer known owners.
- **Swarm propagation:** a downloader advertises each verified piece so later peers can use it.
- **Centralization trade-off:** the tracker simplifies discovery but is a single point of failure.
- **Observability:** the GUI shows real node state, progress, peer history, logs, and transfer counters.

## 6. Arbitrary-file preparation and benchmark

`scripts/prepare_torrent.py` splits an input file into ordered piece files and prints the environment variables that all nodes in the swarm must share. It supports empty files and repeated piece content. `scripts/benchmark.py` generates a temporary payload, starts a local tracker and two nodes, measures a transfer, and verifies the output byte-for-byte. Benchmark results depend on the machine and should not be presented as universal throughput claims.

## 7. Verification

The following commands were run successfully in the build environment:

```bash
python3 -m py_compile *.py scripts/*.py tests/*.py
python3 -m unittest discover -s tests -v
python3 scripts/smoke_test.py
python3 scripts/benchmark.py --megabytes 2 --piece-kib 128
```

Results: Python compilation passed; **16 unit tests passed**; the end-to-end smoke test passed with a full seed, partial seed, empty leecher, repeated-content pieces, reconstruction, and re-seeding; the 2 MiB benchmark passed byte-for-byte integrity validation. One observed benchmark run transferred 2 MiB in approximately 0.205 seconds (about 9.76 MiB/s). This is a single local observation, not a performance guarantee.

The Python source for the GUI compiles, but the GUI could not be visually launched in the headless build environment. Launch it on the target computer and verify its behavior before a live presentation.

## 8. Running the system

For a GUI demonstration, open the project directory and run `python3 gui.py`. Start the tracker, start a seed node, start an empty node on another peer port, and request a download. For repeatable verification, run the unit tests and `scripts/smoke_test.py`. `README.md` and `DEMO.md` contain the detailed steps.

## 9. Limitations and future work

SwarmForge is an educational prototype, not the official BitTorrent protocol. It does not implement DHT discovery, NAT traversal, encryption, peer authentication, signed manifests, rate limiting, choking/unchoking, persistent tracker state, durable resume state, or bounded parallel piece requests. A tracker restart loses in-memory swarm state. The downloader buffers each whole piece in memory and rejects pieces larger than 256 MiB, so the current design is intended for modest educational files. SHA-256 verifies content relative to the manifest; it does not authenticate the publisher.

Possible next research directions include bounded parallel downloads, durable resume metadata, signed manifests, authenticated peer sessions, a replicated tracker, and experiments comparing piece-selection strategies under peer churn.

## 10. AI-use statement

AI assistance was used as a supporting tool during development, mainly to discuss possible design choices, troubleshoot selected issues, and improve some explanatory wording. It was not part of the running application, and no AI model is called by SwarmForge at runtime. The author should be able to explain the submitted code and should follow the course's rules for acknowledging AI assistance. This statement should be kept accurate and updated if the actual development process differs.

## 11. GUI validation and remaining limitations

The GUI source passes Python compilation. Tests under a virtual display created the window, switched themes, displayed telemetry, updated the piece list, drew the peer view, and showed logs. A GUI-based integration run also started a tracker, a seed, and an empty leecher. The download completed asynchronously, reached 100% progress, and matched the source data byte-for-byte. These checks used a virtual X display, not the author's own macOS desktop, so font rendering, window sizing, and folder-opening behavior should still be checked locally before a presentation. The dashboard is for monitoring and control; it is not a security boundary.
