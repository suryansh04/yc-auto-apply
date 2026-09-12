"""Review-queue ordering (spec §5, §8 stage 5, D19, D24). Task 16 fix round 1.

`rank_key` decides only the order drafts are shown to the reviewer -
best-grounded first, while attention is freshest. It is a computed sort
key, never stored, and it is pure so the D24 property is structural: it
takes no job-posting or `isHiring` input at all, so attaching postings to
a company cannot move it in the queue (spec §15's regression test).

Order: companies with a news item, then those with a launch post, then the
rest; ties by `team_size` ascending (unknown size last), then founder name.
"""

_UNKNOWN_TEAM_SIZE = float("inf")  # spec is silent; unknown sorts after every known size


def rank_key(has_news: bool, has_launch: bool, team_size: int | None,
             founder_name: str | None) -> tuple[int, float, str]:
    if has_news:
        strength = 0
    elif has_launch:
        strength = 1
    else:
        strength = 2
    return (strength, team_size if team_size is not None else _UNKNOWN_TEAM_SIZE, founder_name or "")
