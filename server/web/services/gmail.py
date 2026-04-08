"""
Gmail Service -- send emails using stored OAuth2 tokens.
"""

import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def list_recent_messages(
    credentials,
    max_results: int = 10,
    q: str = "",
) -> list[dict]:
    """
    List recent message metadata (From, Subject, Date, snippet, threadId).
    Requires gmail.readonly scope.
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    max_results = max(1, min(int(max_results), 30))
    kwargs: dict = {"userId": "me", "maxResults": max_results}
    if q and q.strip():
        kwargs["q"] = q.strip()
    results = service.users().messages().list(**kwargs).execute()
    messages = results.get("messages", [])
    out: list[dict] = []
    for ref in messages:
        mid = ref.get("id")
        if not mid:
            continue
        m = service.users().messages().get(
            userId="me",
            id=mid,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
        out.append({
            "id": mid,
            "threadId": m.get("threadId"),
            "snippet": m.get("snippet", ""),
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
        })
    return out


def send_email(
    credentials,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    thread_id: str | None = None,
) -> dict:
    """
    Send an email via Gmail API.

    Args:
        credentials: google.oauth2.credentials.Credentials with gmail.send scope
        to: recipient email
        subject: email subject
        body: plain text body
        html_body: optional HTML body
        cc: optional CC
        bcc: optional BCC

    Returns:
        Gmail API response dict with 'id', 'threadId', 'labelIds'
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    if html_body:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(body, "plain"))
        message.attach(MIMEText(html_body, "html"))
    else:
        message = MIMEText(body, "plain")

    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    send_body: dict = {"raw": raw}
    if thread_id and str(thread_id).strip():
        send_body["threadId"] = str(thread_id).strip()

    result = (
        service.users()
        .messages()
        .send(userId="me", body=send_body)
        .execute()
    )

    logger.info("Email sent: id=%s to=%s subject=%s", result.get("id"), to, subject)
    return result


def create_draft(
    credentials,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
) -> dict:
    """
    Create a Gmail draft (does not send). Requires gmail.compose scope.
    """
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    message = MIMEText(body, "plain")
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    draft_body = {"message": {"raw": raw}}
    result = (
        service.users()
        .drafts()
        .create(userId="me", body=draft_body)
        .execute()
    )
    logger.info("Gmail draft created: id=%s subject=%s", result.get("id"), subject)
    return result


def get_user_email(credentials) -> str:
    """Return the authenticated Gmail user's email address."""
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")
