import sys

# Force UTF-8 stdout/stderr on Windows so non-cp1252 filenames (Korean,
# emoji, smart quotes, etc.) in progress messages don't crash the pipeline
# with UnicodeEncodeError.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from .cli import app

if __name__ == "__main__":
    app()
