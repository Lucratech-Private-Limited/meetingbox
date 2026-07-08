"""Summarize MeetingBox voice reliability metrics from device logs.

Usage:
    python scripts/voice_diagnostics_report.py /path/to/device.log

The app emits structured lines like:
    VOICE_EVENT {"event": "barge_in_detected", ...}

This script is read-only. It gives a quick repeated-trial report for wake,
interruption, audio route, and transcript checks without connecting to the
device or mutating app state.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


def _iter_voice_events(path: Path):
    marker = "VOICE_EVENT "
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            idx = line.find(marker)
            if idx < 0:
                continue
            raw = line[idx + len(marker) :].strip()
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("event"), str):
                yield event


def _latencies(events: list[dict[str, Any]], start: str, end: str) -> list[float]:
    values: list[float] = []
    pending: float | None = None
    for event in events:
        name = event.get("event")
        ts = event.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if name == start:
            pending = float(ts)
        elif name == end and pending is not None:
            values.append(max(0.0, float(ts) - pending))
            pending = None
    return values


def _fmt_seconds(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"avg={mean(values):.3f}s max={max(values):.3f}s n={len(values)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Device UI log file to summarize")
    args = parser.parse_args()

    events = list(_iter_voice_events(args.log))
    counts = Counter(event["event"] for event in events)
    transcripts = [
        str(event.get("text") or "").strip()
        for event in events
        if event.get("event") == "final_transcript"
    ]
    routes = [
        event
        for event in events
        if event.get("event") in ("audio_route", "session_update_sent")
    ]

    print(f"voice_events={len(events)}")
    for name, count in sorted(counts.items()):
        print(f"{name}={count}")
    print(f"final_transcripts={len(transcripts)}")
    print(f"empty_final_transcripts={sum(1 for text in transcripts if not text)}")
    print(
        "barge_to_cancel_latency="
        f"{_fmt_seconds(_latencies(events, 'barge_in_detected', 'response_cancel_sent'))}"
    )
    print(
        "speech_to_stop_latency="
        f"{_fmt_seconds(_latencies(events, 'speech_started', 'aplay_killed'))}"
    )
    for route in routes[-3:]:
        print(f"recent_{route.get('event')}={json.dumps(route, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
