# MKV2MP4-Wizard 🎬

A robust, production-ready CLI tool that converts `.mkv` video files to `.mp4` using a
**smart inspection-first approach** — remuxing when possible, re-encoding only when necessary.
Designed for WSL2 (Windows Subsystem for Linux) but works on any Linux system.

---

## Features

- **Smart strategy** – inspects each file with `ffprobe` before touching it:
  - **Remux** (copy): used when all streams are already MP4-compatible (H.264/H.265 + AAC/MP3).
  - **Re-encode**: used when codecs like VP9, AV1, AC3, or DTS are present.
- **Hybrid binary management** – no manual `apt install` needed:
  1. Uses your system `ffmpeg`/`ffprobe` if installed.
  2. Falls back to a cached static binary in `bin/`.
  3. Auto-downloads the latest [BtbN](https://github.com/BtbN/FFmpeg-Builds) GPL static build if nothing is found.
- **WSL2 path handling** – transparently converts Windows-style paths (`C:\Users\…`) to WSL mounts (`/mnt/c/…`).
- **Metadata preservation** – copies global tags (title, artist, date, …) to the output file.
- **Subtitle awareness** – warns when ASS/SSA or other MP4-incompatible subtitle tracks will be dropped.
- **Batch conversion** – pass a directory to process all `.mkv` files inside it recursively.
- **Rich terminal UI** – coloured logs, progress bars, and stream tables.

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/FlaccidFacade/ffmpeg-mkv2mp4.git
cd ffmpeg-mkv2mp4

# 2. (Recommended) Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt
```

No other setup is required. On first run, if `ffmpeg` is not on your PATH, the tool
downloads and caches a static binary automatically.

---

## Usage

### Convert a single file (auto-strategy)
```bash
python main.py --input movie.mkv
```

### Convert a whole directory
```bash
python main.py --input /mnt/c/Videos/ --output /mnt/c/Videos/mp4/
```

### Force re-encode (override smart detection)
```bash
python main.py --input movie.mkv --force
```

### Preview the planned command without executing
```bash
python main.py --input movie.mkv --dry-run
```

### Show detailed stream information
```bash
python main.py --input movie.mkv --verbose
```

### Use system binaries only (no download fallback)
```bash
python main.py --input movie.mkv --prefer-system
```

### Tune re-encode quality
```bash
python main.py --input movie.mkv --force --crf 18 --preset slow
```

### All options
```
  --input PATH, -i PATH   Source .mkv file or directory
  --output DIR, -o DIR    Output directory (default: same as source)
  --force, -f             Force re-encoding even when remux would work
  --dry-run, -n           Print planned command, do not execute
  --verbose, -v           Show stream tables and codec details
  --prefer-system         Use only system ffmpeg/ffprobe (no download)
  --crf N                 libx264 CRF value (default: 23, range 0–51)
  --preset PRESET         libx264 preset (default: medium)
```

---

## Hybrid Binary Strategy

```
┌─────────────────────────────────────────────────────────────┐
│                    Binary Resolution                        │
│                                                             │
│  1. shutil.which("ffmpeg") → run --version                  │
│     ✓ found & working  →  use system binary                 │
│     ✗ missing/broken   →  continue ↓                        │
│                                                             │
│  2. Check bin/ffmpeg (local cache)                          │
│     ✓ found & working  →  use cached binary                 │
│     ✗ missing/broken   →  continue ↓                        │
│                                                             │
│  3. Download BtbN GPL static build (HTTPS)                  │
│     Extract ffmpeg + ffprobe → bin/                         │
│     Set chmod +x → use downloaded binary                    │
└─────────────────────────────────────────────────────────────┘
```

The `bin/` directory is excluded from version control (`.gitignore`) but a
`bin/.gitkeep` is committed so the directory exists in a fresh clone.

---

## Project Structure

```
ffmpeg-mkv2mp4/
├── main.py                  # CLI entry point & orchestration
├── requirements.txt
├── README.md
├── .gitignore
├── bin/
│   └── .gitkeep             # placeholder; ffmpeg/ffprobe downloaded here at runtime
└── src/
    ├── __init__.py
    ├── bin_manager.py       # binary detection, download, and caching
    ├── inspector.py         # ffprobe wrapper; stream analysis; strategy decision
    └── converter.py         # ffmpeg command builder; progress bar; error handling
```

---

## Requirements

| Package   | Purpose                                   |
|-----------|-------------------------------------------|
| `requests` | HTTP download of static binaries         |
| `rich`     | Coloured output, tables, progress bars   |
| `tqdm`     | Download progress bar                    |

Python 3.10 or newer is required.

---

## Exit Codes

| Code | Meaning                                    |
|------|--------------------------------------------|
| 0    | All conversions succeeded                  |
| 1    | At least one conversion failed             |
| 2    | Invalid arguments or input path not found  |