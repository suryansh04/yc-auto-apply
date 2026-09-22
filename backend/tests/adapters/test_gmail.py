"""Task 15: the real `MailSender` adapter over Gmail's REST API.

Every HTTP call is mocked with `respx`; nothing here touches the network.
`build_mime` is checked by parsing the bytes back with the stdlib `email`
package rather than by string-matching, so the assertions are about the
actual message structure a mail client would see (D10: plain text, one
PDF attachment, no HTML part, no tracking).
"""

import base64
import email
import json
import logging
from email import policy

import httpx
import pytest
import respx

from app.adapters.gmail import (
    SEND_URL,
    TOKEN_URL,
    GmailError,
    GmailSender,
    build_mime,
    encode_raw,
)

CLIENT_ID = "client-id.apps.googleusercontent.com"
CLIENT_SECRET = "top-secret-client-secret"
REFRESH_TOKEN = "1//refresh-token-never-logged"
ACCESS_TOKEN = "ya29.access-token-never-logged"
SENDER = "me@gmail.com"
BODY = "Hi Madhav,\n\nYou route calls over voice.\n\nSuryansh\n\nReply no and I won't follow up."


@pytest.fixture
def resume(tmp_path):
    p = tmp_path / "Suryansh_Singh_Resume.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake resume bytes\n%%EOF\n")
    return p


class Ticker:
    def __init__(self, at=1000.0):
        self.at = at

    def __call__(self):
        return self.at


def make_sender(resume=None, now=None, http=None):
    return GmailSender(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN,
        sender_address=SENDER, http=http, now=now or Ticker(),
    )


def token_ok(expires_in=3600):
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": expires_in,
                                     "token_type": "Bearer"})


def send_ok():
    return httpx.Response(200, json={"id": "18f3abc", "threadId": "18f3abc-thread",
                                     "labelIds": ["SENT"]})


def parse_raw(raw: str):
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


# --- MIME ---------------------------------------------------------------------


def test_mime_is_plain_text_with_one_pdf_attachment(resume):
    msg = email.message_from_bytes(
        build_mime(SENDER, "madhav@example.com", "Backend intern at Vahan", BODY, str(resume)),
        policy=policy.default,
    )
    assert msg["To"] == "madhav@example.com"
    assert msg["From"] == SENDER
    assert msg["Subject"] == "Backend intern at Vahan"
    assert msg.is_multipart()
    parts = list(msg.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "application/pdf"]
    assert parts[0].get_content() == BODY + "\n"  # MIME text bodies are newline-terminated
    assert parts[1].get_filename() == "Suryansh_Singh_Resume.pdf"
    assert parts[1].get_content() == resume.read_bytes()
    assert parts[1].get_content_disposition() == "attachment"


def test_mime_has_no_html_part_and_no_tracking(resume):
    raw = build_mime(SENDER, "a@b.com", "s", BODY, str(resume))
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert all(p.get_content_type() != "text/html" for p in msg.walk())
    assert b"<img" not in raw and b"http://" not in raw and b"https://" not in raw


def test_mime_without_attachment_is_a_single_plain_text_part():
    msg = email.message_from_bytes(build_mime(SENDER, "a@b.com", "s", BODY, None), policy=policy.default)
    assert not msg.is_multipart()
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content() == BODY + "\n"


def test_mime_preserves_non_ascii_body(resume):
    body = "Hi Madhav,\n\nThat's the part I keep thinking about — speech is a brutal input.\n"
    msg = email.message_from_bytes(build_mime(SENDER, "a@b.com", "s", body, str(resume)),
                                   policy=policy.default)
    assert list(msg.iter_parts())[0].get_content() == body


def test_missing_attachment_raises_before_any_http_call(tmp_path):
    with respx.mock(assert_all_called=False) as router:
        token = router.post(TOKEN_URL).mock(return_value=token_ok())
        send = router.post(SEND_URL).mock(return_value=send_ok())
        with pytest.raises(FileNotFoundError):
            make_sender().send("a@b.com", "s", BODY, str(tmp_path / "nope.pdf"))
        assert not token.called and not send.called


def test_encode_raw_is_urlsafe_base64_of_the_bytes():
    raw = encode_raw(b"hello\xff\xfe>>??")
    assert "+" not in raw and "/" not in raw
    assert base64.urlsafe_b64decode(raw) == b"hello\xff\xfe>>??"


# --- send ---------------------------------------------------------------------


def test_send_posts_raw_message_and_returns_ids(resume):
    with respx.mock() as router:
        router.post(TOKEN_URL).mock(return_value=token_ok())
        send = router.post(SEND_URL).mock(return_value=send_ok())
        ids = make_sender().send("madhav@example.com", "Backend intern at Vahan", BODY, str(resume))

    assert ids == ("18f3abc", "18f3abc-thread")
    request = send.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    payload = json.loads(request.content)
    assert set(payload) == {"raw"}
    msg = parse_raw(payload["raw"])
    assert msg["To"] == "madhav@example.com"
    assert msg["Subject"] == "Backend intern at Vahan"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "application/pdf"]


def test_refresh_token_is_exchanged_once_and_the_access_token_reused(resume):
    with respx.mock() as router:
        token = router.post(TOKEN_URL).mock(return_value=token_ok())
        router.post(SEND_URL).mock(return_value=send_ok())
        s = make_sender()
        s.send("a@b.com", "s", BODY, str(resume))
        s.send("c@d.com", "s", BODY, str(resume))

    assert token.call_count == 1
    form = dict(httpx.QueryParams(token.calls[0].request.content.decode()))
    assert form == {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                    "refresh_token": REFRESH_TOKEN, "grant_type": "refresh_token"}


def test_expired_access_token_is_refreshed(resume):
    clock = Ticker(1000.0)
    with respx.mock() as router:
        token = router.post(TOKEN_URL).mock(return_value=token_ok(expires_in=3600))
        router.post(SEND_URL).mock(return_value=send_ok())
        s = make_sender(now=clock)
        s.send("a@b.com", "s", BODY, str(resume))
        clock.at += 3600 - 30          # inside the safety margin: still expired
        s.send("a@b.com", "s", BODY, str(resume))
    assert token.call_count == 2


def test_a_401_on_send_refreshes_once_and_retries(resume):
    with respx.mock() as router:
        token = router.post(TOKEN_URL).mock(return_value=token_ok())
        send = router.post(SEND_URL).mock(side_effect=[
            httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}}),
            send_ok(),
        ])
        ids = make_sender().send("a@b.com", "s", BODY, str(resume))
    assert ids == ("18f3abc", "18f3abc-thread")
    assert send.call_count == 2 and token.call_count == 2


def test_persistent_401_raises_after_one_retry(resume):
    with respx.mock() as router:
        router.post(TOKEN_URL).mock(return_value=token_ok())
        send = router.post(SEND_URL).mock(return_value=httpx.Response(401, json={"error": "x"}))
        with pytest.raises(GmailError) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    assert exc.value.status == 401
    assert send.call_count == 2


@pytest.mark.parametrize("status", [403, 429, 500])
def test_non_2xx_send_raises_gmail_error_with_status(resume, status):
    with respx.mock() as router:
        router.post(TOKEN_URL).mock(return_value=token_ok())
        router.post(SEND_URL).mock(return_value=httpx.Response(
            status, json={"error": {"message": f"boom {ACCESS_TOKEN}"}}))
        with pytest.raises(GmailError) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    assert exc.value.status == status
    assert ACCESS_TOKEN not in str(exc.value)


def test_refresh_failure_raises_gmail_error_without_secrets(resume):
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
        send = router.post(SEND_URL).mock(return_value=send_ok())
        with pytest.raises(GmailError) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    text = str(exc.value)
    assert exc.value.status == 400
    assert "invalid_grant" in text                 # the operator needs to know to re-run gmail-auth
    assert REFRESH_TOKEN not in text and CLIENT_SECRET not in text
    # Fix round 1 (F3): the contract is "short OAuth code only" - never the
    # free-text description, never the whole body.
    assert "Token has been expired" not in text
    assert "error_description" not in text
    assert not send.called


def test_refresh_error_code_that_is_not_a_short_string_is_dropped(resume):
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=httpx.Response(
            400, json={"error": {"code": 400, "message": "nested " + REFRESH_TOKEN}}))
        router.post(SEND_URL).mock(return_value=send_ok())
        with pytest.raises(GmailError) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    assert str(exc.value) == "gmail token refresh failed (HTTP 400)"


def test_transport_error_raises_gmail_error(resume):
    with respx.mock() as router:
        router.post(TOKEN_URL).mock(return_value=token_ok())
        router.post(SEND_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(GmailError) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    assert exc.value.status is None


def test_malformed_send_response_raises_gmail_error(resume):
    with respx.mock() as router:
        router.post(TOKEN_URL).mock(return_value=token_ok())
        router.post(SEND_URL).mock(return_value=httpx.Response(200, json={"labelIds": ["SENT"]}))
        with pytest.raises(GmailError):
            make_sender().send("a@b.com", "s", BODY, str(resume))


def test_no_secret_reaches_any_log_record(resume, caplog):
    """Pins this adapter's own log lines and `httpx._client`'s INFO
    "HTTP Request" line. Fix round 1 (F7): respx intercepts at the httpx
    transport, so httpcore's DEBUG trace logger is NOT exercised here -
    the reviewer checked that path separately against a real HTTP server
    at root DEBUG (no leak)."""
    with caplog.at_level(logging.DEBUG):
        with respx.mock() as router:
            router.post(TOKEN_URL).mock(return_value=token_ok())
            router.post(SEND_URL).mock(side_effect=[
                httpx.Response(500, json={"error": ACCESS_TOKEN}), send_ok()])
            s = make_sender()
            with pytest.raises(GmailError):
                s.send("a@b.com", "s", BODY, str(resume))
            s.send("a@b.com", "s", BODY, str(resume))
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert ACCESS_TOKEN not in joined
    assert REFRESH_TOKEN not in joined
    assert CLIENT_SECRET not in joined


def test_secrets_are_not_in_repr():
    s = make_sender()
    assert REFRESH_TOKEN not in repr(s) and CLIENT_SECRET not in repr(s)


# --- Final fix wave (F2): a dead credential is the `GmailAuthRequired` subtype


def test_an_invalid_grant_on_refresh_is_gmail_auth_required(resume):
    """`invalid_grant` (refresh token revoked/expired) is not something a
    retry can cure - the operator must re-run `gmail-auth`. The shared
    exchange raises the `GmailAuthRequired` SUBTYPE so a caller that
    wants to tell that apart from a transient failure (`PollReplies`,
    then the job runner, then the Pipeline banner) can - while every
    existing `except GmailError` still catches it. Message discipline is
    unchanged: code and status only, never a secret or the body."""
    from app.ports import GmailAuthRequired

    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
        router.post(SEND_URL).mock(return_value=send_ok())
        with pytest.raises(GmailAuthRequired) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    text = str(exc.value)
    assert isinstance(exc.value, GmailError)
    assert exc.value.status == 400 and exc.value.reason == "invalid_grant"
    assert REFRESH_TOKEN not in text and CLIENT_SECRET not in text
    assert "Token has been expired" not in text


@pytest.mark.parametrize("status, body", [
    (401, {"error": "unauthorized_client"}),
    (400, {"error": "invalid_client"}),
])
def test_a_401_or_invalid_client_on_refresh_is_gmail_auth_required(resume, status, body):
    from app.ports import GmailAuthRequired

    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=httpx.Response(status, json=body))
        router.post(SEND_URL).mock(return_value=send_ok())
        with pytest.raises(GmailAuthRequired):
            make_sender().send("a@b.com", "s", BODY, str(resume))


def test_a_transient_refresh_failure_is_a_plain_gmail_error_not_auth_required(resume):
    """The boundary of F2: a 500 / `temporarily_unavailable` from the
    token endpoint is exactly the kind of failure the next scheduled
    poll SHOULD retry, so it must stay a plain `GmailError` - widening
    `GmailAuthRequired` to every refresh failure would fail the poll job
    on a Google blip and tell the operator to re-auth for nothing."""
    from app.ports import GmailAuthRequired

    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(return_value=httpx.Response(
            503, json={"error": "temporarily_unavailable"}))
        router.post(SEND_URL).mock(return_value=send_ok())
        with pytest.raises(GmailError) as exc:
            make_sender().send("a@b.com", "s", BODY, str(resume))
    assert not isinstance(exc.value, GmailAuthRequired)
