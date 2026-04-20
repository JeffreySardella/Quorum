from __future__ import annotations

import base64
from pathlib import Path

import httpx


class OllamaClient:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.client = httpx.Client(timeout=600.0)

    def close(self) -> None:
        self.client.close()

    def generate(self, model: str, prompt: str, images: list[Path] | None = None) -> str:
        payload: dict = {"model": model, "prompt": prompt, "stream": False}
        if images:
            payload["images"] = [
                base64.b64encode(p.read_bytes()).decode("ascii") for p in images
            ]
        resp = self.client.post(f"{self.url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")
