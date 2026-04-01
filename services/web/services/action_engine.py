import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from database import get_connection
from routes.integrations import get_action_capabilities, get_credentials_for_provider
from services.calendar import create_event, default_calendar_tz_name
from services.gmail import create_draft as gmail_create_draft
from services.gmail import send_email

logger = logging.getLogger(__name__)

ACTION_KIND_SPECS: dict[str, dict[str, str]] = {
    "cost_analysis": {
        "connector_target": "internal",
        "execution_mode": "artifact_create",
        "title": "Create cost analysis",
    },
    "decision_brief": {
        "connector_target": "internal",
        "execution_mode": "artifact_create",
        "title": "Create decision brief",
    },
    "risk_register": {
        "connector_target": "internal",
        "execution_mode": "artifact_create",
        "title": "Create risk register",
    },
    "task_digest": {
        "connector_target": "internal",
        "execution_mode": "artifact_create",
        "title": "Create task digest",
    },
    "followup_email": {
        "connector_target": "gmail",
        "execution_mode": "message_send",
        "title": "Send follow-up email",
    },
    "schedule_followup": {
        "connector_target": "calendar",
        "execution_mode": "event_create",
        "title": "Schedule follow-up",
    },
}

_anthropic_client = None


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        from anthropic import Anthropic

        _anthropic_client = Anthropic(api_key=api_key)
    return _anthropic_client


def _parse_json_from_llm(text: str) -> Any:
    if "```json" in text:
        start = text.find("```json") + len("```json")
        end = text.find("```", start)
        return json.loads(text[start:end].strip())
    if "[" in text and "]" in text:
        start = text.find("[")
        end = text.rfind("]") + 1
        return json.loads(text[start:end])
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    raise json.JSONDecodeError("No JSON found", text, 0)


def _loads_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _normalize_action_record(row: dict[str, Any]) -> dict[str, Any]:
    payload = _loads_json(row.get("payload"), {})
    artifact = _loads_json(row.get("artifact"), None)
    legacy_draft = _loads_json(row.get("draft"), {})
    result_payload = {**legacy_draft, **payload}

    kind = row.get("kind")
    connector_target = row.get("connector_target")
    execution_mode = row.get("execution_mode")

    if not kind:
        legacy_type = (row.get("type") or "").strip().lower()
        if legacy_type == "email_draft":
            kind = "followup_email"
        elif legacy_type == "calendar_invite":
            kind = "schedule_followup"
        else:
            kind = "task_digest"

    spec = ACTION_KIND_SPECS.get(kind, {})
    connector_target = connector_target or spec.get("connector_target", "internal")
    execution_mode = execution_mode or spec.get("execution_mode", "artifact_create")

    return {
        "id": row["id"],
        "meeting_id": row["meeting_id"],
        "type": row.get("type") or kind,
        "kind": kind,
        "connector_target": connector_target,
        "execution_mode": execution_mode,
        "title": row.get("title") or spec.get("title"),
        "description": row.get("description"),
        "assignee": row.get("assignee"),
        "confidence": row.get("confidence"),
        "payload": result_payload,
        "artifact": artifact,
        "status": row.get("status") or "pending",
        "delivery_status": row.get("delivery_status"),
        "error": row.get("error"),
        "selected_at": row.get("selected_at"),
        "executed_at": row.get("executed_at"),
        "created_at": row.get("created_at"),
    }


def get_meeting_context(meeting_id: str) -> dict[str, Any]:
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, start_time FROM meetings WHERE id = ?", (meeting_id,))
        meeting = cur.fetchone()
        cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
        summary = cur.fetchone()
        cur.execute("SELECT * FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
        local_summary = cur.fetchone()
        cur.execute(
            """
            SELECT segment_num, start_time, text
            FROM segments
            WHERE meeting_id = ?
            ORDER BY segment_num
            """,
            (meeting_id,),
        )
        segments = cur.fetchall()
    finally:
        conn.close()

    chosen_summary = summary or local_summary or {}
    transcript_parts = []
    for segment in segments:
        mins = int((segment.get("start_time") or 0) // 60)
        secs = int((segment.get("start_time") or 0) % 60)
        transcript_parts.append(f"[{mins:02d}:{secs:02d}] {segment.get('text', '')}")

    return {
        "meeting": meeting or {},
        "summary": chosen_summary.get("summary", "") if chosen_summary else "",
        "decisions": _loads_json(chosen_summary.get("decisions"), []),
        "topics": _loads_json(chosen_summary.get("topics"), []),
        "action_items": _loads_json(chosen_summary.get("action_items"), []),
        "transcript": "\n".join(transcript_parts),
    }


def _build_generation_prompt_gmail_calendar(context: dict[str, Any], capabilities: list[dict[str, Any]]) -> str:
    capability_text = json.dumps(capabilities, indent=2)
    tz_hint = default_calendar_tz_name()
    return (
        "You are generating meeting follow-up actions that the user can run from MeetingBox.\n"
        "Only Gmail (follow-up email) and Google Calendar (schedule follow-up) are allowed.\n"
        "Propose at most 3 actions total. Do not propose duplicates or near-duplicates (same intent).\n"
        "Each action must be clearly grounded in the meeting content.\n\n"
        "If the meeting is only a test, a monologue, prompt experimentation, narration, or otherwise has no real external follow-up, return an empty array.\n"
        "Do not invent stakeholders, dates, times, attendees, or follow-ups that are not supported by the transcript or summary.\n"
        "You may only use these action kinds: followup_email, schedule_followup.\n"
        "- followup_email → connector_target gmail, execution_mode message_send\n"
        "- schedule_followup → connector_target calendar, execution_mode event_create\n\n"
        "For followup_email, only emit an action if there is a concrete follow-up worth emailing about. Include payload with subject and body draft text.\n"
        "For schedule_followup, only emit an action if there is a concrete next meeting/check-in/block worth scheduling. Include payload with suggested_date and suggested_time.\n"
        "For schedule_followup, put a tentative draft in payload: "
        'suggested_date (YYYY-MM-DD), suggested_time (HH:MM 24h), duration_minutes (int), '
        f"timezone (IANA, default \"{tz_hint}\"), attendees ([] emails), description (optional).\n\n"
        "Return 0 to 3 actions as a JSON array. Each item must match this shape:\n"
        "[{\n"
        '  "kind": "followup_email",\n'
        '  "title": "Short label",\n'
        '  "description": "One-sentence explanation.",\n'
        '  "why_this_matters": "Why now.",\n'
        '  "connector_target": "gmail",\n'
        '  "execution_mode": "message_send",\n'
        '  "payload": {},\n'
        '  "source_signals": ["..."],\n'
        '  "confidence": 0.0\n'
        "}]\n\n"
        f"Capability catalog:\n{capability_text}\n\n"
        f"Meeting title: {context['meeting'].get('title', 'Untitled')}\n"
        f"Meeting date: {context['meeting'].get('start_time', '')}\n"
        f"Summary: {context['summary']}\n"
        f"Decisions: {json.dumps(context['decisions'])}\n"
        f"Human follow-ups: {json.dumps(context['action_items'])}\n"
        f"Transcript:\n{context['transcript']}\n"
    )


def _build_internal_artifact_prompt(action: dict[str, Any], context: dict[str, Any]) -> str:
    artifact_types = {
        "cost_analysis": "a compact cost analysis with options, assumptions, drivers, and recommendation",
        "decision_brief": "a decision brief with options, tradeoffs, recommendation, and open questions",
        "risk_register": "a risk register with risk, impact, owner, mitigation, and trigger",
        "task_digest": "an execution digest with owners, deadlines, dependencies, and immediate next steps",
    }
    artifact_type = artifact_types.get(action["kind"], "a structured internal artifact")
    return (
        f"Create {artifact_type} based on this meeting.\n"
        "Return only valid JSON with this shape:\n"
        '{\n'
        '  "artifact_type": "cost_analysis",\n'
        '  "headline": "...",\n'
        '  "summary": "...",\n'
        '  "sections": [\n'
        '    {"title": "...", "bullets": ["...", "..."]}\n'
        "  ]\n"
        "}\n\n"
        f"Action:\n{json.dumps(action, indent=2)}\n\n"
        f"Meeting context:\n{json.dumps(context, indent=2)}"
    )


def _build_email_prompt(action: dict[str, Any], context: dict[str, Any]) -> str:
    return (
        "Create a professional follow-up email from this meeting.\n"
        "Return only valid JSON with this shape:\n"
        '{\n'
        '  "to": ["person@example.com"],\n'
        '  "subject": "...",\n'
        '  "body": "...",\n'
        '  "cc": []\n'
        "}\n\n"
        f"Action:\n{json.dumps(action, indent=2)}\n\n"
        f"Meeting context:\n{json.dumps(context, indent=2)}"
    )


def _build_calendar_prompt(action: dict[str, Any], context: dict[str, Any]) -> str:
    tz = default_calendar_tz_name()
    return (
        "Create a practical follow-up calendar event from this meeting.\n"
        f"Use IANA timezone \"{tz}\" for suggested_date + suggested_time unless the transcript specifies a different zone or city.\n"
        "Return only valid JSON with this shape:\n"
        '{\n'
        '  "title": "...",\n'
        '  "description": "...",\n'
        '  "attendees": ["person@example.com"],\n'
        '  "duration_minutes": 30,\n'
        '  "suggested_date": "YYYY-MM-DD",\n'
        '  "suggested_time": "HH:MM",\n'
        f'  "timezone": "{tz}"\n'
        "}\n\n"
        f"Action:\n{json.dumps(action, indent=2)}\n\n"
        f"Meeting context:\n{json.dumps(context, indent=2)}"
    )


def _call_claude_json(prompt: str) -> Any:
    client = get_anthropic_client()
    if not client:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY is not configured.")

    model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Claude API error: {exc}")

    text = resp.content[0].text
    try:
        return _parse_json_from_llm(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse Claude response: {exc}")


def _normalize_title_key(title: str) -> str:
    return " ".join((title or "").lower().split())[:160]


def _dedupe_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("kind", "")).strip().lower(),
            str(item.get("connector_target", "")).strip().lower(),
            _normalize_title_key(str(item.get("title", "")).strip()),
        ]
    )


def _capabilities_gmail_calendar_only(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in capabilities if c.get("connector_target") in ("gmail", "calendar")]


def _find_pending_action_duplicate(
    cur: Any,
    meeting_id: str,
    kind: str,
    connector_target: str,
    title_key: str,
) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT * FROM actions
        WHERE meeting_id = ?
          AND lower(trim(kind)) = lower(trim(?))
          AND lower(trim(connector_target)) = lower(trim(?))
          AND status = 'pending'
        """,
        (meeting_id, kind, connector_target),
    )
    for row in cur.fetchall():
        if _normalize_title_key((row.get("title") or "").strip()) == title_key:
            return row
    return None


def _has_meaningful_gmail_draft(payload: dict[str, Any]) -> bool:
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    return bool(subject and body)


def _has_meaningful_calendar_draft(payload: dict[str, Any]) -> bool:
    suggested_date = str(payload.get("suggested_date") or "").strip()
    suggested_time = str(payload.get("suggested_time") or "").strip()
    return bool(suggested_date and suggested_time)


def _is_valid_generated_action(action: dict[str, Any]) -> bool:
    title = str(action.get("title") or "").strip()
    description = str(action.get("description") or "").strip()
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    connector_target = str(action.get("connector_target") or "").strip()
    if not title:
        return False
    if connector_target == "gmail":
        return _has_meaningful_gmail_draft(payload)
    if connector_target == "calendar":
        return _has_meaningful_calendar_draft(payload) and bool(title or description)
    return False


def generate_actions_for_meeting(meeting_id: str, user_id: str | None) -> list[dict[str, Any]]:
    context = get_meeting_context(meeting_id)
    if not context["summary"] and not context["transcript"]:
        raise HTTPException(status_code=400, detail="No meeting summary or transcript available to generate actions.")

    capabilities = _capabilities_gmail_calendar_only(get_action_capabilities(user_id))
    if not capabilities:
        raise HTTPException(
            status_code=400,
            detail="Connect Gmail and/or Google Calendar under Settings → Integrations to generate actions.",
        )
    generated = _call_claude_json(_build_generation_prompt_gmail_calendar(context, capabilities))
    if not isinstance(generated, list):
        raise HTTPException(status_code=500, detail="Claude returned invalid action data.")

    allowed_connectors = {cap["connector_target"] for cap in capabilities}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in generated:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        spec = ACTION_KIND_SPECS.get(kind)
        if not spec:
            continue
        connector_target = str(item.get("connector_target") or spec["connector_target"]).strip()
        execution_mode = str(item.get("execution_mode") or spec["execution_mode"]).strip()
        if connector_target not in allowed_connectors:
            continue
        action = {
            "kind": kind,
            "type": kind,
            "connector_target": connector_target,
            "execution_mode": execution_mode,
            "title": str(item.get("title") or spec["title"]).strip(),
            "description": str(item.get("description") or item.get("why_this_matters") or "").strip(),
            "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
            "confidence": float(item.get("confidence") or 0),
            "draft": {
                "why_this_matters": item.get("why_this_matters", ""),
                "source_signals": item.get("source_signals", []),
            },
        }
        if not _is_valid_generated_action(action):
            continue
        key = _dedupe_key(action)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(action)
        if len(normalized) >= 3:
            break

    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM actions
            WHERE meeting_id = ?
              AND status = 'pending'
              AND connector_target IN ('gmail', 'calendar')
            """,
            (meeting_id,),
        )
        stored: list[dict[str, Any]] = []
        for action in normalized:
            action_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            cur.execute(
                """
                INSERT INTO actions
                  (id, meeting_id, type, kind, connector_target, execution_mode, title, description, confidence, draft, payload, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    action_id,
                    meeting_id,
                    action["type"],
                    action["kind"],
                    action["connector_target"],
                    action["execution_mode"],
                    action["title"],
                    action["description"],
                    action["confidence"],
                    json.dumps(action["draft"]),
                    json.dumps(action["payload"]),
                    now,
                ),
            )
            cur.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
            stored.append(_normalize_action_record(cur.fetchone()))
        conn.commit()
        return stored
    finally:
        conn.close()


def list_actions_for_meeting(meeting_id: str) -> list[dict[str, Any]]:
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM actions WHERE meeting_id = ? ORDER BY created_at DESC", (meeting_id,))
        rows = cur.fetchall()
        out = []
        for row in rows:
            rec = _normalize_action_record(row)
            if rec.get("connector_target") in ("gmail", "calendar"):
                out.append(rec)
        return out
    finally:
        conn.close()


def update_action_record(action_id: str, *, title: str | None = None, description: str | None = None, payload: dict | None = None) -> dict[str, Any]:
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Action not found")

        updates: list[str] = []
        params: list[Any] = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if payload is not None:
            updates.append("payload = ?")
            params.append(json.dumps(payload))
        if updates:
            params.append(action_id)
            cur.execute(f"UPDATE actions SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        cur.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
        return _normalize_action_record(cur.fetchone())
    finally:
        conn.close()


def dismiss_action_record(action_id: str) -> dict[str, str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM actions WHERE id = ?", (action_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Action not found")
        cur.execute("UPDATE actions SET status = 'dismissed' WHERE id = ?", (action_id,))
        conn.commit()
    finally:
        conn.close()
    return {"id": action_id, "status": "dismissed"}


def _coerce_email_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if not s:
        return []
    return [p.strip() for p in re.split(r"[\s,;]+", s) if p.strip()]


def _gmail_fields_ready(p: dict[str, Any]) -> bool:
    sub = (str(p.get("subject") or "")).strip()
    body = (str(p.get("body") or "")).strip()
    to = p.get("to")
    if not sub or not body:
        return False
    if isinstance(to, list):
        return any(str(x).strip() for x in to)
    return bool(str(to or "").strip())


def _calendar_fields_ready(p: dict[str, Any]) -> bool:
    return bool(str(p.get("suggested_date") or "").strip() and str(p.get("suggested_time") or "").strip())


def _merge_pref_over_llm(llm: dict[str, Any], pref: dict[str, Any]) -> dict[str, Any]:
    """User-provided fields in pref win over LLM output."""
    out = dict(llm)
    for k, v in pref.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        out[k] = v
    return out


def execute_action_record(
    action_id: str,
    user_id: str | None,
    payload_override: dict[str, Any] | None = None,
    *,
    create_draft: bool = False,
) -> dict[str, Any]:
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
        action = cur.fetchone()
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")
    finally:
        conn.close()

    normalized = _normalize_action_record(action)
    if normalized["status"] == "executed":
        return {
            "id": action_id,
            "status": "executed",
            "delivery_status": normalized.get("delivery_status") or "already_executed",
            "artifact": normalized.get("artifact"),
            "result": normalized["payload"],
        }

    context = get_meeting_context(normalized["meeting_id"])
    connector_target = normalized["connector_target"]
    result_payload: dict[str, Any]
    artifact: dict[str, Any] | None = None
    delivery_status = "saved"

    merged_in = dict(normalized["payload"] or {})
    if payload_override:
        merged_in = {**merged_in, **payload_override}
    action_for_llm = {**normalized, "payload": merged_in}

    if connector_target == "internal":
        raise HTTPException(
            status_code=400,
            detail="Internal artifacts are not available in the Actions tab. Dismiss legacy actions or use an older client.",
        )
    elif connector_target == "gmail":
        if not user_id:
            raise HTTPException(status_code=400, detail="Logged-in user required to execute Gmail actions.")
        creds = get_credentials_for_provider(user_id, "gmail")
        if not creds:
            raise HTTPException(status_code=400, detail="Gmail is not connected.")
        if _gmail_fields_ready(merged_in):
            result_payload = dict(merged_in)
        else:
            llm = _call_claude_json(_build_email_prompt(action_for_llm, context))
            result_payload = _merge_pref_over_llm(llm, merged_in)
        to_list = _coerce_email_list(result_payload.get("to"))
        if not to_list:
            raise HTTPException(status_code=400, detail="At least one recipient email is required.")
        to = ", ".join(to_list)
        cc_val = result_payload.get("cc")
        if isinstance(cc_val, list):
            cc_str = ", ".join(_coerce_email_list(cc_val))
        else:
            cc_str = (str(cc_val).strip() if cc_val else "") or None
        subject_str = str(result_payload.get("subject") or normalized["title"] or "Follow-up")
        body_str = str(result_payload.get("body") or "")
        if create_draft:
            draft_res = gmail_create_draft(
                credentials=creds,
                to=to,
                subject=subject_str,
                body=body_str,
                cc=cc_str,
            )
            result_payload["to"] = to_list
            result_payload["gmail_draft_id"] = draft_res.get("id")
            msg = draft_res.get("message") or {}
            result_payload["gmail_message_id"] = msg.get("id")
            delivery_status = "saved_gmail_draft"
        else:
            gmail_result = send_email(
                credentials=creds,
                to=to,
                subject=subject_str,
                body=body_str,
                cc=cc_str,
            )
            result_payload["to"] = to_list
            result_payload["gmail_message_id"] = gmail_result.get("id")
            delivery_status = "sent_via_gmail"
    elif connector_target == "calendar":
        if not user_id:
            raise HTTPException(status_code=400, detail="Logged-in user required to execute Calendar actions.")
        creds = get_credentials_for_provider(user_id, "calendar")
        if not creds:
            raise HTTPException(status_code=400, detail="Google Calendar is not connected.")
        if _calendar_fields_ready(merged_in):
            result_payload = dict(merged_in)
        else:
            llm = _call_claude_json(_build_calendar_prompt(action_for_llm, context))
            result_payload = _merge_pref_over_llm(llm, merged_in)
        start_date = str(result_payload.get("suggested_date") or "").strip()
        start_time = str(result_payload.get("suggested_time") or "10:00").strip()
        if start_time.count(":") == 2:
            start_time = ":".join(start_time.split(":")[:2])
        if not start_date:
            raise HTTPException(
                status_code=400,
                detail="Calendar event needs a date. Add suggested_date in review or regenerate the action.",
            )
        tz_use = (str(result_payload.get("timezone") or "").strip() or default_calendar_tz_name())
        attendees = _coerce_email_list(result_payload.get("attendees"))
        calendar_result = create_event(
            credentials=creds,
            title=str(result_payload.get("title") or normalized["title"] or "Follow-up"),
            start_date=start_date,
            start_time_hhmm=start_time,
            timezone=tz_use,
            duration_minutes=int(result_payload.get("duration_minutes", 30) or 30),
            description=str(result_payload.get("description") or ""),
            attendees=attendees,
        )
        result_payload["suggested_date"] = start_date
        result_payload["suggested_time"] = start_time
        result_payload["timezone"] = tz_use
        result_payload["attendees"] = attendees
        result_payload["calendar_event_id"] = calendar_result.get("id")
        result_payload["calendar_link"] = calendar_result.get("htmlLink")
        delivery_status = "created_via_calendar"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported connector target: {connector_target}")

    stored_payload = {**normalized["payload"], **result_payload}

    now = datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE actions
            SET status = ?, delivery_status = ?, error = NULL, payload = ?, artifact = ?, selected_at = COALESCE(selected_at, ?), executed_at = ?
            WHERE id = ?
            """,
            (
                "executed",
                delivery_status,
                json.dumps(stored_payload),
                json.dumps(artifact) if artifact is not None else None,
                now,
                now,
                action_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": action_id,
        "status": "executed",
        "delivery_status": delivery_status,
        "artifact": artifact,
        "result": result_payload,
    }
