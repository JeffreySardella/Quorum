from __future__ import annotations

from pathlib import Path

from .config import Settings
from .db import QuorumDB


class SearchEngine:
    """Unified search: tries vector search first, falls back to FTS5."""

    def __init__(self, settings: Settings, db: QuorumDB) -> None:
        self.settings = settings
        self.db = db
        self._ollama = None

    def _get_ollama(self):
        if self._ollama is None:
            from .ollama_client import OllamaClient

            self._ollama = OllamaClient(self.settings.ollama_url)
        return self._ollama

    def search(
        self,
        query: str,
        media_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search with vector similarity, fall back to FTS5 keyword search."""
        # Try vector search first
        try:
            ollama = self._get_ollama()
            vector = ollama.embed(self.settings.models.vision, query)
            results = self.db.search_vector(
                vector,
                media_type=media_type,
                after=after,
                before=before,
                limit=limit,
            )
            if results:
                for r in results:
                    r["search_method"] = "vector"
                return results
        except Exception:
            pass

        # Fall back to FTS5 keyword search
        results = self.db.search_text(
            query,
            media_type=media_type,
            after=after,
            before=before,
            limit=limit,
        )
        for r in results:
            r["search_method"] = "keyword"
        return results

    def index_all(self) -> dict[str, int]:
        """Build search indexes for all media."""
        text_count = self.db.reindex_all()

        vec_count = 0
        try:
            ollama = self._get_ollama()
            for row in self.db.conn.execute("SELECT id FROM media").fetchall():
                media_id = row[0]
                parts = []
                for meta in self.db.get_metadata(media_id):
                    if meta["value"]:
                        parts.append(meta["value"])
                for tag in self.db.get_tags(media_id):
                    if tag["value"]:
                        parts.append(tag["value"])
                if not parts:
                    continue
                text = " ".join(parts)
                try:
                    vector = ollama.embed(self.settings.models.vision, text)
                    self.db.index_media_vector(media_id, vector)
                    vec_count += 1
                except Exception:
                    continue
        except Exception:
            pass

        return {"text_indexed": text_count, "vector_indexed": vec_count}

    def close(self) -> None:
        if self._ollama is not None:
            self._ollama.close()
            self._ollama = None
