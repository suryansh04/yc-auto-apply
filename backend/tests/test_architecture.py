"""Fix round 2 (ruling T6-5): the project's most-repeated global
constraint - `app/domain/` and `app/application/` never import
`app/adapters/` or `app/api/` - has been convention-only since Phase 1,
re-verified by grep in every review so far rather than by a test.
Fourteen tasks remain; pin it once here instead of re-deriving it by hand
every time.

Parsed with `ast`, not regex: a docstring or a `#` comment that merely
MENTIONS `app.adapters` (there are several in this codebase, e.g.
`app/api/deps.py`'s own docstrings) must never fail this - only actual
`import` / `from ... import ...` statements count. `ast.parse` sees
comments as nothing at all and a docstring as a single `Constant` string
node, never as `Import`/`ImportFrom` nodes, so this is safe by
construction, not by carefully avoiding trigger words.

Also pins T6-3 (fix round 1): `app/api/` must never import `app.cli` -
`cli.py` is the operator adapter, not a library for the API layer; the
policy/adapter-construction helpers a job stage might otherwise have
duplicated from it (`on_gemini_free_tier`, `build_drafter`) belong in
`app/api/deps.py` instead, imported by BOTH `app.cli` and `app/api/`."""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _python_files(package: str) -> list[Path]:
    root = APP_ROOT / package
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> list[str]:
    """Every dotted module name this file imports - `import a.b.c`
    contributes `"a.b.c"`; `from a.b.c import d` contributes BOTH
    `"a.b.c"` and `"a.b.c.d"` (fix round 3: `from app import cli` must
    be caught too - `node.module` alone is only `"app"`, which is not
    itself forbidden; it's the imported NAME, `cli`, that makes the full
    target `app.cli`. `tests/test_cli_editor.py:12` already writes
    exactly this spelling). A relative import (`from . import x` /
    `from .y import z`) is resolved against the file's own package so it
    cannot silently dodge the check just by using a different import
    spelling."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # The file's own package, e.g. backend/app/api/routers/jobs.py ->
    # "app.api.routers" (an __init__.py's own package is itself, one
    # level up from a plain module's).
    rel = path.relative_to(APP_ROOT.parent)
    parts = list(rel.with_suffix("").parts)  # e.g. ["app", "api", "routers", "jobs"]
    # Both a plain module (`jobs.py`) and a package's `__init__.py` drop
    # just the filename here: for `__init__.py` the remaining parts ARE
    # the package's own dotted name already (its directory IS the
    # package), and for a plain module the package is its parent
    # directory - the same slice either way.
    own_package = ".".join(parts[:-1])

    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Resolve "from . import x" / "from .y import z" style
                # relative imports against this file's own package.
                base_parts = own_package.split(".") if own_package else []
                # level=1 means "this package"; each extra level strips
                # one more trailing component (mirrors Python's own
                # relative-import resolution).
                strip = node.level - 1
                base_parts = base_parts[: len(base_parts) - strip] if strip else base_parts
                base = ".".join(base_parts)
                full = f"{base}.{node.module}" if node.module else base
            else:
                full = node.module or ""
            if full:
                modules.append(full)
                for alias in node.names:
                    # The imported NAME can itself be the forbidden
                    # module, e.g. `from app import cli` (full="app",
                    # alias.name="cli") - the offending target is
                    # "app.cli", never just "app".
                    modules.append(f"{full}.{alias.name}")
    return modules


def _forbidden_imports(files: list[Path], forbidden_prefixes: tuple[str, ...]) -> list[str]:
    """One line per violation: `"<file>: import <module>"` - a future
    failure names both the offending file and the exact import, so
    nobody has to re-derive it from a bare assertion."""
    violations = []
    for path in files:
        for module in _imported_modules(path):
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes):
                violations.append(f"{path.relative_to(APP_ROOT.parent)}: import {module}")
    return violations


def test_domain_never_imports_adapters_api_or_application():
    files = _python_files("domain")
    # Fix round 3: without this, a renamed/deleted app/domain/ package
    # would make `_python_files` return [] and this test would pass
    # vacuously - the guard silently disarmed rather than failing loudly.
    assert files, "no .py files found under app/domain/ - has the package moved or been renamed?"
    violations = _forbidden_imports(files, ("app.adapters", "app.api", "app.application"))
    assert violations == [], (
        "app/domain/ must never import app.adapters, app.api or app.application "
        f"(D8 / the project's global constraint) - violations:\n" + "\n".join(violations)
    )


def test_application_never_imports_adapters_or_api():
    files = _python_files("application")
    assert files, "no .py files found under app/application/ - has the package moved or been renamed?"
    violations = _forbidden_imports(files, ("app.adapters", "app.api"))
    assert violations == [], (
        "app/application/ must never import app.adapters or app.api "
        f"(D8 / the project's global constraint) - violations:\n" + "\n".join(violations)
    )


def test_api_never_imports_cli():
    files = _python_files("api")
    assert files, "no .py files found under app/api/ - has the package moved or been renamed?"
    violations = _forbidden_imports(files, ("app.cli",))
    assert violations == [], (
        "app/api/ must never import app.cli (ruling T6-3: cli.py is the operator "
        "adapter, not a library for the API layer - share policy/adapter-building "
        "helpers via app/api/deps.py instead) - violations:\n" + "\n".join(violations)
    )
