# Quorum — Windows + AMD setup helper.
# From repo root:  .\scripts\setup-windows-amd.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== Quorum setup (Windows + AMD) ===" -ForegroundColor Cyan

# --- Python ---------------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install Python 3.11+ first:" -ForegroundColor Red
    Write-Host "  winget install Python.Python.3.12"
    exit 1
}

# --- uv (optional, fast) --------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (fast Python installer)..." -ForegroundColor Yellow
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

# --- Python deps (ffmpeg + whisper are bundled via pip) -------------------
Write-Host "Installing Python deps..." -ForegroundColor Cyan
Write-Host "  (brings in faster-whisper + imageio-ffmpeg — no system ffmpeg install needed)" -ForegroundColor DarkGray

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv venv
    uv pip install -e .
} else {
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e .
}

# --- Ollama ---------------------------------------------------------------
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Ollama not found. Install from https://ollama.com/download" -ForegroundColor Yellow
    Write-Host "(If you use the AMD AI Bundle, Ollama is already installed there.)"
} else {
    Write-Host ""
    Write-Host "Pulling recommended vision model..." -ForegroundColor Cyan
    ollama pull mistral-small3.2:latest
    Write-Host "Done. Larger / better options you can pull later:" -ForegroundColor DarkGray
    Write-Host "  ollama pull qwen3-vl:32b   # stronger vision" -ForegroundColor DarkGray
    Write-Host "  ollama pull gemma4:31b     # text reasoning for the transcript signal" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== Setup done. ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. copy .env.example .env               # (optional) add TMDB_API_KEY for metadata boost"
Write-Host "  2. copy config.example.toml config.toml"
Write-Host "  3. quorum scan 'E:\path\to\messy\videos'"
Write-Host "  4. cat .\review.jsonl                   # inspect proposals"
Write-Host "  5. quorum apply --dry-run               # preview renames"
Write-Host "  6. quorum apply                         # do it"
Write-Host ""
Write-Host "Optional: for GPU-accelerated Whisper on AMD, install the Vulkan build of" -ForegroundColor DarkGray
Write-Host "whisper.cpp (see README) — flips transcript from CPU to ~10x faster." -ForegroundColor DarkGray
