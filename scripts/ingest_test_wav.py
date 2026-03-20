#!/usr/bin/env python3
"""
Feed a test WAV file into the MeetingBox pipeline (transcription -> summary).
Use this to exercise the full path without a microphone.

Usage:
  python scripts/ingest_test_wav.py path/to/audio.wav
  python scripts/ingest_test_wav.py path/to/audio.wav --base http://localhost:8000

Requires: requests (pip install requests)
"""

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a WAV file into MeetingBox pipeline")
    parser.add_argument("wav", type=Path, help="Path to .wav file")
    parser.add_argument("--base", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()

    path = args.wav.resolve()
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    if path.suffix.lower() != ".wav":
        print("Warning: file is not .wav; server may reject it.", file=sys.stderr)

    try:
        import requests
    except ImportError:
        print("Error: install requests: pip install requests", file=sys.stderr)
        sys.exit(1)

    url = f"{args.base.rstrip('/')}/api/meetings/test/ingest-wav"
    with path.open("rb") as f:
        files = {"file": (path.name, f, "audio/wav")}
        r = requests.post(url, files=files, timeout=60)
    r.raise_for_status()
    data = r.json()
    print(f"Ingested: session_id={data['session_id']}")
    print(f"Open: {args.base}/meeting/{data['session_id']} (after transcription and summary complete)")


if __name__ == "__main__":
    main()
