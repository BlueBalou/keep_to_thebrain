#!/usr/bin/env python3
"""
Google Keep → TheBrain importer
One thought per Keep note, with images attached to that thought.

Usage:
    python keep_to_thebrain.py --export-dir /path/to/keep/export
    python keep_to_thebrain.py --export-dir /path/to/keep/export --brain-id <uuid>

Requirements:
    pip install requests

The export folder should contain:
  - *.json  (one per note)
  - *.png / *.jpg etc. (image attachments)
  - *.html  (ignored — redundant)
"""

import argparse
import json
import os
import re
import sys
import time
import logging
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE   = "https://api.bra.in"
API_KEY    = "d9a749a694b07565590596157bac7b4ea6a43d21ce24938c95f5e0354bdf3336"
DEFAULT_BRAIN_ID = "18faa17b-ee52-4cde-92d8-2f26f83d6956"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept":        "application/json",
}

# Seconds to wait between API calls — stay well under rate limits
RATE_DELAY = 0.5

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("import_log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def api_post(path: str, **kwargs) -> requests.Response:
    """POST to TheBrain API, raise on HTTP error."""
    url = f"{API_BASE}{path}"
    r = requests.post(url, headers=HEADERS, **kwargs)
    if not r.ok:
        log.error(f"  POST {url} -> {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return r


def api_patch(path: str, **kwargs) -> requests.Response:
    url = f"{API_BASE}{path}"
    r = requests.patch(url, headers=HEADERS, **kwargs)
    if not r.ok:
        log.error(f"  PATCH {url} -> {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return r


def probe_api(brain_id: str) -> None:
    """GET the brain to verify the API key and brain ID are valid before importing."""
    url = f"{API_BASE}/brains/{brain_id}"
    r = requests.get(url, headers=HEADERS)
    if r.ok:
        name = r.json().get("name", "(unnamed)")
        log.info(f"API connection OK - brain name: '{name}'")
    else:
        log.error(f"API probe failed {r.status_code}: {r.text[:500]}")
        log.error("Check your API key and brain ID. Aborting.")
        sys.exit(1)


def dot_to_underscore(filename: str) -> str:
    """
    Keep export JSON lists attachment paths with dots as separators
    (e.g. 1585673280194.1658710956.png) but the actual files on disk
    use underscores (1585673280194_1658710956.png).
    This converts the JSON value to the on-disk filename.
    """
    stem, _, ext = filename.rpartition(".")
    # Replace dots in the stem with underscores
    stem_fixed = stem.replace(".", "_")
    return f"{stem_fixed}.{ext}"


def clean_text(text: str) -> str:
    """Strip the leading dash-only lines Keep sometimes inserts."""
    lines = text.splitlines()
    # Drop lines that are only dashes or whitespace
    lines = [l for l in lines if l.strip() not in ("-", "")]
    return "\n".join(lines).strip()


# ── Core import logic ─────────────────────────────────────────────────────────

def create_thought(title: str, brain_id: str) -> str:
    """Create a new thought and return its ID."""
    payload = {
        "name":     title or "Untitled",
        "kind":     1,   # 1 = Normal thought
    }
    r = api_post(
        f"/thoughts/{brain_id}",
        json=payload,
    )
    thought_id = r.json()["id"]
    log.info(f"  Created thought '{title}' → {thought_id}")
    return thought_id


def set_note(thought_id: str, markdown: str, brain_id: str) -> None:
    """Set the markdown note on a thought."""
    if not markdown:
        return
    api_post(
        f"/notes/{brain_id}/{thought_id}/update",
        json={"markdown": markdown},
    )
    log.info(f"  Note set ({len(markdown)} chars)")


def attach_file(thought_id: str, file_path: Path, brain_id: str) -> None:
    """Upload a file as an attachment to a thought."""
    mime = "image/png" if file_path.suffix.lower() == ".png" else "application/octet-stream"
    with open(file_path, "rb") as f:
        r = api_post(
            f"/attachments/{brain_id}/{thought_id}/file",
            files={"file": (file_path.name, f, mime)},
        )
    log.info(f"  Attached {file_path.name}")


def import_note(json_path: Path, export_dir: Path, brain_id: str) -> bool:
    """
    Import a single Keep note JSON as one TheBrain thought.
    Returns True on success, False on failure.
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Cannot read {json_path.name}: {e}")
        return False

    # Skip trashed notes
    if data.get("isTrashed", False):
        log.info(f"Skipping trashed note: {json_path.name}")
        return True

    title    = data.get("title", "").strip() or json_path.stem
    raw_text = data.get("textContent", "")
    note_md  = clean_text(raw_text)

    log.info(f"\nImporting: {json_path.name}  (title='{title}')")

    try:
        thought_id = create_thought(title, brain_id)
        time.sleep(RATE_DELAY)

        if note_md:
            set_note(thought_id, note_md, brain_id)
            time.sleep(RATE_DELAY)

        for att in data.get("attachments", []):
            raw_name   = att.get("filePath", "")
            local_name = dot_to_underscore(raw_name)
            file_path  = export_dir / local_name

            if not file_path.exists():
                # Fallback: try original name
                file_path = export_dir / raw_name

            if not file_path.exists():
                log.warning(f"  Attachment not found: {local_name} (or {raw_name})")
                continue

            attach_file(thought_id, file_path, brain_id)
            time.sleep(RATE_DELAY)

        return True

    except requests.HTTPError as e:
        log.error(f"  HTTP error: {e.response.status_code} — {e.response.text}")
        return False
    except Exception as e:
        log.error(f"  Unexpected error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import Google Keep export into TheBrain")
    parser.add_argument("--export-dir", required=True, help="Path to the Keep export folder")
    parser.add_argument("--brain-id", default=DEFAULT_BRAIN_ID, help="TheBrain brain UUID (default: hardcoded value)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, make no API calls")
    args = parser.parse_args()

    brain_id   = args.brain_id
    export_dir = Path(args.export_dir).expanduser().resolve()
    if not export_dir.is_dir():
        log.error(f"Export directory not found: {export_dir}")
        sys.exit(1)

    json_files = sorted(export_dir.glob("*.json"))
    if not json_files:
        log.error("No .json files found in export directory.")
        sys.exit(1)

    log.info(f"Found {len(json_files)} JSON file(s) in {export_dir}")
    log.info(f"Target brain: {brain_id}")

    probe_api(brain_id)

    if args.dry_run:
        log.info("DRY RUN — no API calls will be made")
        for jf in json_files:
            data = json.loads(jf.read_text(encoding="utf-8"))
            title = data.get("title", "").strip() or jf.stem
            atts  = [dot_to_underscore(a["filePath"]) for a in data.get("attachments", [])]
            trashed = data.get("isTrashed", False)
            log.info(f"  {'[TRASH] ' if trashed else ''}{jf.name} → '{title}' | {len(atts)} attachment(s)")
        return

    ok, fail = 0, 0
    for jf in json_files:
        success = import_note(jf, export_dir, brain_id)
        if success:
            ok += 1
        else:
            fail += 1

    log.info(f"\n{'─'*50}")
    log.info(f"Done. Success: {ok}  Failed: {fail}")
    if fail:
        log.info("Check import_log.txt for details on failures.")


if __name__ == "__main__":
    main()
