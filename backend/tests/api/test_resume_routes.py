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
