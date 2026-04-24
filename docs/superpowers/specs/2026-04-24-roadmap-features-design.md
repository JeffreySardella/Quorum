# Quorum Roadmap Features — Design Spec

**Date:** 2026-04-24
**Scope:** Six planned features + README update, monolithic architecture

---

## 1. New Signals: OCR + Fingerprint

### OCR Signal (`signals/ocr.py`)

Runs PaddleOCR (ONNX Runtime + DirectML, or CPU when `cpu_only = true`) on the same keyframes that vision already extracts.

**Output fields:**
- `all_text` — raw concatenation of all detected text
- `title_card_text` — text from large, centered overlays (title cards, credits)
- `date_stamp` — parsed camcorder date overlays ("JAN 15 2003") as ISO datetime string
- `credits_text` — "Directed by", "Starring", etc.

**Date stamp extraction:** Camcorder date overlays are parsed into datetime and made available as a date signal for `home-videos` and `photos` modes when EXIF is missing. The OCR signal exposes a `parse_date_stamps(keyframes) -> datetime | None` function that `photos.resolve_date()` and `home_videos` can call directly, independent of the full signal pipeline.

**Confidence:** Average OCR detection confidence across text regions. Title-card text with high confidence (>0.8) and a TMDB match boosts the overall signal confidence.

**Used by:**
- `auto` mode ensemble — title cards help identify commercial content
- `enrich` — visible text feeds into the synthesis prompt for richer descriptions
- `home-videos` / `photos` — date stamps as fallback date source

**Config:** `signals.ocr = true/false` (already stubbed in `SignalToggles`).

**Dependencies:** `paddleocr`, `paddlepaddle` (or `onnxruntime` + `onnxruntime-directml`). Uses ONNX Runtime to avoid PyTorch dependency on Windows/AMD.

### Fingerprint Signal (`signals/fingerprint.py`)

Uses `pyacoustid` (Python wrapper around Chromaprint) to fingerprint audio.

**Identification flow:**
1. Extract first 60 seconds of audio (reuses `extract.py`)
2. Compute Chromaprint fingerprint via `pyacoustid`
3. Query AcoustID web service for matches
4. If match found with score > 0.8: return identified recording (song title, artist, associated releases)
5. Map releases to TV shows / movies when possible (theme songs, soundtracks)

**Duplicate detection:**
- Stores fingerprint hashes in `.quorum-cache/fingerprints.json`
- After scanning, compares all fingerprints pairwise (hamming distance on chromaprint hashes)
- Near-duplicates (distance below threshold) are surfaced in `dedup-<timestamp>.log`
- Log format: pairs of file paths + similarity score, for human review

**Home video music tagging:**
- When `enrich` runs and fingerprint identifies background music, adds `<tag>music:Song Name - Artist</tag>` to the `.nfo`

**Config:** `signals.fingerprint = true/false` (already stubbed in `SignalToggles`).
**Env:** `ACOUSTID_API_KEY` in `.env`.
**Dependencies:** `pyacoustid` (wraps Chromaprint C library via fpcalc binary).

Both signals implement the `Signal` protocol from `signals/base.py` and participate in the consensus voter.

---

## 2. `enrich-photos` — Face Clustering + Scene Tagging

New module: `src/quorum/enrich_photos.py`
New CLI command: `quorum enrich-photos <root>`

### Scene Tagging

- Walks `Photos/YYYY/YYYY-MM-DD/` directories
- For each photo, runs the vision LLM (same Ollama model as video enrich) to generate:
  - `setting` — where (beach, kitchen, park, etc.)
  - `activity` — what's happening
  - `objects` — notable items (cake, balloons, dog, etc.)
  - `mood` — festive, casual, formal, etc.
- Writes a `.quorum.json` sidecar per photo with the scene tags
- Writes Plex-compatible `.nfo` sidecars: description from scene tags, `<genre>` and `<tag>` elements
- Resume-friendly: skips photos that already have a sidecar unless `--force`

### Face Clustering

- Uses InsightFace (via ONNX Runtime + DirectML, or CPU when `cpu_only = true`) to extract 512-dim face embeddings from each photo
- First pass: agglomerative clustering with cosine distance threshold (no predefined k)
- Stores embeddings + cluster assignments in `faces.db` (SQLite) at the library root
- Schema: `faces(id, photo_path, bbox_x, bbox_y, bbox_w, bbox_h, embedding BLOB, cluster_id, label TEXT, label_source TEXT, confidence REAL)`
- Clusters start as anonymous ("Person 1", "Person 2", ...)
- Incremental: on subsequent runs, new photos are embedded and assigned to existing clusters or form new ones

### LLM-Assisted Naming

After clustering, for each unnamed cluster:
1. Gather context: which folders contain this person? What are the folder names? What did scene tagging find?
2. Send representative face crops + context to the vision LLM: "This person appears in folders named 'Sophia 4th birthday', 'Easter 2005', 'Sophia soccer'. They are a young girl appearing in 47 photos. Who is this likely to be?"
3. Assign the LLM's guess as a provisional label with confidence score
4. `label_source = "llm"` in `faces.db`
5. Write `face-review-<timestamp>.log` listing all provisional labels for human review

### Seeded Corrections

- `faces/` directory at library root — drop labeled photos here (`sophia.jpg`, `jeff.jpg`)
- On each run, seed photos are embedded and matched against existing clusters (closest cluster by centroid distance)
- Seed matches override LLM guesses: `label_source = "seed"`
- Manual corrections via the review log or web UI persist in `faces.db` as `label_source = "manual"`
- Priority: `manual` > `seed` > `llm`

### Plex Integration

- Face tags written into `.nfo` as `<actor><name>Sophia</name></actor>`
- Scene tags as `<genre>` and `<tag>` elements

### CLI

```
quorum enrich-photos <root>
quorum enrich-photos --no-faces <root>     # scene tagging only
quorum enrich-photos --force <root>        # regenerate all sidecars
```

### Dependencies

`insightface`, `onnxruntime` (+ `onnxruntime-directml` on AMD GPUs).

---

## 3. Folder-Rename Pass

New module: `src/quorum/rename_folders.py`
New CLI command: `quorum rename-folders <root>`
Also runs automatically at the end of `enrich`.

### Logic

1. Walk `Home Videos/YYYY/` and find event folders containing `.nfo` sidecars
2. For each fully-enriched folder (every video has a `.nfo`):
   a. Read all `.nfo` titles and descriptions
   b. Ask the text LLM: "These N videos in a folder currently named 'CURRENT_NAME' have these titles/descriptions: [...]. Propose a clean, accurate folder name under 80 chars."
   c. Compare proposed name to current name
   d. If meaningfully different: rename the folder
3. Skip folders with un-enriched videos (incomplete data). Log as "skipped: not fully enriched"

### Safety

- Writes `rename-folders-<timestamp>.log` in standard JSONL format — reversible with `quorum undo`
- Dry-run: `quorum rename-folders --dry-run <root>`
- Won't rename if proposed name collides with an existing folder

### Auto-trigger after enrich

- At the end of `run_enrich()`, if a folder is fully enriched, automatically runs the rename pass
- `quorum enrich --no-rename <root>` to skip
- Rename log entries appended to the enrich log so a single `quorum undo` reverses both

### Standalone command

- `quorum rename-folders <root>` — runs independently on already-enriched libraries
- Useful after manual `.nfo` edits or on folders enriched in a previous session

---

## 4. Watch-Folder Daemon

New module: `src/quorum/watch.py`
Started by: `quorum serve` (shared with web UI) or `quorum watch` (standalone)

### Configuration

New `[watch]` section in `config.toml`:

```toml
[watch]
poll_interval = 30            # seconds between filesystem checks

[[watch.inbox]]
path = "E:/Incoming/Movies"
mode = "auto"
dest = "E:/Organized"

[[watch.inbox]]
path = "E:/Incoming/HomeVideos"
mode = "home-videos"
dest = "E:/Organized"

[[watch.inbox]]
path = "E:/Incoming/Photos"
mode = "photos"
dest = "E:/Organized"

[watch.plex]
enabled = true
url = "http://127.0.0.1:32400"
token = ""                    # also settable via PLEX_TOKEN env var
library_ids = []              # empty = refresh all
```

### Processing Pipeline

```
new file detected
  -> stabilize wait (no size change for 5 seconds)
  -> organize (auto / home-videos / photos, per inbox config)
  -> enrich / enrich-photos (videos get enrich, photos get enrich-photos)
  -> folder-rename pass (if applicable)
  -> Plex library refresh (if configured)
```

### File Watcher

- Uses `watchdog` library for OS-level filesystem events
- Polling fallback for network drives (configurable via `poll_interval`)
- Stabilization check: file size sampled twice with 5-second gap. Only processes when stable.

### State Management

- `watch-state.json` at the dest root tracks processed files (path + mtime + status)
- Survives daemon restarts — won't reprocess files
- Failed files logged with error, retried on next daemon restart

### Plex Integration

- After processing completes, sends `GET /library/sections/{id}/refresh` to the Plex server
- Requires Plex auth token (from config or `PLEX_TOKEN` env var)
- If `library_ids` is empty, refreshes all library sections

### Resilience

- JSONL log at `<dest>/watch-<date>.log` — same format, same `quorum undo` support
- Ctrl+C triggers graceful shutdown: finishes current file, then exits
- Uncaught exceptions in per-file processing are caught, logged, and skipped

### CLI

```
quorum watch                    # standalone foreground daemon
quorum watch --dry-run          # logs what it would do
```

Also started by `quorum serve` (see Web UI section).

### Dependencies

`watchdog`.

---

## 5. Web UI

New package: `src/quorum/web/` (FastAPI app + Jinja2 templates + static assets)
New CLI command: `quorum serve`

### Stack

- **Backend:** FastAPI
- **Templates:** Jinja2
- **Interactivity:** htmx (loaded from static, no CDN)
- **Styling:** Simple CSS (no framework), or classless CSS like Pico
- **No build step.** No Node, no npm, no JS framework. Ships as Python + HTML + CSS.

### Entry Point

```
quorum serve                    # starts web UI on :8080 + watch daemon
quorum serve --no-watch         # web UI only
quorum serve --port 9090        # custom port
```

`quorum serve` starts both the FastAPI server (via uvicorn) and the watch daemon in background threads. They share the same process and config.

### Pages

| Route | Page | Description |
|---|---|---|
| `/` | Dashboard | Library stats (total videos/photos, enriched count, pending), watch daemon status, Ollama status, GPU/CPU mode |
| `/commands` | Commands | Launch any mode with the same options as CLI. Live progress via SSE |
| `/review` | Review Queue | Browse `scan` proposals. Approve/reject/edit. Bulk approve. Apply button |
| `/library` | Library Browser | Browse organized files by year/event. View `.nfo` inline. Photo preview. Face tags |
| `/faces` | Face Review | View face clusters. Merge/split. Assign names. Drag seed photos |
| `/dedup` | Dedup Report | Fingerprint duplicates side by side. Keep/delete/ignore |
| `/logs` | Logs | Browse all run logs. One-click undo |
| `/settings` | Settings | Edit config.toml values. Restart watch daemon |

### Architecture

- Backend calls the same `run_*` functions the CLI uses — no logic duplication
- Long-running operations run in background threads. An in-memory job registry tracks them
- Progress pushed to browser via SSE (`/api/jobs/{id}/stream`)
- All state is filesystem-based: logs, `.nfo`, `faces.db`, `watch-state.json` — no database beyond SQLite for faces
- API prefix: `/api/` for JSON endpoints that the htmx frontend calls

### Auth

- None by default (local tool)
- Optional basic auth via config for remote/server access:

```toml
[web]
port = 8080
auth_user = ""        # empty = no auth
auth_password = ""
```

### Dependencies

`fastapi`, `uvicorn`, `jinja2`, `python-multipart`.

---

## 6. CPU-Only Mode

Global flag affecting all GPU-accelerated components.

### Configuration

- CLI: `quorum --cpu-only <command>` (top-level typer option)
- Env: `QUORUM_CPU_ONLY=1`
- Config: `cpu_only = true` in `config.toml`
- New field in `Settings`: `cpu_only: bool = False`

### Effect on Components

| Component | GPU mode | CPU-only mode |
|---|---|---|
| Ollama | Unaffected (manages its own device) | Unaffected |
| faster-whisper | Already CPU by default | No change |
| InsightFace | ONNX Runtime + DirectML | ONNX Runtime CPU |
| PaddleOCR | ONNX Runtime + DirectML | ONNX Runtime CPU |
| Chromaprint | Pure CPU | No change |

### Implementation

- Each component that initializes an ONNX Runtime session checks `settings.cpu_only`
- If true: `providers = ["CPUExecutionProvider"]`
- If false: `providers = ["DmlExecutionProvider", "CPUExecutionProvider"]` (falls back to CPU if DirectML unavailable)
- No code path changes — just provider selection at init time

---

## 7. README Update

### Additions

- Command reference for: `enrich-photos`, `rename-folders`, `serve`, `watch`
- Update `enrich` section: `--no-rename` flag, auto-rename behavior
- Config reference: `[watch]`, `[watch.plex]`, `[web]`, `[faces]`, `cpu_only`
- Env reference: `ACOUSTID_API_KEY`, `PLEX_TOKEN`, `QUORUM_CPU_ONLY`
- Architecture diagram: add OCR + fingerprint signals to ensemble flow
- Repo layout: all new files
- Troubleshooting: InsightFace on AMD, PaddleOCR ONNX, watch daemon on network drives, Plex token retrieval
- AMD GPU notes table: add InsightFace, PaddleOCR, Chromaprint rows
- CPU-only section
- Move all six items from Planned to Shipped
- Update summary table at top with new commands

### Cleanup

- Update install section with new dependencies
- Update setup script for new deps
- Review for accuracy against implemented code

---

## Dependencies Summary

All added to `requirements.txt` / `pyproject.toml`:

| Package | Used by | Notes |
|---|---|---|
| `paddleocr` | OCR signal | ONNX-based, no PyTorch |
| `pyacoustid` | Fingerprint signal | Wraps Chromaprint via fpcalc |
| `insightface` | enrich-photos faces | ONNX Runtime backend |
| `onnxruntime` | OCR, faces | CPU provider |
| `onnxruntime-directml` | OCR, faces | AMD GPU provider (optional) |
| `watchdog` | Watch daemon | Filesystem events |
| `fastapi` | Web UI | HTTP framework |
| `uvicorn` | Web UI | ASGI server |
| `jinja2` | Web UI | HTML templates |
| `python-multipart` | Web UI | Form handling |

Existing deps unchanged: `typer`, `httpx`, `pydantic`, `rich`, `faster-whisper`, `imageio-ffmpeg`, `pillow`, `pillow-heif`.
