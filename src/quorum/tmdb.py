from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class TMDBMatch:
    kind: str
    id: int
    title: str
    year: int | None
    overview: str
    popularity: float


class TMDB:
    BASE = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "api_key": self.api_key}
        resp = self.client.get(f"{self.BASE}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def search_multi(self, query: str, year: int | None = None) -> list[TMDBMatch]:
        if not self.api_key or not query.strip():
            return []
        params: dict = {"query": query, "include_adult": "false"}
        if year:
            params["year"] = year
        data = self._get("/search/multi", params)
        matches: list[TMDBMatch] = []
        for item in data.get("results", []):
            kind = item.get("media_type")
            if kind not in ("movie", "tv"):
                continue
            title = item.get("title") or item.get("name") or ""
            date = item.get("release_date") or item.get("first_air_date") or ""
            yr: int | None = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            matches.append(
                TMDBMatch(
                    kind=kind,
                    id=item["id"],
                    title=title,
                    year=yr,
                    overview=item.get("overview", ""),
                    popularity=float(item.get("popularity", 0)),
                )
            )
        return matches
