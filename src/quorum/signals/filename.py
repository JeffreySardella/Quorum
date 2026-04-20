from __future__ import annotations

import re

from .base import Candidate, SignalContext


TV_PATTERNS = [
    re.compile(r"(?P<title>.+?)[\s._-]+[sS](?P<s>\d{1,2})[eE](?P<e>\d{1,2})"),
    re.compile(r"(?P<title>.+?)[\s._-]+(?P<s>\d{1,2})x(?P<e>\d{1,2})"),
]
MOVIE_PATTERN = re.compile(r"(?P<title>.+?)[\s._-]+\(?(?P<year>(?:19|20)\d{2})\)?")

JUNK = re.compile(
    r"\b(?:"
    r"1080p|2160p|720p|480p|4k|"
    r"bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|hdtv|dvdrip|hdrip|"
    r"x264|x265|hevc|h\.?264|h\.?265|av1|"
    r"aac|ac3|dts|ddp5\.?1|ddp|eac3|flac|mp3|"
    r"10bit|8bit|hdr|sdr|remux|proper|repack|extended|uncut|directors\.?cut|"
    r"yify|rarbg|yts|ettv|eztv|galaxy|ntb|sparks|amiable"
    r")\b",
    re.IGNORECASE,
)


def _clean(title: str) -> str:
    t = title.replace(".", " ").replace("_", " ")
    t = JUNK.sub(" ", t)
    return " ".join(t.split()).strip(" -")


class FilenameSignal:
    name = "filename"

    def run(self, ctx: SignalContext) -> list[Candidate]:
        stem = ctx.video.stem
        for pat in TV_PATTERNS:
            m = pat.search(stem)
            if m:
                cleaned = _clean(m.group("title"))
                if not cleaned:
                    continue
                return [Candidate(
                    title=cleaned,
                    kind="tv",
                    season=int(m.group("s")),
                    episode=int(m.group("e")),
                    confidence=0.75,
                    source=self.name,
                    notes=f"pattern={pat.pattern[:30]}",
                )]
        m = MOVIE_PATTERN.search(stem)
        if m:
            cleaned = _clean(m.group("title"))
            if cleaned:
                return [Candidate(
                    title=cleaned,
                    year=int(m.group("year")),
                    kind="movie",
                    confidence=0.70,
                    source=self.name,
                    notes="movie-year pattern",
                )]
        cleaned = _clean(stem)
        if cleaned:
            return [Candidate(title=cleaned, confidence=0.30, source=self.name, notes="bare stem")]
        return []
