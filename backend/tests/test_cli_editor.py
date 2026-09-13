"""Live finding (2026-09-13): `review` crashed with `ModuleNotFoundError:
No module named 'click'`. `_read_key`/`_edit_text` called click directly,
but click is not a declared dependency and typer 0.27 no longer installs
it — the commands worked only on a machine that happened to have click
from something else. Both helpers now use typer's own `getchar` and a
self-contained `$EDITOR` round-trip.
"""

import os
import sys

from app import cli


def test_no_module_imports_click_directly():
    with open(cli.__file__, encoding="utf-8") as fh:
        assert "import click" not in fh.read()


def test_read_key_uses_typer_getchar(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.typer, "getchar", lambda: seen.append(1) or "a")
    assert cli._read_key() == "a"
    assert seen == [1]


def _fake_editor(monkeypatch, tmp_path, script: str) -> None:
    """Install a tiny Python program as $EDITOR (argv[1] is the file)."""
    prog = tmp_path / "ed.py"
    prog.write_text(script, encoding="utf-8")
    monkeypatch.setenv("EDITOR", '"{}" "{}"'.format(sys.executable, prog))
    monkeypatch.delenv("VISUAL", raising=False)


APPEND = "\n".join([
    "import sys, pathlib",
    "p = pathlib.Path(sys.argv[1])",
    "p.write_text(p.read_text(encoding='utf-8') + 'EDITED', encoding='utf-8')",
])


def test_edit_text_round_trips_through_the_editor(monkeypatch, tmp_path):
    _fake_editor(monkeypatch, tmp_path, APPEND)
    out = cli._edit_text("original")
    assert out == "originalEDITED"


def test_edit_text_returns_none_when_nothing_changed(monkeypatch, tmp_path):
    _fake_editor(monkeypatch, tmp_path, "import sys\n")   # touches nothing
    assert cli._edit_text("original") is None


def test_edit_text_returns_none_when_the_editor_fails(monkeypatch, tmp_path):
    _fake_editor(monkeypatch, tmp_path, "import sys\nsys.exit(3)\n")
    assert cli._edit_text("original") is None


def test_edit_text_leaves_no_temp_file_behind(monkeypatch, tmp_path):
    marker = tmp_path / "edited-path.txt"
    script = "\n".join([
        "import sys, pathlib",
        "p = pathlib.Path(sys.argv[1])",
        "p.write_text('new text', encoding='utf-8')",
        "pathlib.Path(r'{}').write_text(sys.argv[1], encoding='utf-8')".format(marker),
    ])
    _fake_editor(monkeypatch, tmp_path, script)
    assert cli._edit_text("original") == "new text"
    edited_path = marker.read_text(encoding="utf-8")
    assert not os.path.exists(edited_path), "temp file was not cleaned up"


def test_edit_text_prefers_visual_over_editor(monkeypatch, tmp_path):
    _fake_editor(monkeypatch, tmp_path, APPEND)
    calls = []
    monkeypatch.setenv("VISUAL", "my-visual-editor")
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 1)
    cli._edit_text("x")
    assert calls and calls[0].startswith("my-visual-editor ")


def test_edit_text_falls_back_to_notepad_on_windows_without_editor(monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 0)
    cli._edit_text("x")
    # shell=True, so the command is one string, not an argv list.
    assert calls and "notepad" in calls[0].lower()
