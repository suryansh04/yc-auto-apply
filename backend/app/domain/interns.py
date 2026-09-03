"""D23: a job posting may only be cited when it is intern-appropriate."""

_OPEN_EXPERIENCE = {"", "any (new grads ok)", "none"}


def is_intern_appropriate(
    employment_type: str | None,
    title: str | None,
    min_experience: str | None,
) -> bool:
    if (employment_type or "").strip().lower() == "internship":
        return True
    if "intern" in (title or "").lower():
        return True
    return (min_experience or "").strip().lower() in _OPEN_EXPERIENCE and bool(title)
