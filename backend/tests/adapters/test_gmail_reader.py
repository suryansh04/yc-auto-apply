"""Reading replies (spec §5). All HTTP mocked with respx; the access token
and client secret must never appear in an exception or a log record."""
import httpx
import pytest
import respx

from app.adapters.gmail_reader import GmailHistoryExpired, GmailReader

HISTORY = "https://gmail.googleapis.com/gmail/v1/users/me/history"
MESSAGES = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

CLIENT_ID = "client-id.apps.googleusercontent.com"
CLIENT_SECRET = "top-secret-client-secret"
ACCOUNT = "me@gmail.com"


class _FakeCredential:
    """Stand-in for `app.adapters.db.models.GmailCredential` - only the
    field the reader's token refresh needs."""

    refresh_token = "1//refresh-token-never-used-in-these-tests"


class _FakeCredentialRepo:
    """Stand-in for `GmailCredentialRepo.get_by_account_email` - none of
    these tests exercise a real refresh (the fixture pre-seeds a cached
    access token), so this is never actually called; it exists so a
    refresh would work if one were ever triggered."""

    def get_by_account_email(self, account_email: str):
        assert account_email == ACCOUNT
        return _FakeCredential()


@pytest.fixture
def reader():
    r = GmailReader(_FakeCredentialRepo(), ACCOUNT, CLIENT_ID, CLIENT_SECRET)
    # Pre-seed the cached access token so these HTTP-endpoint-focused tests
    # never need `TOKEN_URL` mocked - the refresh exchange itself is
    # covered by `test_gmail.py` (shared helper, same code path). Naming
    # it like a real Google access token makes the "no token in the
    # exception" assertion below meaningful rather than vacuous.
    r._access_token = "ya29.pre-seeded-test-access-token"
    r._expires_at = float("inf")
    return r


@respx.mock
def test_history_returns_new_message_ids_and_the_new_cursor(reader):
    respx.get(HISTORY).mock(return_value=httpx.Response(200, json={
        "history": [{"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]},
                    {"messagesAdded": [{"message": {"id": "m2", "threadId": "t1"}}]}],
        "historyId": "555",
    }))
    ids, new_cursor = reader.history_since("500")
    assert ids == ["m1", "m2"] and new_cursor == "555"


@respx.mock
def test_history_pages_through_next_page_token(reader):
    respx.get(HISTORY).mock(side_effect=[
        httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                                  "nextPageToken": "p2", "historyId": "500"}),
        httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "m2"}}]}],
                                  "historyId": "600"}),
    ])
    ids, cursor = reader.history_since("400")
    assert ids == ["m1", "m2"] and cursor == "600"


@respx.mock
def test_an_expired_history_id_raises_so_the_caller_can_fall_back(reader):
    respx.get(HISTORY).mock(return_value=httpx.Response(404, json={"error": {"message": "not found"}}))
    with pytest.raises(GmailHistoryExpired):
        reader.history_since("1")


@respx.mock
def test_recent_message_ids_uses_the_newer_than_query(reader):
    route = respx.get(MESSAGES).mock(return_value=httpx.Response(200, json={
        "messages": [{"id": "m9", "threadId": "t9"}]}))
    assert reader.recent_message_ids() == ["m9"]
    assert "newer_than" in str(route.calls[0].request.url)


@respx.mock
def test_get_message_decodes_the_plain_text_body_and_headers(reader):
    import base64
    body = base64.urlsafe_b64encode("Sure, send it over.".encode()).decode()
    respx.get(f"{MESSAGES}/m1").mock(return_value=httpx.Response(200, json={
        "id": "m1", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {"headers": [{"name": "From", "value": "Pratish <pratish@onefin.in>"},
                                {"name": "Subject", "value": "Re: OneFin intern"}],
                    "mimeType": "text/plain", "body": {"data": body}},
    }))
    msg = reader.get_message("m1")
    assert msg.gmail_thread_id == "t1"
    assert msg.from_address == "pratish@onefin.in"
    assert "send it over" in msg.body_text
    assert msg.received_at.year == 2026


@respx.mock
def test_get_message_prefers_the_text_part_of_a_multipart_reply(reader):
    import base64
    text = base64.urlsafe_b64encode(b"plain version").decode()
    html = base64.urlsafe_b64encode(b"<p>html version</p>").decode()
    respx.get(f"{MESSAGES}/m2").mock(return_value=httpx.Response(200, json={
        "id": "m2", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {"headers": [{"name": "From", "value": "a@b.com"}], "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/html", "body": {"data": html}},
                              {"mimeType": "text/plain", "body": {"data": text}}]},
    }))
    assert reader.get_message("m2").body_text == "plain version"


@respx.mock
def test_a_401_never_carries_the_token(reader):
    respx.get(HISTORY).mock(return_value=httpx.Response(401, json={"error": {"message": "bad token"}}))
    with pytest.raises(Exception) as exc_info:
        reader.history_since("1")
    assert "ya29" not in str(exc_info.value)
