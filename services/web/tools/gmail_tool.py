"""Gmail tool adapter — send mail via stored OAuth (Phase 3)."""

from __future__ import annotations

from typing import Any

from routes.integrations import get_credentials_for_provider
from services.gmail import send_email
from tools.base_tool import ToolError


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
  cc = payload.get("cc")
  cc_str = ", ".join(cc) if isinstance(cc, list) else (str(cc) if cc else None)
  return send_email(
    credentials=creds,
    to=to,
    subject=subject,
    body=body,
    cc=cc_str,
  )
