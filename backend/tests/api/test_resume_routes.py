"""The resume PDF the UI can read and replace (operator request
2026-09-22).

The attachment was the one part of an outgoing email nothing in the UI
could show: the letter was reviewable down to the byte while the file
beside it was a filename in a footer.
"""
import pytest

from app.config import get_operator_settings

PDF = b"%PDF-1.4\nnot really a pdf but it starts like one\n%%EOF\n"


@pytest.fixture
def resume_file(tmp_path, monkeypatch):
    path = tmp_path / "resume.pdf"
    path.write_bytes(PDF)
    monkeypatch.setenv("RESUME_PATH", str(path))
    get_operator_settings.cache_clear()
    yield path
    get_operator_settings.cache_clear()


@pytest.fixture
def no_resume_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RESUME_PATH", str(tmp_path / "missing.pdf"))
    get_operator_settings.cache_clear()
    yield
    get_operator_settings.cache_clear()


def test_get_resume_serves_the_file_send_actually_attaches(client, resume_file):
    r = client.get("/api/resume")
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    # inline, not an attachment: the point is to look at it before sending.
    assert "inline" in r.headers["content-disposition"]


def test_get_resume_is_404_when_there_is_no_file(client, no_resume_file):
    assert client.get("/api/resume").status_code == 404


def test_status_reports_the_file_and_what_the_drafter_knows(client, resume_file):
    body = client.get("/api/resume/status").json()
    assert body["configured"] is True
    assert body["filename"] == "resume.pdf"
    assert body["size_bytes"] == len(PDF)
    # The claims count comes from the PARSED profile, not this file - the
    # two can disagree, which is exactly why it is reported.
    assert "claims" in body


def test_status_says_so_when_nothing_is_configured(client, no_resume_file):
    body = client.get("/api/resume/status").json()
    assert body["configured"] is False and body["filename"] is None


def test_replacing_the_resume_writes_the_new_bytes(client, resume_file):
    new = b"%PDF-1.7\na different resume\n%%EOF\n"
    r = client.post("/api/resume", files={"file": ("new.pdf", new, "application/pdf")})
    assert r.status_code == 200
    assert resume_file.read_bytes() == new
    assert r.json()["size_bytes"] == len(new)


def test_replacing_the_resume_keeps_the_old_one(client, resume_file):
    """Overwriting the only copy of a resume on a mis-click is not a
    failure this app should be capable of."""
    client.post("/api/resume", files={"file": ("new.pdf", b"%PDF-1.7\nnew\n", "application/pdf")})
    backups = [p for p in resume_file.parent.iterdir() if p.name != resume_file.name]
    assert len(backups) == 1
    assert backups[0].read_bytes() == PDF


def test_a_non_pdf_is_refused_and_nothing_is_written(client, resume_file):
    r = client.post("/api/resume", files={"file": ("cv.docx", b"PK\x03\x04 zip", "application/zip")})
    assert r.status_code == 400
    assert "not a PDF" in r.json()["detail"]
    assert resume_file.read_bytes() == PDF
    assert list(resume_file.parent.iterdir()) == [resume_file]


def test_an_empty_upload_is_refused(client, resume_file):
    r = client.post("/api/resume", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400
    assert resume_file.read_bytes() == PDF


def test_an_oversized_upload_is_refused(client, resume_file):
    huge = b"%PDF-1.4" + b"x" * (10 * 1024 * 1024 + 1)
    r = client.post("/api/resume", files={"file": ("huge.pdf", huge, "application/pdf")})
    assert r.status_code == 400
    assert resume_file.read_bytes() == PDF


def test_replacing_works_when_there_was_no_file_before(client, no_resume_file):
    r = client.post("/api/resume", files={"file": ("first.pdf", PDF, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["configured"] is True


# --- parsing it from the UI (operator request 2026-09-23) -----------------
#
# Parsing used to be `python -m app.cli parse-resume PATH` and nothing
# else, which made a brand-new operator's first Draft a dead end: it
# fails until the resume has been read, and the UI had nowhere to read it
# from.

TEXT_RESUME = "Built a scheduler in Python, cut wait 50%\nWon Smart India Hackathon 2025\n"


@pytest.fixture
def text_resume(tmp_path, monkeypatch):
    """A .txt resume: `read_resume_text` only reaches for pypdf on a
    `.pdf` suffix, so this exercises the route without a real PDF."""
    path = tmp_path / "resume.txt"
    path.write_text(TEXT_RESUME, encoding="utf-8")
    monkeypatch.setenv("RESUME_PATH", str(path))
    get_operator_settings.cache_clear()
    yield path
    get_operator_settings.cache_clear()


def _claim(text, span):
    return {"kind": "project", "text": text, "source_span": span}


def test_parsing_stores_the_claims_a_letter_may_assert(client, repos, text_resume, fake_parser):
    fake_parser.claims = [_claim("built a scheduler in Python",
                                 "Built a scheduler in Python, cut wait 50%")]
    body = client.post("/api/resume/parse").json()

    assert body["claims"] == 1
    assert body["status"]["claims"] == 1
    assert len(repos.resume_profile.get_current().claims) == 1


def test_the_model_is_handed_the_resume_text_not_a_path(client, text_resume, fake_parser):
    fake_parser.claims = [_claim("built a scheduler in Python",
                                 "Built a scheduler in Python, cut wait 50%")]
    client.post("/api/resume/parse")
    assert "Built a scheduler in Python" in fake_parser.text
    assert str(text_resume) not in fake_parser.text


def test_a_claim_whose_span_is_not_in_the_resume_is_counted_separately(
    client, text_resume, fake_parser
):
    # "it worked" and "it worked and threw half of it away" must not look
    # the same on screen.
    fake_parser.claims = [
        _claim("built a scheduler in Python", "Built a scheduler in Python, cut wait 50%"),
        _claim("led a team of forty", "Led a team of forty"),
    ]
    body = client.post("/api/resume/parse").json()
    assert body["claims"] + body["dropped"] == 2
    assert body["unverified"] + body["dropped"] >= 1


def test_a_parse_that_produces_nothing_leaves_the_old_claims_alone(
    client, repos, text_resume, fake_parser
):
    repos.resume_profile.save("an older resume", [_claim("something true", "something true")])
    fake_parser.claims = []
    r = client.post("/api/resume/parse")
    assert r.status_code == 422
    assert len(repos.resume_profile.get_current().claims) == 1


def test_no_resume_file_is_404_rather_than_a_model_call(client, no_resume_file, fake_parser):
    assert client.post("/api/resume/parse").status_code == 404
    assert fake_parser.text is None


def test_an_unset_resume_path_says_which_setting_is_missing(client, monkeypatch, fake_parser):
    monkeypatch.delenv("RESUME_PATH", raising=False)
    get_operator_settings.cache_clear()
    r = client.post("/api/resume/parse")
    assert r.status_code == 400
    assert "RESUME_PATH" in r.json()["detail"]
    get_operator_settings.cache_clear()


def test_a_provider_failure_never_leaks_its_response(client, repos, text_resume):
    class Boom:
        def parse(self, raw_text):
            raise RuntimeError("401 from provider: key sk_live_secret is bad")

    repos.parser = Boom()
    r = client.post("/api/resume/parse")
    assert r.status_code == 502
    assert "sk_live_secret" not in r.text
    assert "RuntimeError" in r.json()["detail"]
