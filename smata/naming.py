"""Naming of downloaded document files."""
from __future__ import annotations

import os
import re
import time
from urllib.parse import unquote, urlparse


def main_filename(doc: dict) -> str:
    """The name a document's main file is saved under: <date>-<category>-<id>.<ext>."""
    ext = doc.get("file_extension") or url_extension((doc.get("links") or {}).get("file_url") or "", "bin")
    category = safe_name(doc.get("category") or "uncategorised")
    return f"{epoch_date(doc.get('added_date') or doc.get('created_at'))}-{category}-{doc['id']}.{ext}"


def epoch_date(ts) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else ""


def safe_name(text: str) -> str:
    return re.sub(r'[/\\\x00-\x1f\s]+', "-", text.strip()).strip("-") or "unnamed"


def url_extension(url: str, fallback: str) -> str:
    ext = os.path.splitext(unquote(urlparse(url).path))[1].lstrip(".")
    return ext if ext and len(ext) <= 8 else fallback
