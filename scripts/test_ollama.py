"""Quick end-to-end sanity check for Ollama connectivity + the text model
used by the home-videos folder-name parser. Run from the repo root:

    python scripts\test_ollama.py
"""

from __future__ import annotations

import sys
import time

from quorum.ollama_client import OllamaClient


def main() -> int:
    url = "http://127.0.0.1:11434"
    model = "gemma4:31b"
    print(f"→ posting to {url} with model {model!r} ...")

    client = OllamaClient(url)
    t0 = time.time()
    try:
        reply = client.generate(model, "Reply with exactly one word: hello")
    except Exception as e:
        print(f"✗ call failed: {type(e).__name__}: {e}")
        return 1
    finally:
        client.close()

    elapsed = time.time() - t0
    print(f"✓ {elapsed:.1f}s, response: {reply[:200]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
