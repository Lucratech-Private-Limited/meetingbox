"""Gmail tool adapter — list + send + draft via stored OAuth (communication agent)."""

from __future__ import annotations

from typing import Any

from routes.integrations import get_credentials_for_provider
from services.gmail import create_draft, list_recent_messages, send_email
from tools.base_tool import ToolError


def gmail_list_recent(
    user_id: str,
    max_results: int = 10,
    q: str = "",
) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to read Gmail.")
  creds = get_credentials_for_provider(user_id, "gmail")
  if not creds:
    raise ToolError("Gmail is not connected. Connect it in Settings.")
  max_results = max(1, min(int(max_results), 30))
  messages = list_recent_messages(creds, max_results=max_results, q=q or "")
  return {"messages": messages, "count": len(messages)}


def gmail_send_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to send email.")
  creds = get_credentials_for_provider(user_id, "gmail")
  if not creds:
    raise ToolError("Gmail is not connected. Connect it in Settings.")
  raw_to = payload.get("to")
  if isinstance(raw_to, list):
    to = ", ".join(str(x).strip() for x in raw_to if str(x).strip())
  else:
    to = str(raw_to or "").strip()
  if not to:
    raise ToolError("Recipient address (to) is required.")
  subject = str(payload.get("subject") or "(no subject)")
  body = str(payload.get("body") or "")
  html_raw = payload.get("html_body")
  html_body = str(html_raw).strip() if html_raw not in (None, "") else None
  cc = payload.get("cc")
  cc_str = ", ".join(str(x) for x in cc) if isinstance(cc, list) else (str(cc) if cc else None)
  bcc = payload.get("bcc")
  if isinstance(bcc, list):
    bcc_str = ", ".join(str(x).strip() for x in bcc if str(x).strip())
  else:
    bcc_str = str(bcc).strip() if bcc else None
  thread_id = payload.get("thread_id") or payload.get("threadId")
  thread_id = str(thread_id).strip() if thread_id else None

  return send_email(
    credentials=creds,
    to=to,
    subject=subject,
    body=body,
    html_body=html_body,
    cc=cc_str,
    bcc=bcc_str or None,
    thread_id=thread_id,
  )


def gmail_draft_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  """Save email as a Gmail draft (does not send). Returns the draft id."""
  if not user_id:
    raise ToolError("Sign in is required to save drafts.")
  creds = get_credentials_for_provider(user_id, "gmail")
  if not creds:
    raise ToolError("Gmail is not connected. Connect it in Settings.")
  raw_to = payload.get("to")
  if isinstance(raw_to, list):
    to = ", ".join(str(x).strip() for x in raw_to if str(x).strip())
  else:
    to = str(raw_to or "").strip()
  subject = str(payload.get("subject") or "(no subject)")
  body = str(payload.get("body") or "")
  cc = payload.get("cc")
  cc_str = ", ".join(str(x) for x in cc) if isinstance(cc, list) else (str(cc) if cc else None)
  result = create_draft(credentials=creds, to=to, subject=subject, body=body, cc=cc_str)
  return {
    "draft_id": result.get("id"),
    "to": to,
    "subject": subject,
    "body_preview": body[:200],
    "saved_to_gmail_drafts": True,
  }
