import pytest

from app.domain.replies import LABELS, cancels_sequence


def test_the_six_spec_labels_exist():
    assert set(LABELS) == {"INTERESTED", "REJECTED", "REFERRAL",
                           "AUTO_REPLY", "OUT_OF_OFFICE", "UNCLEAR"}


@pytest.mark.parametrize("label", ["INTERESTED", "REJECTED", "REFERRAL", "UNCLEAR"])
def test_human_labels_cancel_the_sequence(label):
    assert cancels_sequence(label) is True


@pytest.mark.parametrize("label", ["AUTO_REPLY", "OUT_OF_OFFICE"])
def test_machine_labels_never_cancel_the_sequence(label):
    # An autoresponder must not be able to end an outreach (spec §5).
    assert cancels_sequence(label) is False


# --- Added beyond the brief -------------------------------------------------


def test_an_unrecognised_label_never_cancels_the_sequence():
    # Domain-level safety net: whatever normalises an unknown model label
    # to UNCLEAR lives in the application layer, but cancels_sequence
    # itself must never treat an arbitrary string as license to end an
    # outreach - only the four named human labels do.
    assert cancels_sequence("MAYBE") is False
    assert cancels_sequence("") is False
    assert cancels_sequence("interested") is False  # case matters - not normalised here


def test_labels_is_a_tuple_of_exactly_six():
    assert len(LABELS) == 6
    assert isinstance(LABELS, tuple)
