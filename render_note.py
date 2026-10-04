"""Render QUANT_NOTE.md to quant_note.pdf (Markdown -> HTML with note_style.css -> headless Chrome).

    uv run --no-project --with markdown python render_note.py
Set CHROME=/path/to/chrome if Chrome is not at the macOS default location.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import markdown

TRACK = Path(__file__).resolve().parent
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

body = markdown.markdown((TRACK / "QUANT_NOTE.md").read_text(), extensions=["tables", "md_in_html"])
css = (TRACK / "note_style.css").read_text()
html = TRACK / "quant_note.html"
html.write_text(f'<!doctype html><html><head><meta charset="utf-8"><title>Ghost Whales in Biotech Options</title>'
                f"<style>{css}</style></head><body>{body}</body></html>")
subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                f"--print-to-pdf={TRACK / 'quant_note.pdf'}", html.as_uri()], check=True, capture_output=True)
html.unlink()
print(TRACK / "quant_note.pdf")
