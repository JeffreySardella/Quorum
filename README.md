# Quorum

**Local AI-powered media organizer for Plex.** Takes a messy pile of home videos, photos, and ripped movies — puts them into Plex-friendly folders and writes AI-generated titles and descriptions so Plex shows real metadata instead of filenames.

No cloud services. No subscriptions. Runs entirely on your machine against your files — designed for Windows + AMD GPUs (but works anywhere Python + Ollama + ffmpeg run).

## What it does

Quorum has five modes, each solving a different piece of the "my media is a mess" problem:

| Command | What it does | When to use |
|---|---|---|
| `quorum home-videos` | Sorts home videos into `Home Videos/YYYY/YYYY-MM - Event/` by parsing folder/filenames | Family archive with descriptive folder names or phone-timestamp files |
| `quorum photos` | Sorts photos into `Photos/YYYY/YYYY-MM-DD/` by EXIF date | Any photo dump; handles HEIC, skips Aperture libraries |
| `quorum enrich` | Watches each video and writes a Plex `.nfo` with AI-generated title + description | After videos are sorted, to make Plex browsing readable |
| `quorum auto` | Identifies commercial movies/TV against TMDB and moves them to Plex layout | Ripped movies with cryptic release-group names |
| `quorum triage` | Classifies each filename in a mixed folder as home vs commercial | Folder with both personal videos and ripped movies (e.g. VHS archive) |

Plus safety utilities:
- `quorum undo <log>` — reverse any organize/auto run
- `quorum scan` / `quorum apply` — manual review workflow as an alternative to `auto`

**Every destructive action is logged line-by-line** to a JSONL file. Every run is reversible with one command. Run confidently overnight.

## Install

### One-shot setup (Windows, AMD or Intel)

```powershell
git clone <repo-url> Quorum
cd Quorum
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows-amd.ps1
```

The setup script installs everything via pip:

- `faster-whisper` — Whisper transcription (auto-downloads model on first use)
- `imageio-ffmpeg` — bundles an ffmpeg binary, no system install needed
- `pillow` + `pillow-heif` — EXIF reading for photos (including iPhone HEIC)
- All the usual Python deps (typer, httpx, pydantic, rich)

And pulls the default vision model via Ollama:

```powershell
ollama pull mistral-small3.2:latest
```

### Manual install (macOS / Linux / cmd.exe)

```sh
git clone <repo-url> Quorum
cd Quorum
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
ollama pull mistral-small3.2:latest
```

### Configure (optional)

```powershell
copy .env.example .env              # add TMDB_API_KEY here for commercial identify boost
copy config.example.toml config.toml # tweak thresholds, pick different models
```

Quorum works fine without either — TMDB is only needed if you want commercial-identify metadata enrichment.

## Command reference with real examples

### `quorum home-videos` — organize home videos by folder/filename

Designed for family archives where the folder name describes the event (`"2005 sophia 4th bd, fishing derby, sea world"`) and/or filenames have dates (`20160820_115414.mp4` from phones).

```powershell
# dry-run first — nothing moves
quorum home-videos --dry-run "E:\messy\old dvds" "E:\Organized"

# actual run
quorum home-videos "E:\messy\old dvds" "E:\Organized"

# skip LLM folder-name cleanup (regex-only, much faster, less polished)
quorum home-videos --no-llm "E:\messy\Videos" "E:\Organized"
```

Output layout:
```
E:\Organized\Home Videos\
├── 2005\
│   ├── 2005 - Sophia's 4th Birthday, Fishing Derby, Sea World\
│   └── 2005-04 - Easter, Disney, Sophia's Birthday
├── 2006\
└── ...
```

Uses `gemma4:31b` to clean up folder names (fixes typos like `valtines→Valentines`, capitalizes proper names, shortens to under 80 chars). Falls back to pure regex if Ollama isn't running.

Files that can't be dated are parked in `<dest>/_quarantine/<original_folder>/<file>.mp4` with a `.quorum.json` sidecar explaining why.

### `quorum photos` — organize photos by EXIF date

```powershell
# dry-run
quorum photos --dry-run "E:\messy\Photos" "E:\Organized"

# real run
quorum photos "E:\messy\Photos" "E:\Organized"
```

Output layout:
```
E:\Organized\Photos\
├── 2013\
│   ├── 2013-07-04\IMG_1234.jpg
│   └── 2013-11-27\...
└── ...
```

Date resolution priority: EXIF `DateTimeOriginal` → EXIF `DateTime` → filename regex (`YYYYMMDD_HHMMSS`, `IMG_YYYY-MM-DD_*`, etc.) → parent folder year → file mtime. Each file's chosen date source is logged.

**Hard-skips Aperture / iPhoto libraries** — any file whose path includes `.aplibrary` / `.apdata` / `.photoslibrary` packages, or whose extension is `.apversion`, `.apmaster`, `.apfolder`, `.apalbum`, `.bam`, etc. The library stays intact.

Also correctly handles iPhone HEIC files via `pillow-heif`.

### `quorum enrich` — add Plex `.nfo` metadata to organized videos

For each video, extracts keyframes + audio, runs vision LLM + Whisper, and synthesizes a title + description. Writes a `.nfo` sidecar that Plex reads as the video's metadata.

```powershell
# main usage — enrich everything already under <root>/Home Videos/
quorum enrich "E:\Organized"

# much faster: skip audio transcription (vision only)
quorum enrich --no-whisper "E:\Organized"

# regenerate .nfo even if one exists
quorum enrich --force "E:\Organized"
```

Output (per video):
```xml
<?xml version='1.0' encoding='utf-8'?>
<movie>
  <title>Sophia's 4th Birthday Party</title>
  <plot>Children playing with Easter eggs in a backyard with a trampoline, parents cheering them on.</plot>
  <year>2005</year>
  <genre>Home Video</genre>
  <tag>quorum-enriched</tag>
</movie>
```

Point a Plex "Home Videos" library at `E:\Organized\Home Videos\` and it'll show those titles and plots.

**Resume-friendly** — skips any video that already has a `.nfo` (unless `--force`). Safe to interrupt with Ctrl+C and restart later.

**Mislabeled-content detection** — if the vision model sees content that strongly disagrees with the folder name (e.g. baseball footage in a folder labeled "Soccer"), the file is flagged in `enrich-mislabels-*.log` for your review.

### `quorum auto` — identify commercial movies/TV via TMDB

Uses an ensemble (filename parser + Ollama vision + Whisper transcript) to identify unknown video files against TMDB/TVDB, then moves high-confidence matches into Plex's standard layout.

```powershell
quorum auto --dry-run "E:\messy\movies" "E:\Organized"
quorum auto "E:\messy\movies" "E:\Organized"
```

Output layout:
```
E:\Organized\
├── Movies\
│   └── The Matrix (1999)\The Matrix (1999).mkv
└── TV Shows\
    └── Breaking Bad\Season 01\Breaking Bad - s01e05.mkv
```

Low-confidence files (ambiguous or no TMDB match) go to `<dest>/_quarantine/` with the full ensemble reasoning as a sidecar.

TMDB enrichment requires a free API key in `.env`:
```
TMDB_API_KEY=your-key-here
```

### `quorum triage` — split mixed home + commercial folders

For cases like a VHS-rip folder that has both `"04 easter, jeff 4th.mp4"` (home) and `"101 Dalmatians.mp4"` (commercial) scrambled together.

```powershell
quorum triage "E:\messy\VhsTapes"
```

Produces three manifest files alongside the source:
```
triage-home-<stamp>.txt          one absolute path per line
triage-commercial-<stamp>.txt    ditto
triage-unknown-<stamp>.txt       LLM couldn't decide
triage-<stamp>.log               full JSONL reasoning for each file
```

Then feed each manifest to the right tool. Nothing moves from this command — classify-only.

### `quorum undo <log>` — reverse a run

Every organize / auto / photos run produces a log. `undo` reads that log and reverses every move **in reverse order** so nested folder creates collapse cleanly:

```powershell
quorum undo --dry-run "E:\Organized\home-videos-20260419-143134.log"
quorum undo "E:\Organized\home-videos-20260419-143134.log"
```

Refuses to undo if the original source path is already populated — so you can't accidentally clobber a fresh copy.

### `quorum scan` / `quorum apply` — manual review workflow

For the rare case you want to eyeball every proposal before anything moves:

```powershell
quorum scan "E:\messy\movies"                 # writes review.jsonl, nothing moves
# open review.jsonl in a text editor, remove any lines you don't want
quorum apply --dry-run                        # preview
quorum apply                                  # rename in place (no folder reorganize)
```

`auto` is what actually builds a Plex library. `scan`/`apply` only renames in place.

## Architecture

### Data flow for commercial identify (`quorum auto`)

```
video
  │
  ├─► ffmpeg  → keyframes + audio clip
  │
  ├─► Signal: filename     (regex patterns: S01E02, Title (Year), junk stripping)
  ├─► Signal: vision       (Ollama vision model → JSON title guess)
  ├─► Signal: transcript   (Whisper → Ollama text model → JSON title guess)
  │
  └─► Consensus voter
         │  - bucket candidates by normalized title
         │  - score = mean confidence + bonus per agreeing signal
         │  - cross-check top candidate against TMDB
         ▼
      Proposal { current_name, proposed_name, confidence }
         │
         ├─ confidence ≥ auto_apply → moved into Plex structure
         └─ otherwise               → quarantine + sidecar
```

The core principle is in the name: **a rename happens only when the quorum agrees.** No single signal gets veto power.

### Repo layout

```
src/quorum/
  cli.py              typer commands: all the `quorum <verb>` entry points
  config.py           settings: env + config.toml, with pydantic validation
  home_videos.py      date-based sorting using folder names + filename patterns
  photos.py           EXIF-based photo sorting (+ Aperture library skip)
  enrich.py           AI metadata pass: vision + Whisper → .nfo sidecars
  triage.py           home vs commercial filename classifier
  organize.py         `auto` mode: identify + move into Plex layout + log + undo
  pipeline.py         ensemble orchestrator used by `auto`
  extract.py          ffmpeg wrapper (keyframes, audio, duration probe)
  ollama_client.py    minimal HTTP client for Ollama
  tmdb.py             TMDB search client
  signals/
    base.py           Signal protocol, Candidate dataclass
    filename.py       regex parser for release-style names
    vision.py         Ollama vision → candidate
    transcript.py     faster-whisper / whisper.cpp → Ollama → candidate
scripts/
  setup-windows-amd.ps1
  test_ollama.py      quick Ollama connectivity sanity check
```

## AMD GPU notes

Most ML tutorials assume CUDA. Here's what actually works on AMD Windows in 2026:

| Component | AMD path |
|---|---|
| LLM inference | **Ollama** (HIP/ROCm backend, supports 7900 XTX = gfx1100 natively) |
| Vision models | Ollama with `mistral-small3.2`, `qwen3-vl`, or Gemma 4 variants |
| Audio transcription | **faster-whisper** (CPU, zero setup) or **whisper.cpp + Vulkan** (GPU, requires compile from source) |
| Photo EXIF / dedup | pure Python via Pillow — no GPU needed |
| OCR (future) | PaddleOCR via ONNX Runtime + DirectML |
| Face embeddings (future) | InsightFace via ONNX Runtime + DirectML |

**Avoid**: PyTorch-GPU on Windows for anything that doesn't already ship ROCm wheels — painful. Use Ollama + faster-whisper + ONNX-DirectML instead. If you want PyTorch-GPU badly, WSL2 + ROCm works.

## Performance tips

- **`--no-llm` on `home-videos`** — skip folder-name LLM cleanup. Regex-only, 100x faster on phone-timestamp libraries.
- **`--no-whisper` on `enrich`** — skip audio transcription. ~2-3x faster (`19s/file` → `7-9s/file`). Loses audio-derived detail (catching names, quotes, place callouts) but keeps all visual descriptions.
- **Single-model enrich** — the default config uses `mistral-small3.2` for both vision and synthesis, avoiding VRAM swaps. Change via `config.toml` if you have a dedicated text model you prefer.
- **Keep Ollama warm** — the first call loads the model (~20–30s). Subsequent calls are much faster. Ollama keeps models resident for ~5 minutes after last use.

## Configuration reference

`config.toml` (copy from `config.example.toml`):

```toml
[models]
vision = "mistral-small3.2:latest"     # must exist in `ollama list`
text   = "gemma4:31b"

[whisper]
backend      = "faster-whisper"        # or "whisper.cpp"
model_size   = "small"                 # tiny | base | small | medium | large-v3 | distil-large-v3
compute_type = "auto"                  # auto | int8 | int8_float16 | float16 | float32
language     = "auto"                  # "en", "es", etc., or "auto"
# whisper.cpp backend paths (only if backend = "whisper.cpp"):
# binary = "C:/tools/whisper.cpp/whisper-cli.exe"
# model  = "C:/tools/whisper.cpp/models/ggml-large-v3-q5_0.bin"

[thresholds]
auto_apply   = 0.85                    # min confidence to auto-move in `auto` mode
review_floor = 0.30                    # below this, skipped entirely

[extract]
keyframe_count = 6                     # how many keyframes per video for vision
audio_seconds  = 30                    # how much audio to transcribe
cache_dir      = ".quorum-cache"       # extraction cache

[signals]                              # toggle individual signals
filename    = true
vision      = true
transcript  = true
```

`.env`:

```
TMDB_API_KEY=your-free-key-from-themoviedb.org
OLLAMA_URL=http://127.0.0.1:11434
```

## Troubleshooting

### `auto` mode is quarantining movies that should be obvious

The ensemble can get fooled when the **vision LLM hallucinates** a confidently-wrong title that outweighs the correct filename signal. Observed during testing: `A Quiet Place 2018 UHD BluRay...` (filename clearly says the answer) got overridden by vision saying "The Last of Us 2023" because the post-apocalyptic keyframes looked similar. Pattern is visible in the `.quorum.json` sidecar — if one candidate has `source: "filename"` with a year and another `source: "vision"` with a confident-but-wrong title, that's the bug.

Two practical workarounds:

1. **For small piles (≤20 files):** write a hardcoded mover like `scripts/manual_movies.py`. Faster than fighting the pipeline. The script writes an `auto-manual-*.log` in the same format as auto, so `quorum undo` still works.
2. **For larger piles:** bump the filename-signal confidence in `src/quorum/signals/filename.py` (movie-year pattern: 0.70 → 0.95) so a clean filename+year wins over vision hallucinations. See the Known Issues section.

### Vision or Whisper hanging for minutes on one file

Whisper can fall into a hallucination loop on noisy/silent audio — spinning for 10+ minutes on a single clip. Fixed in the default config (`beam_size=1`, `condition_on_previous_text=False`), but if you see stalls in your enrich log, those tunings are in `src/quorum/signals/transcript.py`.

Skip Whisper entirely with `quorum enrich --no-whisper` if stalls persist — vision alone produces good descriptions for most home video content.

### ffmpeg errors on files with special characters

Some filenames with colons, curly braces, or ampersands break ffmpeg's subprocess call (`Cats & Dogs_ ...`, `Spy.{2015}....avi`). The pipeline catches these per-file and continues, but those files won't get keyframes/audio. Rename the file before processing if you need them identified.

### Vision model gives generic descriptions ("Footage from 2005")

Usually means ffmpeg didn't actually extract keyframes, and vision got an empty input. Check the enrich log — if `reasoning` says "no visual or audio data provided," your ffmpeg binary is broken or missing. Set `QUORUM_FFMPEG=<path>` to override.

## Environment overrides

- `QUORUM_FFMPEG=<path>` — force a specific ffmpeg binary (useful if system ffmpeg on PATH is outdated)
- `QUORUM_SEPARATE_SYNTHESIS=1` — revert to using `models.text` for synthesis instead of reusing the vision model

## Roadmap

**Shipped**
- [x] `home-videos` mode (date-sort by folder/filename + LLM name cleanup)
- [x] `photos` mode (EXIF + HEIC + Aperture safety)
- [x] `enrich` mode (vision + Whisper → `.nfo` sidecars, resume-friendly)
- [x] `auto` mode (commercial identify via ensemble + TMDB, with quarantine)
- [x] `triage` mode (home vs commercial classifier)
- [x] `undo` for all destructive runs
- [x] Works end-to-end on all-AMD Windows setups

**Planned**
- [ ] `enrich-photos` — face clustering (InsightFace/DirectML) + scene tagging
- [ ] Folder-rename pass: use enrich output to retitle event folders
- [ ] Fingerprint signal (Chromaprint + AcoustID) for theme-song matching
- [ ] OCR signal (PaddleOCR on keyframes for title cards / credits)
- [ ] Watch-folder daemon + Plex library refresh trigger
- [ ] Web UI for the review queue

## License

MIT.
