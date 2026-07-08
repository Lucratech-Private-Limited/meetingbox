"""
Recording intelligence — write side.

This module owns everything needed to make a Note or Meeting *findable*:

1. Persisting the rich context a user gives **before** and **after** a
   recording (intent, mentioned people/projects/events/…), captured by the
   voice layer and staged through the recording API.
2. Extracting entities + keywords from the transcript and summary after the
   meeting agent runs.
3. Building the searchable artifacts that power ranked retrieval:
     * a denormalized lowercase ``search_blob`` for cheap metadata scoring,
     * an FTS5 row (``recordings_fts``) for fast keyword/transcript matching,
     * a dense embedding (``recording_embeddings``) for semantic ranking.

Everything here is best-effort and degrades gracefully: if no embedding API
key is configured, semantic ranking is simply skipped and keyword + metadata
ranking still works. If FTS5 is unavailable, search falls back to LIKE.

The read side lives in ``services/recording_search.py``.
"""

from __future__ import annotations

import array
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from database import get_connection

logger = logging.getLogger("meetingbox.recording_store")

EMBEDDING_MODEL = os.getenv("MEETINGBOX_EMBEDDING_MODEL", "text-embedding-3-small")

# Metadata fields that hold JSON string-arrays in recording_context.
LIST_FIELDS = (
    "intent_tags",
    "context_tags",
    "referenced_people",
    "referenced_projects",
    "referenced_events",
    "referenced_organizations",
    "referenced_locations",
    "referenced_topics",
    "keywords",
    "future_reference_tags",
)

TEXT_FIELDS = ("session_intent", "pre_context", "post_context")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now().isoformat()


def _norm_str(v: Any) -> str:
    return str(v or "").strip()


def _as_list(v: Any) -> list[str]:
    """Coerce arbitrary input into a clean, de-duplicated list of strings."""
    out: list[str] = []
    if v is None:
        return out
    if isinstance(v, str):
        # Allow comma / newline separated strings too.
        raw_items: Iterable[Any] = re.split(r"[\n,;]+", v) if v.strip() else []
    elif isinstance(v, (list, tuple, set)):
        raw_items = v
    else:
        raw_items = [v]
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            s = _norm_str(item.get("name") or item.get("value") or item.get("text"))
        else:
            s = _norm_str(item)
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _load_json_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return _as_list(raw)
    try:
        return _as_list(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return _as_list(raw)


# --------------------------------------------------------------------------- #
# Context persistence
# --------------------------------------------------------------------------- #
def get_recording_context(meeting_id: str) -> dict[str, Any]:
    """Return the stored context row for a recording (empty defaults if none)."""
    mid = _norm_str(meeting_id)
    base = _empty_context(mid)
    if not mid:
        return base
    conn = get_connection()
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recording_context WHERE meeting_id = ?", (mid,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return base
    out: dict[str, Any] = {"meeting_id": mid}
    out["session_type"] = _norm_str(row.get("session_type")) or "meeting"
    for f in TEXT_FIELDS:
        out[f] = _norm_str(row.get(f))
    for f in LIST_FIELDS:
        out[f] = _load_json_list(row.get(f))
    out["search_blob"] = _norm_str(row.get("search_blob"))
    out["created_at"] = row.get("created_at")
    out["updated_at"] = row.get("updated_at")
    return out


def _empty_context(meeting_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"meeting_id": meeting_id, "session_type": "meeting"}
    for f in TEXT_FIELDS:
        out[f] = ""
    for f in LIST_FIELDS:
        out[f] = []
    out["search_blob"] = ""
    return out


def save_recording_context(
    meeting_id: str,
    *,
    session_type: Optional[str] = None,
    merge: bool = True,
    **fields: Any,
) -> dict[str, Any]:
    """
    Upsert context for a recording.

    When ``merge`` is True (default) list fields are unioned with what's already
    stored and text fields are appended (so pre- and post-recording context can
    be captured at different times without clobbering each other). When False,
    the provided fields replace existing values.

    Accepts any of: session_intent, pre_context, post_context, and the LIST_FIELDS.
    """
    mid = _norm_str(meeting_id)
    if not mid:
        return _empty_context("")

    existing = get_recording_context(mid) if merge else _empty_context(mid)
    merged = dict(existing)

    if session_type is not None and _norm_str(session_type):
        merged["session_type"] = _norm_str(session_type).lower()

    for f in TEXT_FIELDS:
        if f not in fields:
            continue
        incoming = _norm_str(fields[f])
        if not incoming:
            continue
        if merge and merged.get(f) and incoming not in merged[f]:
            merged[f] = (merged[f] + "\n" + incoming).strip()
        else:
            merged[f] = incoming

    for f in LIST_FIELDS:
        if f not in fields:
            continue
        incoming = _as_list(fields[f])
        if merge:
            merged[f] = _as_list(list(merged.get(f, [])) + incoming)
        else:
            merged[f] = incoming

    merged["search_blob"] = _build_search_blob(merged)
    now = _now()

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM recording_context WHERE meeting_id = ?", (mid,))
        exists = cur.fetchone() is not None
        params = {
            "meeting_id": mid,
            "session_type": merged.get("session_type") or "meeting",
            "search_blob": merged.get("search_blob", ""),
            "updated_at": now,
        }
        for f in TEXT_FIELDS:
            params[f] = merged.get(f, "")
        for f in LIST_FIELDS:
            params[f] = json.dumps(merged.get(f, []), ensure_ascii=False)

        if exists:
            set_cols = ", ".join(f"{k} = :{k}" for k in params if k != "meeting_id")
            cur.execute(
                f"UPDATE recording_context SET {set_cols} WHERE meeting_id = :meeting_id",
                params,
            )
        else:
            params["created_at"] = now
            cols = ", ".join(params.keys())
            placeholders = ", ".join(f":{k}" for k in params.keys())
            cur.execute(
                f"INSERT INTO recording_context ({cols}) VALUES ({placeholders})",
                params,
            )
        conn.commit()
    finally:
        conn.close()

    merged["meeting_id"] = mid
    merged["updated_at"] = now
    return merged


def _build_search_blob(ctx: dict[str, Any]) -> str:
    """Denormalized lowercase text of all metadata for cheap LIKE/scoring."""
    chunks: list[str] = []
    for f in TEXT_FIELDS:
        if ctx.get(f):
            chunks.append(str(ctx[f]))
    for f in LIST_FIELDS:
        vals = ctx.get(f) or []
        if vals:
            chunks.append(" ".join(str(x) for x in vals))
    return re.sub(r"\s+", " ", " ".join(chunks)).strip().lower()


# --------------------------------------------------------------------------- #
# Entity + keyword extraction (LLM with regex fallback)
# --------------------------------------------------------------------------- #
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "had",
    "are", "was", "were", "will", "would", "could", "should", "about", "into",
    "your", "you", "our", "their", "them", "they", "she", "him", "her", "his",
    "what", "when", "where", "which", "who", "whom", "there", "here", "then",
    "than", "been", "being", "just", "like", "also", "some", "any", "all",
    "not", "but", "can", "did", "does", "done", "get", "got", "let", "lets",
    "okay", "yeah", "yes", "right", "going", "want", "need", "know", "think",
    "meeting", "note", "notes", "record", "recording",
}


def _keyword_fallback(text: str, limit: int = 25) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}", (text or "").lower())
    freq: dict[str, int] = {}
    for w in words:
        if w in _STOPWORDS or len(w) <= 2:
            continue
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:limit]]


def extract_entities(transcript: str, summary: str, pre_context: str = "") -> dict[str, list[str]]:
    """
    Extract people / projects / events / organizations / locations / topics /
    keywords from a recording. Uses Claude when available; otherwise a keyword
    fallback so the system still indexes something.
    """
    empty = {
        "referenced_people": [],
        "referenced_projects": [],
        "referenced_events": [],
        "referenced_organizations": [],
        "referenced_locations": [],
        "referenced_topics": [],
        "keywords": [],
    }
    blob = "\n\n".join(p for p in (pre_context, summary, transcript) if _norm_str(p))
    if not _norm_str(blob):
        return empty

    llm = _extract_entities_llm(transcript=transcript, summary=summary, pre_context=pre_context)
    if llm is not None:
        # Always backfill keywords if the model omitted them.
        if not llm.get("keywords"):
            llm["keywords"] = _keyword_fallback(blob)
        return {**empty, **llm}

    out = dict(empty)
    out["keywords"] = _keyword_fallback(blob)
    return out


def _extract_entities_llm(transcript: str, summary: str, pre_context: str) -> Optional[dict[str, list[str]]]:
    try:
        from routes import meetings as meetings_routes

        client = meetings_routes._get_anthropic_client()
    except Exception:
        client = None
    if not client:
        return None

    context_block = ""
    if _norm_str(pre_context):
        context_block = (
            "Context the user gave before/around the recording (very important — "
            "may name people or events not spoken in the transcript):\n"
            f"{pre_context.strip()}\n\n"
        )
    body = (summary or "").strip()
    tail = (transcript or "").strip()
    # Keep the prompt bounded.
    if len(tail) > 12000:
        tail = tail[:12000]

    prompt = (
        "Extract searchable metadata from a recording so it can be found later. "
        "Return ONLY valid JSON with these exact keys, each an array of short strings "
        "(deduplicated, proper-cased, no empty strings):\n"
        "{\n"
        '  "referenced_people": [],\n'
        '  "referenced_projects": [],\n'
        '  "referenced_events": [],\n'
        '  "referenced_organizations": [],\n'
        '  "referenced_locations": [],\n'
        '  "referenced_topics": [],\n'
        '  "keywords": []\n'
        "}\n\n"
        "Rules:\n"
        "- people: names of humans mentioned (first name, full name, or role+name).\n"
        "- projects: named initiatives/products (e.g. 'Project Atlas').\n"
        "- events: named or referenced events (e.g. 'board meeting', 'investor call', "
        "'client review', 'standup').\n"
        "- organizations: companies/teams/departments.\n"
        "- locations: places.\n"
        "- topics: 3-8 high-level subjects discussed.\n"
        "- keywords: 8-20 salient terms useful for search.\n"
        "- Include entities that appear ONLY in the context block, not just the transcript.\n"
        "- Do not invent anything not supported by the text.\n\n"
        f"{context_block}"
        f"Summary:\n{body}\n\n"
        f"Transcript:\n{tail}"
    )
    try:
        resp = client.messages.create(
            model=os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929"),
            max_tokens=int(os.getenv("AI_METADATA_MAX_TOKENS", "1024")),
            messages=[{"role": "user", "content": prompt}],
        )
        text = meetings_routes._anthropic_message_text(resp)
    except Exception as exc:
        logger.debug("entity extraction LLM call failed: %s", exc)
        return None
    if not text:
        return None
    if "```json" in text:
        start = text.find("```json") + len("```json")
        end = text.find("```", start)
        text = text[start:end]
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("entity extraction returned non-JSON; ignoring")
        return None
    if not isinstance(data, dict):
        return None
    return {
        "referenced_people": _as_list(data.get("referenced_people")),
        "referenced_projects": _as_list(data.get("referenced_projects")),
        "referenced_events": _as_list(data.get("referenced_events")),
        "referenced_organizations": _as_list(data.get("referenced_organizations")),
        "referenced_locations": _as_list(data.get("referenced_locations")),
        "referenced_topics": _as_list(data.get("referenced_topics")),
        "keywords": _as_list(data.get("keywords")),
    }


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
def _embedding_client():
    try:
        if not _norm_str(os.getenv("OPENAI_API_KEY")):
            return None
        from openai import OpenAI

        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception:
        return None


def embed_text(text: str) -> Optional[list[float]]:
    """Return an embedding vector for text, or None if embeddings are unavailable."""
    t = _norm_str(text)
    if not t:
        return None
    client = _embedding_client()
    if not client:
        return None
    if len(t) > 28000:
        t = t[:28000]
    try:
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=t)
        return list(resp.data[0].embedding)
    except Exception as exc:
        logger.debug("embedding call failed: %s", exc)
        return None


def vector_to_blob(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def blob_to_vector(blob: bytes) -> list[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def _store_embedding(meeting_id: str, vec: list[float]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO recording_embeddings (meeting_id, model, dim, vector, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (meeting_id, EMBEDDING_MODEL, len(vec), vector_to_blob(vec), _now()),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# FTS indexing
# --------------------------------------------------------------------------- #
def _fts_available(conn) -> bool:
    try:
        conn.execute("SELECT 1 FROM recordings_fts LIMIT 1")
        return True
    except Exception:
        return False


def _update_fts(meeting_id: str, title: str, summary: str, transcript: str, metadata: str) -> None:
    conn = get_connection()
    try:
        if not _fts_available(conn):
            return
        conn.execute("DELETE FROM recordings_fts WHERE meeting_id = ?", (meeting_id,))
        conn.execute(
            "INSERT INTO recordings_fts (meeting_id, title, summary, transcript, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (meeting_id, title or "", summary or "", transcript or "", metadata or ""),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Public: index a recording end-to-end
# --------------------------------------------------------------------------- #
def _load_recording_text(meeting_id: str) -> dict[str, Any]:
    conn = get_connection()
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, recording_mode, participants FROM meetings WHERE id = ?", (meeting_id,))
        m = cur.fetchone() or {}
        cur.execute("SELECT summary FROM summaries WHERE meeting_id = ?", (meeting_id,))
        s = cur.fetchone() or {}
        cur.execute(
            "SELECT text FROM segments WHERE meeting_id = ? ORDER BY segment_num ASC",
            (meeting_id,),
        )
        seg_rows = cur.fetchall()
    finally:
        conn.close()
    transcript = "\n".join(_norm_str(r.get("text")) for r in seg_rows if _norm_str(r.get("text")))
    return {
        "title": _norm_str(m.get("title")),
        "recording_mode": _norm_str(m.get("recording_mode")) or "meeting",
        "summary": _norm_str(s.get("summary")),
        "transcript": transcript,
    }


def index_recording(
    meeting_id: str,
    *,
    extract: bool = True,
) -> dict[str, Any]:
    """
    Build/refresh all search artifacts for a finished recording:
      * extract entities + keywords from transcript/summary (merged into context),
      * write the FTS row,
      * compute + store the embedding.

    Safe to call multiple times. Returns a small status dict.
    """
    mid = _norm_str(meeting_id)
    if not mid:
        return {"ok": False, "reason": "no_meeting_id"}

    rec = _load_recording_text(mid)
    ctx = get_recording_context(mid)
    if _norm_str(rec["recording_mode"]) and ctx.get("session_type") in (None, "", "meeting"):
        ctx["session_type"] = rec["recording_mode"]

    if extract:
        entities = extract_entities(
            transcript=rec["transcript"],
            summary=rec["summary"],
            pre_context=ctx.get("pre_context", ""),
        )
        ctx = save_recording_context(
            mid,
            session_type=rec["recording_mode"],
            merge=True,
            **entities,
        )

    # Metadata text for FTS = everything searchable that isn't the transcript/summary.
    metadata_text = ctx.get("search_blob", "")

    _update_fts(
        mid,
        title=rec["title"],
        summary=rec["summary"],
        transcript=rec["transcript"],
        metadata=metadata_text,
    )

    embedded = False
    doc = _compose_embedding_doc(rec, ctx)
    vec = embed_text(doc)
    if vec:
        _store_embedding(mid, vec)
        embedded = True

    return {"ok": True, "meeting_id": mid, "embedded": embedded}


def backfill_fts_index(limit: int = 5000) -> dict[str, Any]:
    """
    Cheap migration for recordings created before the search index existed:
    build the FTS row + search_blob for any meeting without an FTS entry.

    Intentionally does NOT call the LLM extractor or the embeddings API, so it's
    safe to run at startup. New recordings get full extraction/embeddings via
    ``index_recording`` in the pipeline; semantic search simply doesn't apply to
    un-backfilled legacy rows until they're re-summarized.
    """
    conn = get_connection()
    try:
        if not _fts_available(conn):
            return {"ok": False, "reason": "fts_unavailable"}
        rows = conn.execute(
            """
            SELECT m.id FROM meetings m
            WHERE m.id NOT IN (SELECT meeting_id FROM recordings_fts)
            ORDER BY COALESCE(m.created_at, m.start_time, '') DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception:
        logger.debug("backfill candidate query failed", exc_info=True)
        return {"ok": False, "reason": "query_failed"}
    finally:
        conn.close()

    count = 0
    for (mid,) in rows:
        try:
            rec = _load_recording_text(mid)
            ctx = get_recording_context(mid)
            if ctx.get("session_type") in (None, "", "meeting"):
                ctx["session_type"] = rec["recording_mode"]
            _update_fts(
                mid,
                title=rec["title"],
                summary=rec["summary"],
                transcript=rec["transcript"],
                metadata=ctx.get("search_blob", ""),
            )
            count += 1
        except Exception:
            logger.debug("backfill failed for meeting_id=%s", mid, exc_info=True)
    if count:
        logger.info("recording FTS backfill indexed %d legacy recording(s)", count)
    return {"ok": True, "indexed": count}


def _compose_embedding_doc(rec: dict[str, Any], ctx: dict[str, Any]) -> str:
    """
    The text we embed = summary + metadata + transcript. Putting metadata and
    summary first (and weighting them by inclusion) means a note tagged
    'board meeting' embeds near a query about the board meeting even if the
    transcript never says those words.
    """
    parts: list[str] = []
    if rec.get("title"):
        parts.append(f"Title: {rec['title']}")
    parts.append(f"Type: {ctx.get('session_type', 'meeting')}")
    if ctx.get("session_intent"):
        parts.append(f"Intent: {ctx['session_intent']}")
    if ctx.get("pre_context"):
        parts.append(f"Before: {ctx['pre_context']}")
    if ctx.get("post_context"):
        parts.append(f"After: {ctx['post_context']}")
    for label, field in (
        ("People", "referenced_people"),
        ("Projects", "referenced_projects"),
        ("Events", "referenced_events"),
        ("Organizations", "referenced_organizations"),
        ("Locations", "referenced_locations"),
        ("Topics", "referenced_topics"),
        ("Keywords", "keywords"),
    ):
        vals = ctx.get(field) or []
        if vals:
            parts.append(f"{label}: {', '.join(vals)}")
    if rec.get("summary"):
        parts.append(f"Summary: {rec['summary']}")
    if rec.get("transcript"):
        tail = rec["transcript"]
        parts.append(f"Transcript: {tail[:8000]}")
    return "\n".join(parts).strip()
