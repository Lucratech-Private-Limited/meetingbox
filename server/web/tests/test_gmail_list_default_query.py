"""Gmail list default search query when `q` is omitted and strict inbox filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_message_metadata(message_id: str, frm: str, subj: str, snippet: str = "") -> dict:
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "From", "value": frm},
                {"name": "Subject", "value": subj},
                {"name": "Date", "value": "Mon, 1 Jan 2024 12:00:00 +0000"},
            ],
        },
    }


@patch("services.gmail.build")
def test_list_recent_messages_applies_default_q(mock_build, monkeypatch):
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_DEFAULT_Q", raising=False)
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_MERGE", raising=False)
    monkeypatch.delenv("MEETINGBOX_GMAIL_STRICT_INBOX", raising=False)

    msg_list = MagicMock()
    msg_list.execute.return_value = {"messages": [{"id": "m1"}]}
    msg_get = MagicMock()
    msg_get.execute.return_value = _make_message_metadata(
        "m1", "bob@example.com", "Hello", ""
    )
    messages_api = MagicMock()
    messages_api.list.return_value = msg_list
    messages_api.get.return_value = msg_get
    users_api = MagicMock()
    users_api.messages.return_value = messages_api
    service = MagicMock()
    service.users.return_value = users_api
    mock_build.return_value = service

    from services.gmail import list_recent_messages

    rows = list_recent_messages(object(), max_results=5, q="")

    kwargs = messages_api.list.call_args.kwargs
    assert "q" in kwargs
    assert "category:primary" in kwargs["q"]
    assert "filename:ics" in kwargs["q"]
    assert len(rows) == 1
    assert rows[0]["subject"] == "Hello"


@patch("services.gmail.build")
def test_list_recent_messages_respects_explicit_q(mock_build, monkeypatch):
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_DEFAULT_Q", raising=False)
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_MERGE", raising=False)
    monkeypatch.delenv("MEETINGBOX_GMAIL_STRICT_INBOX", raising=False)

    msg_list = MagicMock()
    msg_list.execute.return_value = {"messages": []}
    messages_api = MagicMock()
    messages_api.list.return_value = msg_list
    users_api = MagicMock()
    users_api.messages.return_value = messages_api
    service = MagicMock()
    service.users.return_value = users_api
    mock_build.return_value = service

    from services.gmail import list_recent_messages

    list_recent_messages(object(), max_results=5, q="in:spam")

    kwargs = messages_api.list.call_args.kwargs
    assert kwargs.get("q") == "in:spam"


@patch("services.gmail.build")
def test_list_recent_messages_merges_is_unread_with_scope(mock_build, monkeypatch):
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_DEFAULT_Q", raising=False)
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_MERGE", raising=False)
    monkeypatch.delenv("MEETINGBOX_GMAIL_STRICT_INBOX", raising=False)

    msg_list = MagicMock()
    msg_list.execute.return_value = {"messages": []}
    messages_api = MagicMock()
    messages_api.list.return_value = msg_list
    users_api = MagicMock()
    users_api.messages.return_value = messages_api
    service = MagicMock()
    service.users.return_value = users_api
    mock_build.return_value = service

    from services.gmail import list_recent_messages

    list_recent_messages(object(), max_results=5, q="is:unread")

    kwargs = messages_api.list.call_args.kwargs
    q = kwargs.get("q", "")
    assert "is:unread" in q
    assert "category:primary" in q


@patch("services.gmail.build")
def test_merge_disabled_uses_raw_q(mock_build, monkeypatch):
    monkeypatch.delenv("MEETINGBOX_GMAIL_LIST_DEFAULT_Q", raising=False)
    monkeypatch.setenv("MEETINGBOX_GMAIL_LIST_MERGE", "0")
    monkeypatch.delenv("MEETINGBOX_GMAIL_STRICT_INBOX", raising=False)

    msg_list = MagicMock()
    msg_list.execute.return_value = {"messages": []}
    messages_api = MagicMock()
    messages_api.list.return_value = msg_list
    users_api = MagicMock()
    users_api.messages.return_value = messages_api
    service = MagicMock()
    service.users.return_value = users_api
    mock_build.return_value = service

    from services.gmail import list_recent_messages

    list_recent_messages(object(), max_results=5, q="is:unread")

    kwargs = messages_api.list.call_args.kwargs
    assert kwargs.get("q") == "is:unread"


def test_env_overrides_default_gmail_query(monkeypatch):
    monkeypatch.setenv("MEETINGBOX_GMAIL_LIST_DEFAULT_Q", "in:starred")
    from services import gmail as gmail_mod

    assert gmail_mod.default_gmail_list_query() == "in:starred"


def test_strict_inbox_blocks_bulk_and_keeps_individual_and_calendar(monkeypatch):
    monkeypatch.delenv("MEETINGBOX_GMAIL_STRICT_INBOX", raising=False)
    from services import gmail as gmail_mod

    show = gmail_mod.should_show_in_personal_inbox
    assert show("noreply@amazon.com", "Your receipt for order 1", "") is False
    assert show(
        "digest@quora.com",
        "Digest for you",
        "Click unsubscribe to stop weekly digests.",
    ) is False
    assert show(
        "Docker <no-reply@notify.docker.com>",
        "Welcome",
        "verified your account",
        headers_lc={"list-unsubscribe": "<https://example.com/unsub>"},
    ) is False
    assert show(
        "Google Antigravity <antigravity-noreply@google.com>",
        "Get started with Google Antigravity",
        "Welcome to Antigravity",
    ) is False
    assert show("user@gmail.com", "Security alert", "") is False
    assert (
        show(
            "Google Calendar <calendar-notification@google.com>",
            "Invitation: Q4 planning",
            "",
        )
        is True
    )
    assert show("colleague@company.com", "Lunch tomorrow?", "") is True
    assert show(
        "LinkedIn <invitations@linkedin.com>",
        "Invitation to connect",
        "",
        label_ids=["CATEGORY_SOCIAL"],
    ) is False


def test_soft_mode_hides_list_unsubscribe_without_strict(monkeypatch):
    monkeypatch.setenv("MEETINGBOX_GMAIL_STRICT_INBOX", "0")
    monkeypatch.delenv("MEETINGBOX_GMAIL_POSTFILTER", raising=False)
    from services import gmail as gmail_mod

    assert gmail_mod._should_hide_list_row_soft(
        "Welcome to the product",
        "Brand <hello@example.com>",
        "Thanks for signing up",
        headers_lc={"list-unsubscribe": "<mailto:unsub@example.com>"},
    ) is True


def test_soft_legacy_ignores_postfilter_when_disabled(monkeypatch):
    monkeypatch.setenv("MEETINGBOX_GMAIL_STRICT_INBOX", "0")
    monkeypatch.setenv("MEETINGBOX_GMAIL_POSTFILTER", "0")
    from services import gmail as gmail_mod

    assert gmail_mod._should_hide_list_row_soft("Your receipt", "noreply@store.com", "") is False
