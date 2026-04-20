"""One-shot mover for the 10 commercial movies in E:\\Jeffrey\\Movies\\.

Hard-coded titles since auto-mode is getting confused by vision LLM
hallucinations on release-group-named rips. For this small batch the
manual map is faster + more accurate than the 30-minute code fix.

Run:  python scripts/manual_movies.py

Writes moves into the same auto-*.log format so `quorum undo` works.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


SRC = Path(r"E:\Jeffrey\_Organized\_quarantine")
DEST = Path(r"E:\Jeffrey\_Organized\Movies")
LOG_PATH = Path(r"E:\Jeffrey\_Organized") / f"auto-manual-{datetime.now():%Y%m%d-%H%M%S}.log"


# Filename (case-sensitive) → (Title, Year)
MAPPING = {
    "2012.m4v":                                                            ("2012", 2009),
    "A Quiet Place 2018 UHD BluRay 2160p DDP 7 1 DV HDR x265-hallowed.mkv": ("A Quiet Place", 2018),
    "Cats & Dogs_ The Revenge of Kitty Galore.m4v":                        ("Cats & Dogs - The Revenge of Kitty Galore", 2010),
    "Jurassic.World.2015.HD-TS.XVID.AC3.HQ.Hive-CM8.avi":                  ("Jurassic World", 2015),
    "Just Go With It (1080p HD).m4v":                                      ("Just Go with It", 2011),
    "Killer Bean Forever 4K - Official FULL MOVIE.mp4":                    ("Killer Bean Forever", 2009),
    "Little Fockers.m4v":                                                  ("Little Fockers", 2010),
    "Marmaduke.m4v":                                                       ("Marmaduke", 2010),
    "Spy.{2015}.NEWSOURCE.HQCAM.XVID.MP3.MRG.avi":                         ("Spy", 2015),
    "The Purge 2 Anarchy 2014 READNFO HDRip XviD-HELLRAZ0R.avi":           ("The Purge - Anarchy", 2014),
}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    moved = missing = collided = 0
    with LOG_PATH.open("w", encoding="utf-8") as log_f:
        for filename, (title, year) in MAPPING.items():
            src = SRC / filename
            if not src.exists():
                print(f"MISSING  {filename}")
                missing += 1
                continue

            folder = f"{title} ({year})"
            ext = src.suffix.lower()
            dst = DEST / folder / f"{folder}{ext}"

            if dst.exists():
                print(f"EXISTS   {dst}")
                collided += 1
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            print(f"OK       {filename}  ->  {dst}")
            log_f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "src": str(src),
                "dst": str(dst),
                "action": "move",
                "kind": "movie",
                "title": title,
                "year": year,
            }) + "\n")
            moved += 1

    print()
    print(f"moved={moved}  missing={missing}  collision={collided}")
    print(f"log: {LOG_PATH}")
    print(f"undo: quorum undo \"{LOG_PATH}\"")


if __name__ == "__main__":
    main()
