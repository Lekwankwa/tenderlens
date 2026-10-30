"""Builds the standalone, styled privacy policy page from PRIVACY.md and says where it is served.

The page is written into the upload widget's component folder because Streamlit already serves every file
there at /component/<component name>/ (same trick as signed-out.html - plain static serving refuses .html),
so the same relative URL works on localhost and on Cloud Run. It is rebuilt whenever PRIVACY.md is newer than
the generated file, so editing the markdown is all it takes.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

log = logging.getLogger("tenderlens.privacy_page")

_ROOT = Path(__file__).resolve().parents[1]
SOURCE = _ROOT / "PRIVACY.md"
OUTPUT = Path(__file__).resolve().parent / "static" / "upload_widget" / "privacy.html"

_PLACEHOLDER = "<p>The privacy policy is temporarily unavailable. Please contact the TenderLens team.</p>"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy policy - TenderLens</title>
<style>
  :root {{ color-scheme: light dark; --bg: #fff; --fg: #1f2328; --muted: #5b636b; --line: #d0d7de; --accent: #1a5fb4; --code: #f3f4f6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0e1117; --fg: #e6e8eb; --muted: #9aa3ad; --line: #30363d; --accent: #6ea8fe; --code: #1a1f27; }} }}
  body {{ margin: 0; background: var(--bg); color: var(--fg); font-family: -apple-system, system-ui, "Segoe UI", Roboto, sans-serif; line-height: 1.65; }}
  main {{ max-width: 800px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 1.8rem; letter-spacing: -.02em; margin: 0 0 8px; }}
  h2 {{ font-size: 1.25rem; margin: 32px 0 8px; padding-top: 16px; border-top: 1px solid var(--line); }}
  h3 {{ font-size: 1.05rem; margin: 22px 0 6px; }}
  p, li {{ margin: 0 0 12px; }}
  a {{ color: var(--accent); }}
  code {{ background: var(--code); padding: 1px 5px; border-radius: 4px; font-size: .9em; }}
  table {{ border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }}
  th, td {{ border: 1px solid var(--line); padding: 6px 10px; text-align: left; }}
  .back {{ font-size: .9rem; color: var(--muted); }}
</style>
</head>
<body>
<main>
<p class="back">TenderLens</p>
{body}
</main>
</body>
</html>
"""


def _to_html(text: str) -> str:
    try:
        import markdown
        return markdown.markdown(text, extensions=["tables", "sane_lists"])
    except ImportError:
        return f"<pre style='white-space:pre-wrap'>{html.escape(text)}</pre>"  # readable, just unstyled


def build(source: Path = SOURCE, output: Path = OUTPUT, force: bool = False) -> bool:
    """Write the page if it is missing or older than `source`. Returns True when a usable page exists. A missing
    or unreadable source gets a placeholder page rather than an exception."""
    try:
        if not force and output.is_file() and source.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
            return True
        try:
            body = _to_html(source.read_text(encoding="utf-8"))
        except OSError as err:
            log.warning("privacy source unavailable: %s", err)
            body = _PLACEHOLDER
        output.write_text(_TEMPLATE.format(body=body), encoding="utf-8")
        return True
    except OSError as err:  # read-only disk etc.: the app must still start
        log.warning("could not write the privacy page: %s", err)
        return output.is_file()
