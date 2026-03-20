"""
Gmail Service -- send emails using stored OAuth2 tokens.
"""

import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def send_email(
    credentials,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
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

    result = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )

    logger.info("Email sent: id=%s to=%s subject=%s", result.get("id"), to, subject)
    return result


def get_user_email(credentials) -> str:
    """Return the authenticated Gmail user's email address."""
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")
