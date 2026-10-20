# SwarmForge Architecture and Learning Lab

**Author: Swayam Shakya**

## 1. Components

```text
                         control messages
                +-------------------------------+
                |                               v
          +-----------+                   +-------------+
          |  Tracker  |                   | GUI / CLI   |
          | peer index|                   | local API   |
          +-----------+                   +-------------+
             ^     ^                           |      |
     register|     |get_peers / manifest / heartbeat       |      |stats/logs/download
             |     |                           v      v
       +-----------+       raw piece TCP   +-----------+
       | Peer A    | <--------------------> | Peer B    |
       | seed/leecher|                      | leecher   |
       +-----------+                        +-----------+
```

The tracker is a control-plane service. It stores which peers claim to have which pieces and provides an ordered manifest. Peer nodes are the data plane: they transfer bytes directly to one another. A node can act as a downloader and uploader at the same time.

## 2. Piece lifecycle

1. A complete seed computes SHA-256 digests for configured piece files and publishes an ordered manifest with position-aware piece IDs.
2. A new peer registers its endpoint and requests the manifest.
3. For each missing piece, the downloader asks the tracker for owners and uses a simplified rarest-first rule (fewest currently discoverable owners; manifest order breaks ties).
4. The downloader asks a selected peer for the piece. The peer sends a small JSON header with the byte length, then raw bytes.
5. The receiver reads exactly the advertised length and computes SHA-256. A mismatch or incomplete transfer is rejected.
6. A verified piece is saved, announced to the tracker, and made available through the peer's upload server.
7. Once all pieces are present, the receiver concatenates them in manifest order into `output.bin`.

## 3. Important distributed-systems concepts

- **Partial failure:** a peer can disappear or reject a request while the tracker and other peers remain available. The client retries and tries other peers.
- **Shared state:** the tracker maintains a concurrent registry guarded by a lock. Peer records are refreshed by heartbeat leases and stale owners are pruned during lookups/health checks.
- **Data integrity:** content hashes verify piece bytes independently of the TCP connection.
- **Control/data plane separation:** the tracker does not relay piece bytes.
- **Availability and replication:** once a leecher verifies a piece, it becomes a new source, allowing the swarm to grow.
- **Trade-off:** the centralized tracker simplifies discovery but remains a single point of failure.

## 4. Message protocol

Control messages are UTF-8 JSON objects terminated by a newline. The peer transfer protocol sends one JSON header followed by exactly `size` raw bytes. Because the receiver reads the header one byte at a time, it does not consume bytes from the binary body while parsing the header.

| Message | Sender → receiver | Purpose |
|---|---|---|
| `register` | peer → tracker | Advertise endpoint and locally available piece IDs; complete seeds may publish the manifest |
| `get_manifest` | peer → tracker | Fetch canonical piece order, names, hashes, and sizes |
| `get_peers` | peer → tracker | Find sources for a given piece ID |
| `get_piece` | downloader → uploader | Request a specific piece; uploader responds with header then bytes |
| `announce` | peer → tracker | Advertise a piece only after local SHA-256 verification |
| `heartbeat` | peer → tracker | Refresh the peer lease for currently available piece IDs |
| `unregister` | peer → tracker | Remove the peer during graceful shutdown |
| `health` | client → tracker | Check tracker service health and in-memory counts |
| `stats`, `logs`, `download`, `register` | GUI/CLI → peer API | Inspect or control a local node |

## 5. Experiments to run and document

1. **Swarm growth:** run one seed and then one, two, and three empty leechers. Show that completed leechers are also piece sources.
2. **Tracker failure:** stop the tracker after registration. Explain why a peer may still upload to a known endpoint but cannot discover new owners.
3. **Peer failure:** stop one seed during transfer. Record whether retry/fallback succeeds when another source is available.
4. **Integrity failure:** alter a piece file after creating the manifest, or unit-test a modified byte string. Explain why the SHA-256 check prevents accepting altered content.
5. **Rarest-first:** create a swarm where one piece has fewer owners than another. Compare the selected piece and discuss the limitation that this implementation performs discovery requests serially.
6. **Chunk-size effect:** run the smoke test with different `SWARMFORGE_CHUNK_SIZE` values and record transfer time. Tiny sample files may make results noisy; repeat with larger generated files.
7. **Control/data plane:** inspect logs and explain that the tracker exchanges hashes/endpoints while file bytes move peer-to-peer.

For a report, include environment details, commands, expected versus actual results, screenshots of the GUI, and a small table of repeated measurements. Do not present a single tiny-file run as a meaningful benchmark.

## 6. Limitations and next milestones

- The tracker uses in-memory state; restarting it loses the registry and manifest.
- Peer liveness uses periodic heartbeat leases, but there is no authenticated identity or failure detector beyond those leases.
- There is no authentication, encryption, DHT, NAT traversal, choking/unchoking, parallel piece requests, or durable resume database.
- A complete seed establishes the manifest. Multiple unrelated files must not reuse the same torrent ID.
- The current downloader buffers one whole piece in memory before writing it; streaming-to-temp-file would scale better.

Potential next milestones: persistent resume state, a streaming transfer format, per-peer throughput metrics, parallel downloads with bounded concurrency, and signed manifests.


## Tracker leases and lifecycle

Peers refresh the tracker with a heartbeat containing their endpoint and currently available piece IDs. The tracker expires stale owners after a lease timeout and supports explicit unregister on graceful shutdown. This reduces stale-peer discovery in a long-running local experiment, but it is not a substitute for authenticated peer identity or a production DHT.

## Arbitrary-file workflow

`scripts/prepare_torrent.py` splits an input file into ordered chunks and prints the environment variables needed by the tracker and every node. The source file is reconstructed by concatenating verified pieces in manifest order. Empty input files are represented by one empty piece.
