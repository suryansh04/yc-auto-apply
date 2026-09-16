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


# --- Fix round 1: pin the four behaviours the review found surviving under
# mutation - correct as shipped, but unguarded because the test file above
# is the brief's Step 1 content verbatim. Each test targets the exact
# regression the brief's own prose called out.


@respx.mock
@pytest.mark.parametrize("text", ["Yes!", "OK"])  # "Yes!" needs 2 '=' of padding, "OK" needs 1
def test_get_message_decodes_an_unpadded_base64_body(reader, text):
    """Gmail's `body.data` is base64url WITHOUT the trailing `=` padding
    Python's own `base64.urlsafe_b64encode` always emits; naive
    `urlsafe_b64decode` raises `binascii.Error: Incorrect padding` on
    that. Both padding-length cases (1 and 2 missing `=`) are covered."""
    import base64

    padded = base64.urlsafe_b64encode(text.encode()).decode()
    assert padded.endswith("=")
    unpadded = padded.rstrip("=")
    respx.get(f"{MESSAGES}/m-unpadded").mock(return_value=httpx.Response(200, json={
        "id": "m-unpadded", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {"headers": [{"name": "From", "value": "a@b.com"}],
                    "mimeType": "text/plain", "body": {"data": unpadded}},
    }))
    assert reader.get_message("m-unpadded").body_text == text


@respx.mock
def test_get_message_finds_plain_text_nested_inside_multipart_mixed(reader):
    """A real reply with an attachment arrives as `multipart/mixed`
    wrapping a `multipart/alternative` (html + plain) part - one level
    deeper than the flat `multipart/alternative` the other test covers.
    A one-level-only part walk would silently return an empty body for
    every such reply."""
    import base64

    text = base64.urlsafe_b64encode(b"plain reply text").decode()
    html = base64.urlsafe_b64encode(b"<p>html reply text</p>").decode()
    pdf = base64.urlsafe_b64encode(b"%PDF-1.4 fake resume bytes").decode()
    respx.get(f"{MESSAGES}/m-nested").mock(return_value=httpx.Response(200, json={
        "id": "m-nested", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {
            "headers": [{"name": "From", "value": "a@b.com"}],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/html", "body": {"data": html}},
                    {"mimeType": "text/plain", "body": {"data": text}},
                ]},
                {"mimeType": "application/pdf", "body": {"data": pdf, "attachmentId": "a1"}},
            ],
        },
    }))
    assert reader.get_message("m-nested").body_text == "plain reply text"


@respx.mock
def test_get_message_with_no_body_part_returns_empty_string(reader):
    """A message with no `text/plain` leaf anywhere (e.g. image-only, or a
    body Gmail didn't inline) must return `""`, not raise - Task 8 decides
    what an empty body means, but a `KeyError`/`TypeError` here would take
    down the whole poll."""
    respx.get(f"{MESSAGES}/m-empty").mock(return_value=httpx.Response(200, json={
        "id": "m-empty", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {"headers": [{"name": "From", "value": "a@b.com"}],
                    "mimeType": "text/plain", "body": {}},
    }))
    assert reader.get_message("m-empty").body_text == ""


@respx.mock
def test_a_401_body_carrying_a_fake_refresh_token_never_leaks(reader, caplog):
    """`test_a_401_never_carries_the_token` above only checks the literal
    string "ya29" (an access-token prefix). This pins the stronger claim
    the brief actually makes: the raw response BODY must never reach the
    exception or the logs, exercised with a secret shaped like a refresh
    token and a client secret sitting directly in the JSON body (a
    provider that ever did this would otherwise leak through
    `_error_code` if that helper were loosened to return arbitrary
    fields)."""
    import logging

    fake_refresh_token = "1//09-fake-refresh-token-should-never-leak"
    fake_client_secret = "GOCSPX-fake-client-secret-should-never-leak"
    respx.get(HISTORY).mock(return_value=httpx.Response(401, json={
        "error": {"message": "bad token", "refresh_token": fake_refresh_token,
                  "client_secret": fake_client_secret},
    }))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception) as exc_info:
            reader.history_since("1")
    joined_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert fake_refresh_token not in str(exc_info.value)
    assert fake_refresh_token not in repr(exc_info.value)
    assert fake_refresh_token not in joined_logs
    assert fake_client_secret not in str(exc_info.value)
    assert fake_client_secret not in repr(exc_info.value)
    assert fake_client_secret not in joined_logs


# --- Final fix wave (F2, spec §9): a 401 is `GmailAuthRequired`, never a
# plain transient error - and still never carries the token or the body.


@respx.mock
def test_a_401_from_gmail_is_gmail_auth_required_without_the_token(reader):
    from app.ports import GmailAuthRequired, GmailError

    respx.get(HISTORY).mock(return_value=httpx.Response(401, json={
        "error": {"code": 401, "message": "Invalid Credentials ya29.leaked-token", "status": "UNAUTHENTICATED"},
    }))
    with pytest.raises(GmailAuthRequired) as exc_info:
        reader.history_since("1")
    assert isinstance(exc_info.value, GmailError)
    assert exc_info.value.status == 401
    assert "ya29" not in str(exc_info.value) and "ya29" not in repr(exc_info.value)
    assert "Invalid Credentials" not in str(exc_info.value)


@respx.mock
@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_a_non_401_failure_stays_a_plain_gmail_error(reader, status):
    """The boundary: only a 401 means "the credential is dead". A 403
    (quota / scope), 429 or 5xx is retried by the next scheduled poll
    and must NOT tell the operator to re-run `gmail-auth`."""
    from app.ports import GmailAuthRequired, GmailError

    respx.get(HISTORY).mock(return_value=httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(GmailError) as exc_info:
        reader.history_since("1")
    assert not isinstance(exc_info.value, GmailAuthRequired)


def test_no_stored_credential_is_gmail_auth_required():
    """No refresh token to exchange at all (`gmail-auth` never run for
    this account, or the row cleared): same remedy as a revoked grant,
    same type. Message names no account address and no secret."""
    from app.ports import GmailAuthRequired

    class _EmptyRepo:
        def get_by_account_email(self, account_email: str):
            return None

    r = GmailReader(_EmptyRepo(), ACCOUNT, CLIENT_ID, CLIENT_SECRET)
    with pytest.raises(GmailAuthRequired) as exc_info:
        r.history_since("1")
    assert ACCOUNT not in str(exc_info.value)
    assert CLIENT_SECRET not in str(exc_info.value)
