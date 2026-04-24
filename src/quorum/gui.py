"""Quorum GUI — a single-window desktop wrapper for all the CLI commands.

Launch with:
    quorum gui
    # or
    python -m quorum.gui

Design notes:
- Wraps the existing `quorum.exe` CLI as a subprocess. No re-implementation
  of the logic; the GUI is purely a friendlier launcher.
- Each tab corresponds to one CLI verb (home-videos, photos, enrich, auto,
  triage) with its options exposed as checkboxes.
- Subprocess output streams line-by-line into a log pane via a background
  thread; GUI updates go through root.after() to stay thread-safe.
- Ollama and GPU status are polled on startup and refreshed on demand.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog

try:
    import customtkinter as ctk
except ImportError as e:
    raise SystemExit(
        "customtkinter is not installed. Run:  pip install customtkinter\n"
        "Or reinstall the project:  pip install -e ."
    ) from e


# Resolve the sibling quorum.exe (both GUI and CLI ship in .venv/Scripts/ on Windows).
QUORUM_EXE = Path(sys.executable).parent / ("quorum.exe" if sys.platform == "win32" else "quorum")


# ── small helpers ─────────────────────────────────────────────────────────
def check_ollama(url: str = "http://127.0.0.1:11434") -> tuple[bool, list[str]]:
    """Returns (reachable, list of installed model names)."""
    try:
        import httpx
        r = httpx.get(f"{url}/api/tags", timeout=2.0)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return True, [n for n in names if n]
    except Exception:
        return False, []


def detect_gpu() -> str:
    """Best-effort GPU name + VRAM — Windows only, returns '' on failure."""
    if sys.platform != "win32":
        return ""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name, AdapterRAM | ConvertTo-Json",
            ],
            capture_output=True, text=True, timeout=6,
        )
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        # Skip the boring virtual/integrated adapters if a real GPU is present.
        def score(item):
            name = (item.get("Name") or "").lower()
            if "parsec" in name or "basic" in name:
                return -1
            if "radeon" in name or "geforce" in name or "rtx" in name or "rx " in name:
                return 10
            return 0
        best = max(data, key=score)
        name = (best.get("Name") or "unknown GPU").strip()
        vram = best.get("AdapterRAM") or 0
        if isinstance(vram, int) and vram > 0:
            return f"{name}  ({vram // (1024**3)} GB reported)"
        return name
    except Exception:
        return ""


# ── main app ──────────────────────────────────────────────────────────────
class QuorumApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Quorum — Local AI Media Organizer")
        self.geometry("980x720")
        self.minsize(820, 640)

        self.active_process: subprocess.Popen | None = None
        self.ollama_url = "http://127.0.0.1:11434"
        self.models: list[str] = []

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_tabs()
        self._build_log_pane()
        self._build_status_bar()
        self._refresh_ollama_status()

    # ── status bar ────────────────────────────────────────────────────────
    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, height=32, corner_radius=0)
        bar.pack(side="bottom", fill="x")
        self.status_label = ctk.CTkLabel(bar, text="checking...", anchor="w")
        self.status_label.pack(side="left", padx=12, pady=4)
        refresh_btn = ctk.CTkButton(bar, text="Refresh", width=80,
                                    command=self._refresh_ollama_status)
        refresh_btn.pack(side="right", padx=8, pady=4)

    def _refresh_ollama_status(self) -> None:
        def worker():
            ok, models = check_ollama(self.ollama_url)
            gpu = detect_gpu()
            self.models = models
            parts = []
            if ok:
                parts.append(f"[Ollama OK — {len(models)} model(s)]")
            else:
                parts.append("[Ollama unreachable — start it with: ollama serve]")
            if gpu:
                parts.append(f"GPU: {gpu}")
            self.after(0, lambda: self.status_label.configure(text="   ".join(parts)))
            # refresh model dropdown in enrich / auto tabs
            self.after(0, self._refresh_model_dropdowns)
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_model_dropdowns(self) -> None:
        values = self.models or ["(none found — is Ollama running?)"]
        for dd in getattr(self, "_model_dropdowns", []):
            dd.configure(values=values)

    # ── tabs ──────────────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self, height=320)
        self.tabs.pack(side="top", fill="x", padx=12, pady=(12, 6))
        self._model_dropdowns: list[ctk.CTkOptionMenu] = []

        for name in ("Home Videos", "Photos", "Enrich", "Movies", "Triage"):
            self.tabs.add(name)

        self._build_home_videos_tab(self.tabs.tab("Home Videos"))
        self._build_photos_tab(self.tabs.tab("Photos"))
        self._build_enrich_tab(self.tabs.tab("Enrich"))
        self._build_movies_tab(self.tabs.tab("Movies"))
        self._build_triage_tab(self.tabs.tab("Triage"))

    # ── per-tab widgets ───────────────────────────────────────────────────
    def _picker(self, parent, row: int, label: str) -> tuple[ctk.CTkEntry, ctk.CTkButton]:
        ctk.CTkLabel(parent, text=label, width=110, anchor="e").grid(row=row, column=0, padx=(8, 6), pady=6, sticky="e")
        entry = ctk.CTkEntry(parent)
        entry.grid(row=row, column=1, padx=6, pady=6, sticky="ew")
        def pick():
            d = filedialog.askdirectory()
            if d:
                entry.delete(0, "end")
                entry.insert(0, d)
        btn = ctk.CTkButton(parent, text="Browse…", width=90, command=pick)
        btn.grid(row=row, column=2, padx=(6, 8), pady=6)
        return entry, btn

    def _build_home_videos_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        src, _ = self._picker(tab, 0, "Source:")
        dst, _ = self._picker(tab, 1, "Destination:")
        dry_var = ctk.BooleanVar()
        nollm_var = ctk.BooleanVar()
        ctk.CTkCheckBox(tab, text="Dry-run (simulate only)", variable=dry_var).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ctk.CTkCheckBox(tab, text="Skip LLM folder-name cleanup (--no-llm, much faster)",
                        variable=nollm_var).grid(row=3, column=1, sticky="w", padx=6, pady=2)
        hint = ("Organizes family/home videos by folder-name year + filename dates. "
                "Destination gets a Home Videos/YYYY/... tree.")
        ctk.CTkLabel(tab, text=hint, wraplength=780, justify="left",
                     text_color=("gray30", "gray70")).grid(row=4, column=0, columnspan=3, padx=10, pady=(8, 4), sticky="w")

        def build_args():
            args = [str(QUORUM_EXE), "home-videos"]
            if dry_var.get(): args.append("--dry-run")
            if nollm_var.get(): args.append("--no-llm")
            args.extend([src.get(), dst.get()])
            return args

        self._add_run_row(tab, 5, build_args, requires=[src, dst])

    def _build_photos_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        src, _ = self._picker(tab, 0, "Source:")
        dst, _ = self._picker(tab, 1, "Destination:")
        dry_var = ctk.BooleanVar()
        ctk.CTkCheckBox(tab, text="Dry-run", variable=dry_var).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        hint = ("Organizes photos by EXIF capture date into Photos/YYYY/YYYY-MM-DD/. "
                "Hard-skips Aperture / iPhoto libraries automatically.")
        ctk.CTkLabel(tab, text=hint, wraplength=780, justify="left",
                     text_color=("gray30", "gray70")).grid(row=3, column=0, columnspan=3, padx=10, pady=(8, 4), sticky="w")

        def build_args():
            args = [str(QUORUM_EXE), "photos"]
            if dry_var.get(): args.append("--dry-run")
            args.extend([src.get(), dst.get()])
            return args

        self._add_run_row(tab, 4, build_args, requires=[src, dst])

    def _build_enrich_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        root, _ = self._picker(tab, 0, "Organized root:")
        force_var = ctk.BooleanVar()
        nowhisper_var = ctk.BooleanVar(value=True)
        norename_var = ctk.BooleanVar()
        ctk.CTkCheckBox(tab, text="Skip Whisper transcription (2-3× faster, vision-only descriptions)",
                        variable=nowhisper_var).grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ctk.CTkCheckBox(tab, text="Force regenerate existing .nfo files",
                        variable=force_var).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ctk.CTkCheckBox(tab, text="Skip automatic folder rename after enrichment (--no-rename)",
                        variable=norename_var).grid(row=3, column=1, sticky="w", padx=6, pady=2)
        hint = ("Walks every video under the root, extracts keyframes + audio, runs "
                "vision + Whisper, writes a Plex .nfo sidecar with a real title + plot. "
                "Resume-friendly — skips any video that already has a .nfo. "
                "After enrichment, automatically renames fully enriched event folders.")
        ctk.CTkLabel(tab, text=hint, wraplength=780, justify="left",
                     text_color=("gray30", "gray70")).grid(row=4, column=0, columnspan=3, padx=10, pady=(8, 4), sticky="w")

        def build_args():
            args = [str(QUORUM_EXE), "enrich"]
            if force_var.get(): args.append("--force")
            if nowhisper_var.get(): args.append("--no-whisper")
            if norename_var.get(): args.append("--no-rename")
            args.append(root.get())
            return args

        self._add_run_row(tab, 5, build_args, requires=[root])

    def _build_movies_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        src, _ = self._picker(tab, 0, "Source:")
        dst, _ = self._picker(tab, 1, "Destination:")
        dry_var = ctk.BooleanVar()
        ctk.CTkCheckBox(tab, text="Dry-run", variable=dry_var).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        hint = ("Identifies commercial movies against TMDB and moves them into a "
                "Movies/Title (Year)/ layout. Needs TMDB_API_KEY in .env for best "
                "results.  Vision-heavy — can mis-label rips; review the log before trusting.")
        ctk.CTkLabel(tab, text=hint, wraplength=780, justify="left",
                     text_color=("gray30", "gray70")).grid(row=3, column=0, columnspan=3, padx=10, pady=(8, 4), sticky="w")

        def build_args():
            args = [str(QUORUM_EXE), "auto"]
            if dry_var.get(): args.append("--dry-run")
            args.extend([src.get(), dst.get()])
            return args

        self._add_run_row(tab, 4, build_args, requires=[src, dst])

    def _build_triage_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        src, _ = self._picker(tab, 0, "Source (mixed home + commercial):")
        hint = ("Classifies every video filename in the folder as 'home' vs 'commercial' "
                "using the text LLM. Writes two manifest files you can then feed to the "
                "appropriate tool. Nothing moves — classify only.")
        ctk.CTkLabel(tab, text=hint, wraplength=780, justify="left",
                     text_color=("gray30", "gray70")).grid(row=2, column=0, columnspan=3, padx=10, pady=(8, 4), sticky="w")

        def build_args():
            return [str(QUORUM_EXE), "triage", src.get()]

        self._add_run_row(tab, 3, build_args, requires=[src])

    # ── run/stop + log ────────────────────────────────────────────────────
    def _add_run_row(self, tab, row: int, build_args, requires: list[ctk.CTkEntry]) -> None:
        frame = ctk.CTkFrame(tab, fg_color="transparent")
        frame.grid(row=row, column=0, columnspan=3, sticky="e", padx=8, pady=(6, 8))
        run_btn = ctk.CTkButton(frame, text="▶ Start", width=110, height=34,
                                font=("", 14, "bold"))
        stop_btn = ctk.CTkButton(frame, text="■ Stop", width=90, height=34,
                                 fg_color="#a03030", hover_color="#c24040", state="disabled")

        def on_start():
            if any(not e.get().strip() for e in requires):
                self.log_text.insert("end", "⚠ Please fill in all required paths.\n")
                self.log_text.see("end")
                return
            args = build_args()
            self.log_text.insert("end", f"\n$ {' '.join(_quote(a) for a in args)}\n")
            self.log_text.see("end")
            run_btn.configure(state="disabled")
            stop_btn.configure(state="normal")
            self._run_subprocess(args, on_done=lambda code: (
                run_btn.configure(state="normal"),
                stop_btn.configure(state="disabled"),
                self.log_text.insert("end", f"\n[exit {code}]\n"),
                self.log_text.see("end"),
            ))

        def on_stop():
            if self.active_process and self.active_process.poll() is None:
                self.active_process.terminate()
                self.log_text.insert("end", "\n[terminating...]\n")
                self.log_text.see("end")

        run_btn.configure(command=on_start)
        stop_btn.configure(command=on_stop)
        run_btn.pack(side="right", padx=6)
        stop_btn.pack(side="right", padx=6)

    def _build_log_pane(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.pack(side="top", fill="both", expand=True, padx=12, pady=(6, 12))
        header = ctk.CTkFrame(frame, fg_color="transparent", height=28)
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Output", anchor="w",
                     font=("", 13, "bold")).pack(side="left", padx=8)
        clear_btn = ctk.CTkButton(header, text="Clear", width=70,
                                  command=lambda: self.log_text.delete("1.0", "end"))
        clear_btn.pack(side="right", padx=8, pady=2)
        self.log_text = ctk.CTkTextbox(frame, wrap="word", font=("Consolas", 11))
        self.log_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    def _run_subprocess(self, args: list[str], on_done) -> None:
        def worker():
            try:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                )
            except FileNotFoundError as e:
                self.after(0, lambda: self.log_text.insert("end", f"⚠ {e}\n"))
                self.after(0, on_done, -1)
                return
            self.active_process = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                self.after(0, lambda l=line: (self.log_text.insert("end", l), self.log_text.see("end")))
            proc.wait()
            self.active_process = None
            self.after(0, on_done, proc.returncode)

        threading.Thread(target=worker, daemon=True).start()


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s else s


def main() -> None:
    app = QuorumApp()
    app.mainloop()


if __name__ == "__main__":
    main()
