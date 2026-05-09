# Nisemonogatari-Telnet

A Telnet server that streams **Nisemonogatari** episodes as ANSI art, directly to your terminal.

```
telnet nisemonogatari.com
```
---
![image](demo.gif)
---

## What it is

Connect via Telnet and get an interactive episode menu. Pick an episode, and the server streams pre-rendered ANSI frames to your terminal in real time.

**Covered arcs (Season 2):**

| Key | Episode | Arc |
|-----|---------|-----|
| 1–8 | S02E01–S02E08 | Karen Bee |
| A–B | S02E09–S02E11 | Tsukihi Phoenix |

---

## Requirements

**To watch:** any modern terminal with:
- ANSI / VT100 escape sequence support
- UTF-8 encoding
- Minimum **80 × 24** columns/rows

> **Windows users:** use [PuTTY](https://putty.org) or WSL. The built-in Windows Telnet client is not supported.

**To run:** Docker, or Python 3.12+.

---

## Connect

```sh
telnet nisemonogatari.com
```

Controls inside the session:

| Key | Action |
|-----|--------|
| `1`–`9`, `A`, `B` | Select episode |
| `Q` | Disconnect |
| `Q` (during playback) | Return to menu |

---

## Self-hosting

### Docker

```sh
docker build -t nisemono .
docker run -p 23:23 nisemono
```

### Python (bare)

```sh
python telnet_server.py --host 0.0.0.0 --port 23
```

The server expects pre-rendered episode files at `output/S02Exx.ansi.gz`. All 11 episode files must be present for the full menu to work. Missing files are logged as warnings and gracefully reported to the client at playback time.

### Custom host/port

```sh
python telnet_server.py --host 0.0.0.0 --port 2323
```

---


## Project structure

```
.
├── telnet_server.py   # Server, negotiation, menu, frame playback
├── output/            # Pre-rendered ANSI episode files (*.ansi.gz)
├── Dockerfile
└── fly.toml
```

### ANSI file format

Each `.ansi.gz` file is a gzip-compressed binary stream:

**File header (16 bytes):**

| Field | Type | Description |
|-------|------|-------------|
| magic | 4 bytes | `ANSI` |
| fps_num | uint32 BE | Framerate numerator |
| fps_den | uint32 BE | Framerate denominator |
| total_frames | uint32 BE | Total frame count |

**Per-frame header (12 bytes):**

| Field | Type | Description |
|-------|------|-------------|
| frame_no | uint32 BE | Frame index |
| flags | uint32 BE | `0x01` = keyframe |
| dlen | uint32 BE | Byte length of frame data |

Frame data follows immediately after each header, raw ANSI/VT100 escape sequences, sent directly to the client socket.
