import pytest
from app.domain.interns import is_intern_appropriate

@pytest.mark.parametrize("etype,title,exp,expected", [
    # Real shapes observed in the YC payload (spec §3).
    ("Internship", "Vision Engineer Intern (AI & Computer Vision)", "", True),
    ("Internship", "Marketing Intern", None, True),
    ("Full-time", "UI UX Intern", "Any (new grads ok)", True),
    ("Full-time", "Founder's Office - Intern", "Any (new grads ok)", True),
    ("Full-time", "Backend Engineer", "Any (new grads ok)", True),
    ("Full-time", "Senior Engineer", "6+ years", False),
    ("Full-time", "Engineering Manager", "3+ years", False),
    ("Full-time", "Lead Product Designer", "6+ years", False),
    ("Full-time", "Site Reliability Engineer", "1+ years", False),
])
def test_intern_appropriateness(etype, title, exp, expected):
    assert is_intern_appropriate(etype, title, exp) is expected

def test_case_insensitive_on_title():
    assert is_intern_appropriate("Full-time", "GRAPHIC DESIGN INTERN", "3+ years") is True

def test_all_none_is_not_appropriate():
    assert is_intern_appropriate(None, None, None) is False
