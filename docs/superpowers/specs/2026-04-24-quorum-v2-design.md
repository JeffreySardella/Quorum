# Quorum V2 — Concentric Rings Design Spec

**Date:** 2026-04-24
**Status:** Approved
**Approach:** Concentric Rings — expand outward from the core, each ring shippable independently
**Pace:** Marathon — quality over speed, build it right over months
**UI parity:** Every feature works in both CLI and web UI

---

## Table of Contents

1. [Vision](#vision)
2. [Architecture: The Quorum Engine](#architecture-the-quorum-engine)
3. [Ring 1: Smarter Core](#ring-1-smarter-core)
4. [Ring 2: New Media Types & Cross-Media Intelligence](#ring-2-new-media-types--cross-media-intelligence)
5. [Ring 3: Generalize Beyond Media](#ring-3-generalize-beyond-media)
6. [Build Order](#build-order)
7. [Cross-Cutting Concerns](#cross-cutting-concerns)

---

## Vision

Quorum V1 is a local, AI-powered media organizer for Plex. V2 expands Quorum into three concentric rings:

- **Ring 1:** Make the existing media pipeline smarter — search, dashboards, dedup, event detection, review workflows, notifications, and self-improving accuracy.
- **Ring 2:** Handle new media types (music, documents, scanned photos, audio memos) and link them across types into unified events.
- **Ring 3:** Generalize the engine beyond media — organize any files on your filesystem using the same quorum-voting approach, backed by a plugin architecture.

Each ring is independently shippable. Ring 1 makes the existing tool significantly better. Ring 2 expands scope. Ring 3 transforms Quorum into a general-purpose AI file organizer.

Core principles carry forward from V1:
- Everything runs locally, no cloud, no subscriptions
- Every destructive action is logged and reversible
- Multiple independent signals vote before any action
- Graceful degradation when AI models are unavailable

---

## Architecture: The Quorum Engine

### Foundation Layers

Three new foundational layers support all 21 features:

#### 1. Unified Metadata Index (`quorum.db`)

A single SQLite database replacing scattered state files (`.nfo` sidecars, `faces.db`, `watch-state.json`). Existing Plex-compatible sidecars continue to be written, but `quorum.db` becomes the source of truth for Quorum itself.

**Schema:**

```
media
├── id            INTEGER PRIMARY KEY
├── path          TEXT UNIQUE
├── type          TEXT (video, photo, music, document, audio, other)
├── size          INTEGER
├── checksum      TEXT (SHA-256)
├── created_at    TEXT (ISO 8601)
├── modified_at   TEXT (ISO 8601)
├── duration      REAL (seconds, nullable)
├── source_device TEXT (nullable)
└── event_id      INTEGER FK → events (nullable)

metadata
├── id            INTEGER PRIMARY KEY
├── media_id      INTEGER FK → media
├── key           TEXT (title, description, transcript, ocr_text, etc.)
└── value         TEXT

embeddings
├── id            INTEGER PRIMARY KEY
├── media_id      INTEGER FK → media
├── type          TEXT (face, scene, audio, text)
├── vector        BLOB (float32 array, queried via sqlite-vec)
└── label         TEXT (nullable — face name, scene tag, etc.)

tags
├── id            INTEGER PRIMARY KEY
├── media_id      INTEGER FK → media
├── category      TEXT (face, scene, object, custom)
└── value         TEXT

events
├── id            INTEGER PRIMARY KEY
├── name          TEXT
├── start_time    TEXT (ISO 8601)
├── end_time      TEXT (ISO 8601)
├── auto_detected BOOLEAN
└── metadata      TEXT (JSON — scene summary, people involved, etc.)

signals
├── id            INTEGER PRIMARY KEY
├── media_id      INTEGER FK → media
├── signal_name   TEXT (filename, vision, transcript, ocr, fingerprint, etc.)
├── candidate     TEXT (proposed title/identification)
├── confidence    REAL (0.0–1.0)
├── reasoning     TEXT
└── created_at    TEXT (ISO 8601)

feedback
├── id            INTEGER PRIMARY KEY
├── media_id      INTEGER FK → media
├── action        TEXT (approve, reject, correct)
├── original      TEXT (what was proposed)
├── correction    TEXT (what user said it should be, nullable)
└── created_at    TEXT (ISO 8601)

actions
├── id            INTEGER PRIMARY KEY
├── operation     TEXT (move, rename, delete, tag, etc.)
├── source_path   TEXT
├── dest_path     TEXT (nullable)
├── metadata      TEXT (JSON — any additional context)
├── reversible    BOOLEAN
└── created_at    TEXT (ISO 8601)

processing
├── id            INTEGER PRIMARY KEY
├── media_id      INTEGER FK → media (nullable for batch jobs)
├── job_type      TEXT
├── status        TEXT (pending, running, completed, failed)
├── progress      REAL (0.0–1.0)
├── error         TEXT (nullable)
├── started_at    TEXT (nullable)
└── completed_at  TEXT (nullable)
```

**Migration path:** `quorum db migrate` reads existing `.nfo` files, `faces.db`, and undo logs into the new schema. Non-destructive — old files are preserved.

**CLI commands:**
- `quorum db stats` — summary of index contents
- `quorum db export` — dump to portable JSON
- `quorum db rebuild` — re-index from sidecars and source files

#### 2. Plugin System

Each "organizer" registers against a shared engine core.

**Engine core (`quorum.engine`):**
- `SignalRegistry` — register signal implementations by name
- `ConsensusVoter` — configurable voting with per-signal weights
- `UndoLog` — transactional action logging with rollback
- `MetadataIndex` — read/write interface to `quorum.db`
- `NotificationBus` — event publish/subscribe
- `ConfigLoader` — merged config from `config.toml` + CLI flags

**Plugin interface:**

```python
class QuorumPlugin:
    name: str
    file_types: list[str]           # extensions this plugin handles
    signals: list[Signal]           # signal implementations
    default_rules: list[Rule]       # default organization rules
    cli_commands: list[typer.Typer]  # CLI subcommands to register
    web_routes: list[APIRouter]     # FastAPI routes to mount

    def on_register(self, engine: Engine) -> None: ...
    def on_scan(self, files: list[Path]) -> list[Proposal]: ...
    def on_apply(self, proposals: list[Proposal]) -> list[Action]: ...
```

**Discovery:** Python entry points (`[project.entry-points."quorum.plugins"]`). Built-in plugins ship with the package. Third-party plugins installable via pip.

**Build sequence:** Extract engine from existing code during Ring 1 work. Formalize plugin interface at the start of Ring 2. Ring 3 plugins depend on the stable interface.

#### 3. Notification Bus

Lightweight internal event system using Python's built-in patterns (no external message queue).

**Events:**

```
file.scanned        — new file discovered
file.enriched       — metadata extraction complete
file.moved          — file organized to destination
file.quarantined    — file moved to quarantine with reason
job.started         — processing job began
job.completed       — processing job finished
job.failed          — processing job errored
dedup.found         — duplicate cluster detected
review.pending      — new item in review queue
event.detected      — new event auto-created
feedback.received   — user approved/rejected/corrected
```

**Listeners (channels):**
- **Terminal** — Rich-formatted inline updates
- **Web/SSE** — push to browser via existing SSE infrastructure
- **Desktop** — OS-native toast notifications via `plyer`
- **Webhook** — HTTP POST to user-configured URL (supports Ntfy, Pushover, Slack, Discord, etc.)

---

## Ring 1: Smarter Core

All Ring 1 features build on the existing codebase plus the unified metadata index. No plugin architecture required yet — that's extracted incrementally.

### R1-1. Unified Metadata Index

**See Architecture section above for full schema.**

This is the foundation that all other Ring 1 features depend on. Build first.

**Implementation scope:**
- New `quorum/db.py` module with SQLAlchemy-free raw SQLite (keeping dependencies light)
- Migration command to import existing `.nfo`, `faces.db`, `watch-state.json`
- All existing write paths updated to dual-write (sidecar + index)
- All existing read paths updated to query index first, fall back to sidecar
- `sqlite-vec` extension for vector similarity search

### R1-2. Semantic Search

Natural language queries against the metadata index using text embeddings.

**Indexing:**
- At enrichment time, generate text embeddings via Ollama `/api/embeddings` endpoint
- Embed: video descriptions, transcripts, OCR text, photo scene descriptions, face labels, filenames
- Store in `embeddings` table with type `text`
- Incremental — only embed new/changed content

**Querying:**
- User query → embed via same model → cosine similarity via `sqlite-vec`
- Post-filter by date range, media type, tags, faces
- Return ranked results with relevance score and match snippet

**CLI:**
```
quorum search "beach videos from 2022"
quorum search "photos with grandma" --type photo --limit 20
quorum search "someone blowing out candles" --after 2023-01-01
```
Output: Rich table with path, type, score, and snippet.

**Web:**
- Search bar in the page header (available on every page)
- Results page with thumbnails (cached keyframes for video, actual images for photos)
- Filter sidebar: date range picker, media type checkboxes, face selector, tag cloud
- Infinite scroll or pagination

**Embedding model:** Uses whichever Ollama model is configured in `[models]`. No additional model download. Falls back to keyword search (SQLite FTS5) if Ollama is unavailable.

### R1-3. Library Dashboard

Stats and health overview of the organized library.

**Metrics:**
- Total files by type and year
- Storage consumption breakdown (bar chart by type, treemap by year)
- Processing pipeline: enriched vs. pending vs. quarantined (donut chart)
- Face cluster summary: top 10 people by appearance count
- Confidence distribution: histogram of auto-identification scores
- Recent activity: last 50 files processed/moved/quarantined
- Watch daemon status: active inboxes, last event timestamps
- Event timeline: events per month

**CLI:** `quorum dashboard`
- Rich-formatted terminal output
- Key numbers in a panel, activity sparkline, top faces list
- Refreshes on each invocation (no live mode in CLI)

**Web:**
- Expanded existing dashboard page
- Lightweight charts: inline SVG or Chart.js (loaded from CDN)
- Auto-refresh via SSE for processing status
- Keeps existing htmx/Jinja2 architecture

### R1-4. Smart Dedup

Cross-type, cross-format duplicate detection.

**Detection strategies:**

| Strategy | What it catches | Method |
|----------|----------------|--------|
| Exact duplicate | Same file, different path | SHA-256 checksum |
| Near-duplicate photo | Crops, resizes, slight edits | Perceptual hash (pHash) via `imagehash` |
| Cross-media moment | Photo taken during a video | Timestamp within 5s + face/scene overlap |
| Re-encoded video | Same content, different quality | Audio fingerprint + duration match (±2s) |

**Output:** Dedup report — clusters of related files, each with a recommended "keep" candidate (highest resolution, best quality, original format).

**CLI:**
```
quorum dedup scan                # find duplicates, write report
quorum dedup scan --aggressive   # include near-matches and cross-media
quorum dedup report              # view last scan results
quorum dedup apply               # move duplicates to holding folder (reversible)
quorum dedup apply --cluster 3   # apply for specific cluster only
```

**Web:**
- Dedup page (extends existing dedup report functionality)
- Side-by-side preview per cluster
- One-click keep/remove per file
- Batch actions: keep-best-in-all-clusters
- File details: resolution, size, format, creation date

**Safety:** Duplicates are moved to a configurable holding folder, never deleted. `quorum undo` reverses dedup actions. Holding folder can be purged manually after review.

### R1-5. Event Detection

Auto-group media into events by temporal and content clustering.

**Clustering algorithm:**
1. Sort all media by timestamp
2. Split into events at gaps > threshold (default: 2 hours, configurable via `[events.gap_hours]`)
3. Refine with secondary signals:
   - Face overlap: files sharing 2+ faces get affinity bonus
   - Scene similarity: cosine similarity of scene embeddings > 0.7 merges adjacent events
   - Folder proximity: files already in the same directory get affinity bonus
4. Name events via LLM: pass constituent metadata to Ollama, generate descriptive name
5. Fallback name: `{date} — {most common scene tag}`

**Data model:** Events in the `events` table. Each media file has an optional `event_id` FK. Files can belong to at most one event.

**CLI:**
```
quorum events detect                      # scan library, create/update events
quorum events list                        # show all events with counts
quorum events list --year 2023            # filter by year
quorum events show "Beach Day 2023"       # list files in event
quorum events show 42                     # by ID
quorum events merge <id1> <id2>           # combine events
quorum events split <id> --at <timestamp> # split at a point
quorum events rename <id> "New Name"      # manual rename
quorum events unlink <file>               # remove file from its event
```

**Web:**
- Events page: scrollable chronological timeline
- Each event card shows: name, date range, file count, thumbnail collage (up to 6 images)
- Click into event: grid of all media with playback/preview
- Drag-and-drop files between events
- Split/merge controls
- Event name inline editing

### R1-6. Confidence Review Workflow

Structured queue for reviewing borderline identifications.

**Queue population:**
- Files with consensus confidence between `review_floor` (0.30) and `auto_apply` (0.85) thresholds
- Sorted by confidence ascending (most uncertain first)
- New items added automatically as enrichment/auto runs

**Review item display:**
- Current filename and path
- Proposed identification (title, year, type)
- Per-signal breakdown: each signal's candidate, confidence, and reasoning
- Keyframe thumbnails (video) or image preview (photo)
- Transcript snippet if available
- TMDB match details if applicable

**CLI:**
```
quorum review                        # show next 10 items
quorum review --count 50             # show more
quorum review --sort confidence      # sort order (default: ascending)
quorum review --sort newest          # most recent first
quorum approve <id>                  # accept proposed identification
quorum reject <id>                   # keep original, mark reviewed
quorum correct <id> "Title (Year)"  # manually set correct answer
quorum review stats                  # pending/approved/rejected counts
```

**Web:**
- Review page: card layout, one item per card
- Each card: signal breakdown table, thumbnails, action buttons
- Keyboard shortcuts: `a` approve, `r` reject, `c` correct, arrow keys navigate
- Batch mode: select multiple, bulk approve/reject
- Filter by media type, date, confidence range
- Progress indicator: "47 of 132 reviewed"

### R1-7a. Processing Notifications

Alert users when things happen.

**Notification channels:**

| Channel | Implementation | Config |
|---------|---------------|--------|
| Terminal | Rich inline (existing) | Always on during CLI operations |
| Web/SSE | Push to browser (existing SSE) | Always on when web UI is open |
| Desktop | `plyer` library, OS-native toasts | `[notify.desktop]` toggle |
| Webhook | HTTP POST with JSON payload | `[notify.webhook.url]` |

**CLI:**
```
quorum notify test                     # send test to all enabled channels
quorum notify config --desktop on      # toggle desktop notifications
quorum notify config --webhook <url>   # set webhook URL
quorum notify config --webhook off     # disable webhook
quorum notify history                  # show recent notifications
```

**Webhook payload:**
```json
{
  "event": "job.completed",
  "timestamp": "2026-04-24T16:00:00Z",
  "summary": "Enriched 47 videos in 12m 34s",
  "details": { "processed": 47, "quarantined": 2, "duration_s": 754 }
}
```

**Config section:**
```toml
[notify]
desktop = true
webhook = ""           # URL, empty = disabled
webhook_events = ["job.completed", "file.quarantined"]  # filter
```

### R1-7b. Accuracy Feedback Loop

Self-improving signal weights based on user corrections.

**Data flow:**
1. User approves/rejects/corrects via review workflow
2. Feedback stored in `feedback` table: media ID, action, original proposal, correction
3. On `quorum signals retune` (or automatically after N feedback entries):
   - For each signal, calculate accuracy rate per content type
   - Adjust weight: `new_weight = base_weight * (accuracy / baseline_accuracy)`
   - Clamp weights to `[0.1, 3.0]` range to prevent runaway
4. Updated weights written to `config.toml` under `[signals.weights]`
5. User can inspect and override any weight

**Weight storage:**
```toml
[signals.weights]
filename = 1.0       # default baseline
vision = 1.0
transcript = 1.0
ocr = 0.8            # adjusted down after feedback showed OCR is noisy
fingerprint = 1.2    # adjusted up — fingerprint rarely wrong

[signals.weights.by_type]
# per-content-type overrides
home_video.vision = 1.3
home_video.filename = 0.6
commercial.filename = 1.5
```

**CLI:**
```
quorum signals weights              # show current weights
quorum signals retune               # recalculate from feedback
quorum signals retune --dry-run     # show what would change
quorum signals reset                # reset to defaults
```

**Transparency:** No black-box ML. Weight adjustments are simple ratios derived from approval/rejection rates, fully visible in config, and manually overridable.

---

## Ring 2: New Media Types & Cross-Media Intelligence

Ring 2 features register as plugins against the engine core. The plugin interface is formalized at the start of Ring 2 work.

### R2-1. Music Organization

**Plugin:** `quorum-music`

**File types:** `.mp3`, `.flac`, `.ogg`, `.m4a`, `.wav`, `.aac`, `.wma`, `.opus`

**Signals:**
- **Tag extraction** — read ID3v2 (MP3), Vorbis comments (FLAC/OGG), MP4 atoms (M4A) via `mutagen`
- **Audio fingerprint** — Chromaprint (reuse existing `fingerprint` signal) + MusicBrainz lookup
- **Filename parsing** — regex for `Artist - Title`, `01 - Track.mp3`, etc.

**Organization:**
- Plex-friendly structure: `Music/Artist/Album/01 - Track.ext`
- Download album art from MusicBrainz/Cover Art Archive
- Generate Plex-compatible `.nfo` for artists and albums
- Handle compilations and soundtracks (Various Artists)

**CLI:** `quorum music scan`, `quorum music apply`, `quorum music search`

**Dependencies:** `mutagen` (tag reading/writing), MusicBrainz API (free, rate-limited)

### R2-2. Document Organization

**Plugin:** `quorum-docs`

**File types:** `.pdf`, `.docx`, `.doc`, `.txt`, `.rtf`, `.odt`, `.xlsx`, `.csv`, `.pptx`

**Signals:**
- **Text extraction** — `pdfplumber` for PDFs, `python-docx` for Word, plain read for text
- **OCR** — PaddleOCR (reuse existing) for scanned PDFs and image-based documents
- **LLM classification** — Ollama categorizes: receipt, invoice, manual, legal, correspondence, medical, tax, personal
- **Date extraction** — regex + LLM for document dates (not file modification dates)
- **Entity extraction** — LLM pulls key entities: amounts, parties, account numbers

**Organization:**
- Structure: `Documents/{Category}/{Year}/{descriptive-name}.ext`
- Categories configurable in `config.toml`
- Searchable full-text index in `quorum.db` (FTS5)

**CLI:** `quorum docs scan`, `quorum docs apply`, `quorum docs search`, `quorum docs categories`

**Dependencies:** `pdfplumber`, `python-docx` (both lightweight)

### R2-3. Scanned Photo Recovery

**Plugin:** `quorum-scan-recovery`

**Capabilities:**
- Detect scanned prints: aspect ratio analysis, border detection, moiré pattern detection
- Auto-crop: find photo boundaries within scanner bed image
- Multi-photo scans: detect and split multiple photos on a single scan
- Deskew: straighten rotated scans via OpenCV
- Enhance: auto-levels, contrast adjustment for faded prints
- Date estimation: OCR for printed timestamps (common on film-era photos), LLM visual era estimation ("clothing and cars suggest 1980s")

**Output:** Recovered images fed into the standard photo pipeline (enrich-photos) for face clustering and scene tagging.

**CLI:** `quorum scan-recovery process <dir>`, `quorum scan-recovery preview <file>`

**Dependencies:** `opencv-python-headless` (image processing), already have PaddleOCR and Ollama

### R2-4. Cross-Media Event Linking

**Extension of R1-5 (Event Detection)**

Once Ring 2 plugins are active, events can span media types:
- Photos + videos from the same afternoon
- Voice memos recorded at an event
- Documents related to a trip (boarding passes, itineraries scanned via R2-2)
- Music playing in a video matched to the actual track (via fingerprint)

**New capabilities:**
- `quorum events enrich <id>` — re-analyze an event with all available plugins
- Event metadata includes: people present (faces), location (if EXIF GPS available), soundtrack, documents
- Event export: `quorum events export <id> --format zip` — package all media + metadata for sharing

### R2-5. Screen Recording Detection

**Signal (added to core, not a separate plugin):**

A classification signal that runs during triage/enrich to distinguish:
- **Camera footage** — real-world scenes, faces, variable lighting
- **Screen recording** — UI elements, mouse cursors, static layouts, text-heavy
- **Gaming clip** — 3D rendered scenes, HUD elements, high motion
- **Tutorial/presentation** — slides, code editors, narration-heavy audio

**Implementation:** Vision LLM prompt tuned for screen content detection. Keyframe analysis for UI element patterns (taskbars, window chrome).

**Routing:** Screen recordings and gaming clips go to configurable alternative directories instead of the family media library.

### R2-6. Audio Memo Organization

**Plugin:** `quorum-audio`

**File types:** `.m4a` (voice memos), `.wav`, `.ogg`, `.mp3` (non-music, short duration)

**Distinguishing from music:** Heuristic chain: (1) no music tags present, (2) speech detected in first 30s via Whisper, (3) duration < 10 minutes (configurable). If all three pass → audio memo. If ambiguous, falls back to LLM classification of a transcript sample. User can override via custom taxonomy rules (R3-6).

**Signals:**
- **Whisper transcription** — reuse existing faster-whisper, full transcription (not 30s clip)
- **Speaker diarization** — identify distinct speakers (via `pyannote.audio` or simpler energy-based detection)
- **Topic extraction** — LLM summarizes transcript into topic tags
- **Date extraction** — file metadata, mentioned dates in transcript

**Organization:** `Audio Memos/{Year}/{Date} — {Topic}.ext` with `.txt` sidecar containing full transcript.

**CLI:** `quorum audio scan`, `quorum audio apply`, `quorum audio search`

### R2-7. Backup Manifest

**Core feature (not a plugin) — works across all media types.**

**Manifest format:** SQLite database (portable, queryable) containing:
- Every organized file: path, checksum, size, media type
- Organization decisions: which signals proposed what, final consensus
- Event memberships
- Face/tag associations
- Config snapshot at manifest creation time

**CLI:**
```
quorum backup manifest                    # generate manifest for entire library
quorum backup manifest --since 2026-01    # incremental since date
quorum backup verify <manifest>           # check backup integrity against manifest
quorum backup rebuild <manifest> <source> # re-organize source files using saved decisions
quorum backup diff <m1> <m2>             # compare two manifests
```

**Use cases:**
- Verify backup completeness after copying to external drive
- Rebuild organization on a new machine without re-running AI
- Audit what changed between two points in time

---

## Ring 3: Generalize Beyond Media

Ring 3 transforms Quorum from a media tool into a general-purpose AI file organizer. All Ring 3 features depend on the plugin architecture being stable.

### R3-1. Downloads Folder Tamer

**Plugin:** `quorum-downloads`

**Behavior:**
- Watch configured download directories (default: `~/Downloads`)
- On new file arrival (stabilization check, same as existing watch daemon):
  1. Classify by extension + content analysis
  2. Route to appropriate directory based on rules + AI classification
  3. Log action for undo

**Classification categories:**
- Installer/executable → `Apps/` (or quarantine with warning)
- Document → feed into `quorum-docs` pipeline
- Image → feed into photo pipeline
- Video → feed into video pipeline
- Archive (`.zip`, `.tar.gz`) → inspect contents, classify by dominant type
- Code/data → `Projects/` or leave in place
- Unknown → hold in `Downloads/Unsorted/` for manual review

**Config:**
```toml
[downloads]
watch_paths = ["~/Downloads"]
route_installers = "~/Apps"
route_unknown = "~/Downloads/Unsorted"
auto_extract_archives = false
```

**CLI:** `quorum downloads watch`, `quorum downloads tidy` (one-shot scan), `quorum downloads rules`

### R3-2. Desktop Organizer

**Plugin:** `quorum-desktop`

**Behavior:**
- Periodic scan of Desktop (and other configured surfaces)
- Age-based suggestions: files older than threshold (default: 30 days) flagged for archival
- Project detection: group related files by name patterns, shared prefixes, temporal clustering
- Suggested actions: archive to dated folder, route to appropriate organizer, or flag for deletion

**CLI:**
```
quorum desktop scan                # analyze Desktop, show suggestions
quorum desktop tidy                # apply suggestions (with confirmation)
quorum desktop tidy --auto         # apply without confirmation (uses undo log)
quorum desktop stats               # age distribution of Desktop files
```

**Config:**
```toml
[desktop]
paths = ["~/Desktop"]
archive_after_days = 30
archive_to = "~/Archive/{year}/{month}/"
```

### R3-3. Plugin Architecture (Formalization)

**Timing:** Extract engine during Ring 1, formalize interface at Ring 2 start, open to third parties at Ring 3.

**Engine package:** `quorum.engine`

```
quorum/engine/
├── __init__.py          # public API
├── signals.py           # SignalRegistry, Signal protocol
├── voter.py             # ConsensusVoter with configurable weights
├── index.py             # MetadataIndex (quorum.db interface)
├── undo.py              # UndoLog with transactional rollback
├── bus.py               # NotificationBus (pub/sub)
├── config.py            # ConfigLoader (toml + env + CLI)
├── plugin.py            # QuorumPlugin base class
└── runner.py            # Orchestrator: scan → signal → vote → apply
```

**Plugin lifecycle:**
1. Discovery via Python entry points
2. `on_register(engine)` — plugin receives engine reference
3. Engine calls `on_scan(files)` with files matching plugin's `file_types`
4. Plugin returns `Proposal` objects
5. Engine runs consensus voter across all proposals
6. Engine calls `on_apply(proposals)` for accepted proposals

**Third-party plugin template:** Cookiecutter template or `quorum plugin init <name>` scaffolding command.

**CLI:** `quorum plugins list`, `quorum plugins info <name>`, `quorum plugins enable/disable <name>`

### R3-4. Project File Organizer

**Plugin:** `quorum-projects`

**Detection heuristics:**
- Related extensions: `.psd` + `.png` exports, `.docx` + `.pdf`, `.ai` + `.svg`
- Shared name stems: `report.docx`, `report-final.docx`, `report-v2.docx`
- Temporal clustering: files created within a short window with related names
- Source control markers: `.git`, `package.json`, `Makefile` indicate project roots

**Actions:**
- Group scattered project files into a project folder
- Identify orphaned exports (PNG with no source PSD)
- Suggest consolidation moves

**CLI:** `quorum projects scan`, `quorum projects gather`, `quorum projects list`

**Safety:** Only suggests moves, never auto-applies for project files (too risky). User must explicitly `quorum projects apply`.

### R3-5. Email Attachment Organizer

**Plugin:** `quorum-email`

**Supported formats:** `.mbox` (Thunderbird, Gmail export), `.pst` (Outlook, via `libpff`), Maildir

**Pipeline:**
1. Parse email archive, extract attachments with metadata (sender, date, subject)
2. Deduplicate against existing library (checksum match)
3. Classify each attachment (document, photo, etc.)
4. Route to appropriate organizer pipeline
5. Preserve email context in metadata (who sent it, when, subject line)

**CLI:**
```
quorum email import <archive>           # import and classify attachments
quorum email import <archive> --dry-run # preview without importing
quorum email stats <archive>            # show attachment summary
```

**Dependencies:** `mailbox` (stdlib for mbox/Maildir), `libpff-python` (optional, for PST)

### R3-6. Custom Taxonomy Rules

**Core feature — works across all plugins.**

User-defined rules in `config.toml` that layer on top of AI signals. Higher-priority rules override AI classification.

**Rule format:**
```toml
[[rules]]
name = "invoices"
match = { extension = [".pdf"], ocr_contains = "invoice" }
action = { move_to = "Documents/Financial/Invoices/{year}/" }
priority = 10

[[rules]]
name = "kids-art"
match = { type = "photo", faces = ["sophia", "max"], scene_contains = "drawing" }
action = { move_to = "Family/Kids Art/{year}/" }
priority = 5

[[rules]]
name = "work-screenshots"
match = { type = "photo", screen_recording = true, time_range = "09:00-17:00" }
action = { move_to = "Work/Screenshots/{year}/{month}/" }
priority = 8
```

**Match conditions:** `extension`, `type`, `faces`, `scene_contains`, `ocr_contains`, `transcript_contains`, `filename_matches` (regex), `size_gt`/`size_lt`, `time_range`, `screen_recording`, `duration_gt`/`duration_lt`

**Template variables in paths:** `{year}`, `{month}`, `{day}`, `{date}`, `{face}`, `{scene}`, `{category}`, `{ext}`

**CLI:**
```
quorum rules list                       # show all rules with priorities
quorum rules test <file>                # show which rules would match a file
quorum rules add --interactive          # guided rule creation
quorum rules disable <name>             # toggle rule off
```

**Web:** Rules management page with form-based rule creation, drag-to-reorder priority, and test-against-file functionality.

### R3-7. Portable "Organize Anything" CLI

The capstone feature that ties everything together.

```
quorum organize <path>                         # auto-detect types, load plugins, scan
quorum organize <path> --rules custom.toml     # use specific ruleset
quorum organize <path> --dry-run               # preview only
quorum organize <path> --type documents        # force specific plugin
quorum organize <path> --interactive           # review each proposal
```

**Behavior:**
1. Scan directory, detect file types present
2. Load relevant plugins automatically
3. Run each file through appropriate signal pipeline
4. Apply custom taxonomy rules
5. Run consensus voter
6. Present scan manifest for review (or auto-apply if confidence meets threshold)
7. Log all actions for undo

**This is the "just point Quorum at a messy folder and it figures it out" experience.**

---

## Build Order

### Ring 1 (Build sequentially — each depends on the previous)

| Order | Feature | Depends On | Estimated Scope |
|-------|---------|-----------|----------------|
| 1 | R1-1: Unified Metadata Index | — | Large (foundation for everything) |
| 2 | R1-3: Library Dashboard | R1-1 | Medium |
| 3 | R1-2: Semantic Search | R1-1 | Medium-Large |
| 4 | R1-5: Event Detection | R1-1 | Medium |
| 5 | R1-4: Smart Dedup | R1-1 | Medium |
| 6 | R1-6: Confidence Review Workflow | R1-1 | Medium |
| 7 | R1-7a: Processing Notifications | R1-1 (notification bus) | Small-Medium |
| 8 | R1-7b: Accuracy Feedback Loop | R1-6 | Small-Medium |

**Rationale:** Index first (everything depends on it). Dashboard second (immediate visibility into what the index contains — motivating). Search third (highest-impact user-facing feature). Events and dedup can swap. Review workflow needs the index populated. Notifications and feedback loop are polish on top.

### Ring 2 (More flexible — plugins are independent)

| Order | Feature | Depends On | Rationale |
|-------|---------|-----------|-----------|
| 1 | R3-3: Plugin architecture | Ring 1 complete | Extract engine, formalize interface |
| 2 | R2-5: Screen Recording Detection | Plugin arch | Signal addition, small scope |
| 3 | R2-1: Music Organization | Plugin arch | Reuses existing fingerprint signal |
| 4 | R2-6: Audio Memo Organization | Plugin arch | Reuses existing Whisper |
| 5 | R2-2: Document Organization | Plugin arch | Reuses existing OCR |
| 6 | R2-3: Scanned Photo Recovery | Plugin arch + R2-2 | Needs OpenCV, more complex |
| 7 | R2-4: Cross-Media Event Linking | All plugins active | Needs multiple types indexed |
| 8 | R2-7: Backup Manifest | All plugins active | Needs full picture of library |

**Note:** Plugin architecture (R3-3) is built at the start of Ring 2 even though it's a Ring 3 feature — it's the prerequisite for all Ring 2 work.

### Ring 3 (Build after Ring 2 plugins prove the architecture)

| Order | Feature | Depends On |
|-------|---------|-----------|
| 1 | R3-6: Custom Taxonomy Rules | Plugin arch |
| 2 | R3-1: Downloads Folder Tamer | Plugin arch + rules |
| 3 | R3-2: Desktop Organizer | Plugin arch + rules |
| 4 | R3-4: Project File Organizer | Plugin arch |
| 5 | R3-5: Email Attachment Organizer | Plugin arch + R2-2 |
| 6 | R3-7: Portable "Organize Anything" | Everything |

---

## Cross-Cutting Concerns

### CLI/Web Parity

Every feature provides both interfaces:
- **CLI:** Typer commands following existing patterns (`quorum <noun> <verb>`)
- **Web:** FastAPI routes + Jinja2 templates + htmx, following existing patterns
- Shared business logic in feature modules, thin CLI and web layers on top

### Testing Strategy

- Unit tests for signals, voter logic, index queries
- Integration tests with sample media files (small corpus in `tests/fixtures/`)
- CLI tests via Typer's test client
- Web tests via FastAPI's test client
- No mocking of SQLite — use in-memory databases

### Configuration

New features add sections to `config.toml`. Pattern:
```toml
[feature_name]
enabled = true
# feature-specific settings with sensible defaults
```

All features work with zero config (sensible defaults). Power users tune via config.

### Performance

- Index operations are incremental — skip already-processed files
- Embedding generation is the bottleneck — batch where possible
- SQLite WAL mode for concurrent read/write
- `sqlite-vec` for vector search avoids external dependencies (no FAISS, no Chroma)

### Dependencies

New dependencies kept minimal:
- `sqlite-vec` — vector similarity in SQLite (single extension)
- `imagehash` — perceptual hashing for photo dedup
- `plyer` — cross-platform desktop notifications
- `mutagen` — audio tag reading (Ring 2)
- `pdfplumber` — PDF text extraction (Ring 2)
- `opencv-python-headless` — image processing for scan recovery (Ring 2)

No heavy frameworks. No external services. Everything stays local.
