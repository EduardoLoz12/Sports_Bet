"""Vercel entrypoint — re-exports the Flask app from dashboard/app.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from app import app  # noqa: E402
