"""Gmail tool adapter — full email operations surface (communication agent)."""

from __future__ import annotations

from typing import Any

from routes.integrations import get_credentials_for_provider
from services.gmail import (
  add_recipients_to_draft,
  archive_message,
  create_draft,
  forward_message,
  list_recent_messages,
  remove_recipients_from_draft,
  reply_all_in_thread,
  reply_to_thread,
  send_draft,
  send_email,
  trash_message,
  update_draft,
)
from tools.base_tool import ToolError


# ----------------- shared payload helpers -----------------


def _require_creds(user_id: str):
  if not user_id:
    raise ToolError("Sign in is required.")
  creds = get_credentials_for_provider(user_id, "gmail")
  if not creds:
    raise ToolError("Gmail is not connected. Connect it in Settings.")
  return creds


def _addr_field_str(value: Any) -> str:
  """Coerce a recipient field (None / str / list) to a comma-separated string."""
  if value is None:
    return ""
  if isinstance(value, list):
    return ", ".join(str(x).strip() for x in value if str(x).strip())
  return str(value).strip()


def _addr_field_optional_str(value: Any) -> str | None:
  """Like _addr_field_str but returns None when the caller did not provide the key.
  Used by update_draft so omitted fields are preserved instead of cleared."""
  if value is None:
    return None
  if isinstance(value, list):
    return ", ".join(str(x).strip() for x in value if str(x).strip())
  return str(value).strip()


def _addr_field_list(value: Any) -> list[str]:
  """Coerce a recipient field to a list of stripped strings."""
  if value is None:
    return []
  if isinstance(value, list):
    return [str(x).strip() for x in value if str(x).strip()]
  s = str(value).strip()
  return [s] if s else []


# ----------------- existing tools (unchanged surface) -----------------


def _build_message_to_draft_id_map(creds, max_results: int) -> dict[str, str]:
  """When the caller queries drafts, Gmail's messages.list returns message IDs which
  are NOT valid draft IDs (drafts.update/send require the draft ID). Join via
  drafts.list to build a message_id -> draft_id map so callers can resolve correctly."""
  try:
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    fetch = max(max_results * 3, 30)
    fetch = min(fetch, 100)
    resp = (
      service.users()
      .drafts()
      .list(userId="me", maxResults=fetch)
      .execute()
    )
    out: dict[str, str] = {}
    for d in resp.get("drafts", []) or []:
      did = d.get("id")
      mid = (d.get("message") or {}).get("id")
      if did and mid:
        out[mid] = did
    return out
  except Exception:
    return {}


def _query_targets_drafts(q: str) -> bool:
  ql = (q or "").lower()
  return ("in:drafts" in ql) or ("is:draft" in ql) or ("label:draft" in ql)


def gmail_list_recent(
  user_id: str,
  max_results: int = 10,
  q: str = "",
) -> dict[str, Any]:
  creds = _require_creds(user_id)
  max_results = max(1, min(int(max_results), 30))
  messages = list_recent_messages(creds, max_results=max_results, q=q or "")
  if _query_targets_drafts(q):
    mid_to_did = _build_message_to_draft_id_map(creds, max_results)
    for m in messages:
      did = mid_to_did.get(m.get("id"))
      if did:
        m["draft_id"] = did
  return {"messages": messages, "count": len(messages)}


def gmail_send_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  creds = _require_creds(user_id)
  to = _addr_field_str(payload.get("to"))
  if not to:
    raise ToolError("Recipient address (to) is required.")
  subject = str(payload.get("subject") or "(no subject)")
  body = str(payload.get("body") or "")
  html_raw = payload.get("html_body")
  html_body = str(html_raw).strip() if html_raw not in (None, "") else None
  cc_str = _addr_field_str(payload.get("cc")) or None
  bcc_str = _addr_field_str(payload.get("bcc")) or None
  thread_id_raw = payload.get("thread_id") or payload.get("threadId")
  thread_id = str(thread_id_raw).strip() if thread_id_raw else None

  return send_email(
    credentials=creds,
    to=to,
    subject=subject,
    body=body,
    html_body=html_body,
    cc=cc_str,
    bcc=bcc_str,
    thread_id=thread_id,
  )


def gmail_draft_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Save email as a Gmail draft (does not send). Returns the draft id."""
  creds = _require_creds(user_id)
  to = _addr_field_str(payload.get("to"))
  subject = str(payload.get("subject") or "(no subject)")
  body = str(payload.get("body") or "")
  cc_str = _addr_field_str(payload.get("cc")) or None
  result = create_draft(credentials=creds, to=to, subject=subject, body=body, cc=cc_str)
  return {
    "draft_id": result.get("id"),
    "to": to,
    "subject": subject,
    "body_preview": body[:200],
    "saved_to_gmail_drafts": True,
  }


# ----------------- new tools (Email Operations Agent) -----------------


def gmail_update_draft_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Replace fields on an existing draft. Omitted fields are preserved."""
  creds = _require_creds(user_id)
  draft_id = str(payload.get("draft_id") or "").strip()
  if not draft_id:
    raise ToolError("draft_id is required to update a draft.")

  subject = payload.get("subject")
  body = payload.get("body")
  html_body = payload.get("html_body")

  return update_draft(
    credentials=creds,
    draft_id=draft_id,
    to=_addr_field_optional_str(payload.get("to")),
    subject=str(subject) if subject is not None else None,
    body=str(body) if body is not None else None,
    cc=_addr_field_optional_str(payload.get("cc")),
    bcc=_addr_field_optional_str(payload.get("bcc")),
    html_body=str(html_body) if html_body else None,
  )


def gmail_add_recipients_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Append recipients to an existing draft, preserving the current list."""
  creds = _require_creds(user_id)
  draft_id = str(payload.get("draft_id") or "").strip()
  if not draft_id:
    raise ToolError("draft_id is required to add recipients.")

  to_add = _addr_field_list(payload.get("to_add"))
  cc_add = _addr_field_list(payload.get("cc_add"))
  bcc_add = _addr_field_list(payload.get("bcc_add"))
  if not (to_add or cc_add or bcc_add):
    raise ToolError("At least one of to_add / cc_add / bcc_add is required.")

  return add_recipients_to_draft(
    credentials=creds,
    draft_id=draft_id,
    to_add=to_add or None,
    cc_add=cc_add or None,
    bcc_add=bcc_add or None,
  )


def gmail_remove_recipients_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Remove specific recipients from an existing draft (case-insensitive match)."""
  creds = _require_creds(user_id)
  draft_id = str(payload.get("draft_id") or "").strip()
  if not draft_id:
    raise ToolError("draft_id is required to remove recipients.")

  to_remove = _addr_field_list(payload.get("to_remove"))
  cc_remove = _addr_field_list(payload.get("cc_remove"))
  bcc_remove = _addr_field_list(payload.get("bcc_remove"))
  if not (to_remove or cc_remove or bcc_remove):
    raise ToolError("At least one of to_remove / cc_remove / bcc_remove is required.")

  return remove_recipients_from_draft(
    credentials=creds,
    draft_id=draft_id,
    to_remove=to_remove or None,
    cc_remove=cc_remove or None,
    bcc_remove=bcc_remove or None,
  )


def gmail_reply_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Reply to the most recent message in a thread (recipient defaults to original sender)."""
  creds = _require_creds(user_id)
  thread_id = str(payload.get("thread_id") or payload.get("threadId") or "").strip()
  if not thread_id:
    raise ToolError("thread_id is required to reply.")
  body = str(payload.get("body") or "")
  if not body.strip():
    raise ToolError("body is required for a reply.")
  html_raw = payload.get("html_body")
  html_body = str(html_raw).strip() if html_raw not in (None, "") else None
  cc = payload.get("cc")

  return reply_to_thread(
    credentials=creds,
    thread_id=thread_id,
    body=body,
    html_body=html_body,
    cc=cc,
  )


def gmail_reply_all_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Reply to all participants in a thread (excluding the authenticated user)."""
  creds = _require_creds(user_id)
  thread_id = str(payload.get("thread_id") or payload.get("threadId") or "").strip()
  if not thread_id:
    raise ToolError("thread_id is required to reply-all.")
  body = str(payload.get("body") or "")
  if not body.strip():
    raise ToolError("body is required for reply-all.")
  html_raw = payload.get("html_body")
  html_body = str(html_raw).strip() if html_raw not in (None, "") else None

  return reply_all_in_thread(
    credentials=creds,
    thread_id=thread_id,
    body=body,
    html_body=html_body,
  )


def gmail_forward_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Forward an existing inbox message to new recipients (text body only; no attachments)."""
  creds = _require_creds(user_id)
  message_id = str(payload.get("message_id") or payload.get("messageId") or "").strip()
  if not message_id:
    raise ToolError("message_id is required to forward.")
  to = payload.get("to")
  to_str = _addr_field_str(to)
  if not to_str:
    raise ToolError("Forward requires at least one recipient (to).")
  body = payload.get("body")
  html_raw = payload.get("html_body")
  html_body = str(html_raw).strip() if html_raw not in (None, "") else None

  return forward_message(
    credentials=creds,
    message_id=message_id,
    to=to_str,
    body=str(body) if body is not None else None,
    html_body=html_body,
  )


def gmail_archive_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Remove a message from the inbox (archive). Does not delete."""
  creds = _require_creds(user_id)
  message_id = str(payload.get("message_id") or payload.get("messageId") or "").strip()
  if not message_id:
    raise ToolError("message_id is required to archive an email.")
  result = archive_message(creds, message_id)
  return {
    "id": message_id,
    "status": "archived",
    "label_ids": result.get("labelIds", []),
  }


def gmail_delete_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Move a message to Gmail Trash (recoverable for 30 days). Never permanently deletes."""
  creds = _require_creds(user_id)
  message_id = str(payload.get("message_id") or payload.get("messageId") or "").strip()
  if not message_id:
    raise ToolError("message_id is required to delete an email.")
  return trash_message(creds, message_id)


def gmail_send_draft_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """
  Send an existing Gmail draft by draft_id.
  Uses Gmail's drafts.send API — the draft is automatically moved to Sent
  and removed from Drafts. No body needed; content is taken from the stored draft.
  """
  creds = _require_creds(user_id)
  draft_id = str(payload.get("draft_id") or "").strip()
  if not draft_id:
    raise ToolError("draft_id is required to send a draft. Use gmail_list_recent with q='in:drafts' to find it.")
  return send_draft(credentials=creds, draft_id=draft_id)
