import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from database import get_connection
from routes.integrations import get_action_capabilities, get_credentials_for_provider
from services.calendar import create_event
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


def _build_generation_prompt(context: dict[str, Any], capabilities: list[dict[str, Any]]) -> str:
    capability_text = json.dumps(capabilities, indent=2)
    return (
        "You are generating high-value, agentic meeting actions.\n"
        "Do not restate generic human follow-ups. Propose only actions that an AI system can execute now.\n"
        "Only use connectors present in the capability catalog. If no external connector fits, prefer internal artifacts.\n"
        "You may only use these action kinds: cost_analysis, decision_brief, risk_register, task_digest, followup_email, schedule_followup.\n"
        "Return 0 to 5 actions as a JSON array. Each item must match this shape:\n"
        "[{\n"
        '  "kind": "cost_analysis",\n'
        '  "title": "Create cost analysis for vendor options",\n'
        '  "description": "One-sentence explanation of what will be produced or sent.",\n'
        '  "why_this_matters": "Why this action is valuable now.",\n'
        '  "connector_target": "internal | gmail | calendar",\n'
        '  "execution_mode": "artifact_create | message_send | event_create",\n'
        '  "payload": {},\n'
        '  "source_signals": ["signal one", "signal two"],\n'
        '  "confidence": 0.0\n'
        "}]\n\n"
        f"Capability catalog:\n{capability_text}\n\n"
        f"Meeting title: {context['meeting'].get('title', 'Untitled')}\n"
        f"Meeting date: {context['meeting'].get('start_time', '')}\n"
        f"Summary: {context['summary']}\n"
        f"Decisions: {json.dumps(context['decisions'])}\n"
        f"Human follow-ups: {json.dumps(context['action_items'])}\n"
        f"Topics: {json.dumps(context['topics'])}\n"
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
    return (
        "Create a practical follow-up meeting invitation from this meeting.\n"
        "Return only valid JSON with this shape:\n"
        '{\n'
        '  "title": "...",\n'
        '  "description": "...",\n'
        '  "attendees": ["person@example.com"],\n'
        '  "duration_minutes": 30,\n'
        '  "suggested_date": "YYYY-MM-DD",\n'
        '  "suggested_time": "HH:MM"\n'
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


def _dedupe_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("kind", "")).strip().lower(),
            str(item.get("connector_target", "")).strip().lower(),
            str(item.get("title", "")).strip().lower(),
        ]
    )


def generate_actions_for_meeting(meeting_id: str, user_id: str | None) -> list[dict[str, Any]]:
    context = get_meeting_context(meeting_id)
    if not context["summary"] and not context["transcript"]:
        raise HTTPException(status_code=400, detail="No meeting summary or transcript available to generate actions.")

    capabilities = get_action_capabilities(user_id)
    generated = _call_claude_json(_build_generation_prompt(context, capabilities))
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
        if not action["title"]:
            continue
        key = _dedupe_key(action)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(action)

    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        stored: list[dict[str, Any]] = []
        for action in normalized:
            cur.execute(
                """
                SELECT *
                FROM actions
                WHERE meeting_id = ?
                  AND lower(trim(kind)) = lower(trim(?))
                  AND lower(trim(title)) = lower(trim(?))
                  AND lower(trim(connector_target)) = lower(trim(?))
                LIMIT 1
                """,
                (meeting_id, action["kind"], action["title"], action["connector_target"]),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE actions
                    SET description = ?, confidence = ?, payload = ?, draft = ?, execution_mode = ?, type = ?
                    WHERE id = ?
                    """,
                    (
                        action["description"],
                        action["confidence"],
                        json.dumps(action["payload"]),
                        json.dumps(action["draft"]),
                        action["execution_mode"],
                        action["type"],
                        existing["id"],
                    ),
                )
                cur.execute("SELECT * FROM actions WHERE id = ?", (existing["id"],))
                stored.append(_normalize_action_record(cur.fetchone()))
                continue

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
        return [_normalize_action_record(row) for row in cur.fetchall()]
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


def execute_action_record(action_id: str, user_id: str | None) -> dict[str, Any]:
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
    kind = normalized["kind"]
    connector_target = normalized["connector_target"]
    result_payload: dict[str, Any]
    artifact: dict[str, Any] | None = None
    delivery_status = "saved"

    if connector_target == "internal":
        result_payload = _call_claude_json(_build_internal_artifact_prompt(normalized, context))
        artifact = result_payload if isinstance(result_payload, dict) else {"content": result_payload}
        delivery_status = "saved_internal_artifact"
    elif connector_target == "gmail":
        if not user_id:
            raise HTTPException(status_code=400, detail="Logged-in user required to execute Gmail actions.")
        creds = get_credentials_for_provider(user_id, "gmail")
        if not creds:
            raise HTTPException(status_code=400, detail="Gmail is not connected.")
        result_payload = _call_claude_json(_build_email_prompt(normalized, context))
        recipients = result_payload.get("to", [])
        if isinstance(recipients, list):
            to = ", ".join([str(item).strip() for item in recipients if str(item).strip()])
        else:
            to = str(recipients or "").strip()
        gmail_result = send_email(
            credentials=creds,
            to=to,
            subject=result_payload.get("subject", normalized["title"]),
            body=result_payload.get("body", ""),
            cc=", ".join(result_payload.get("cc", [])) if isinstance(result_payload.get("cc"), list) else result_payload.get("cc"),
        )
        result_payload["to"] = recipients if isinstance(recipients, list) else [to]
        result_payload["gmail_message_id"] = gmail_result.get("id")
        delivery_status = "sent_via_gmail"
    elif connector_target == "calendar":
        if not user_id:
            raise HTTPException(status_code=400, detail="Logged-in user required to execute Calendar actions.")
        creds = get_credentials_for_provider(user_id, "calendar")
        if not creds:
            raise HTTPException(status_code=400, detail="Google Calendar is not connected.")
        result_payload = _call_claude_json(_build_calendar_prompt(normalized, context))
        start_date = result_payload.get("suggested_date", "")
        start_time = result_payload.get("suggested_time", "10:00")
        start_iso = f"{start_date}T{start_time}:00" if start_date else None
        calendar_result = create_event(
            credentials=creds,
            title=result_payload.get("title", normalized["title"]),
            start_time=start_iso,
            duration_minutes=int(result_payload.get("duration_minutes", 30)),
            description=result_payload.get("description", ""),
            attendees=result_payload.get("attendees", []),
        )
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
