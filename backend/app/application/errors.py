"""An error whose message is safe to put in front of the operator.

Job rows record `error=type(exc).__name__` and nothing else (spec §9,
`RunJob.fail`), because an exception message can carry an API key - a
provider's 401 body, a connection string, a traceback's locals. That rule
is right for everything the app did not write itself, and it is why a
failed stage shows up in the UI as `failed: ValueError`.

Live finding (2026-09-23), from the operator asking what a brand-new user
would experience: pressing **Draft** before the resume has been parsed
fails with a sentence that says exactly what to do - "no resume profile
with claims - run `parse-resume PATH` first" - and the screen shows
`failed: ValueError` instead. The advice exists only in the API's
terminal, which someone using the browser UI has no reason to be
watching. There is no way forward from the screen they are on.

`OperatorError` is the exception a stage raises when it has decided, on
purpose, that its message is written FOR the operator: a missing setting
named but never valued, a step that has to be run first, a refusal the
app makes by policy. Nothing constructs one from a provider's response,
a traceback, or any string this codebase did not author - and that is the
whole safety argument. It is not "errors we think are probably fine to
show"; it is errors written to be shown.

It subclasses `ValueError` so every existing `except ValueError` around
`load_resume_claims` and friends keeps working unchanged.
"""

from __future__ import annotations


class OperatorError(ValueError):
    """A failure whose message was written to be read by the operator."""
