"""Task 16 fix round 1 (F4/T16-2): the review-queue `rank_key` is pure
domain code (spec §5, §8 stage 5, D19/D24) - it orders the approval queue
and nothing else, by strength of research material, never by postings or
`isHiring`."""

import pytest

from app.domain.ranking import rank_key


def test_news_before_launch_before_the_rest():
    news = rank_key(has_news=True, has_launch=True, team_size=100, founder_name="Zed")
    launch = rank_key(has_news=False, has_launch=True, team_size=1, founder_name="Al")
    rest = rank_key(has_news=False, has_launch=False, team_size=1, founder_name="Al")
    assert news < launch < rest


def test_ties_break_on_team_size_ascending_then_founder_name():
    small = rank_key(True, False, 5, "Zed")
    big = rank_key(True, False, 50, "Al")
    assert small < big
    assert rank_key(True, False, 5, "Al") < rank_key(True, False, 5, "Bea")


def test_unknown_team_size_sorts_after_every_known_size():
    """Spec is silent on a missing `team_size`; the choice is "last", and a
    raw `None` mixed with ints would raise `TypeError` in `sorted`."""
    unknown = rank_key(True, False, None, "Al")
    assert rank_key(True, False, 10_000, "Zed") < unknown
    sorted([unknown, rank_key(True, False, 3, "Al")])   # must not raise


def test_a_missing_founder_name_is_treated_as_empty():
    assert rank_key(True, False, 1, None) < rank_key(True, False, 1, "Al")


@pytest.mark.parametrize("has_news, has_launch", [(True, True), (True, False), (False, True), (False, False)])
def test_the_key_has_no_postings_or_is_hiring_input(has_news, has_launch):
    """D24 at the signature level: there is nothing to pass postings in as."""
    import inspect

    params = set(inspect.signature(rank_key).parameters)
    assert params == {"has_news", "has_launch", "team_size", "founder_name"}
    assert rank_key(has_news, has_launch, 1, "x") == rank_key(has_news, has_launch, 1, "x")
