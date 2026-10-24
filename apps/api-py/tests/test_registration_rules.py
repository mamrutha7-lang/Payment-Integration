"""The registration rule engine — which populated conditions must match for a
rule to fire. `_rule_matches` is pure (takes a constructed RegistrationRule and
the applicant fields), so it needs no DB.
"""

from app.models.job import DegreeLevel
from app.models.registration import RegistrationRule
from app.routers.registration import _email_domain, _rule_matches


def _rule(**kw) -> RegistrationRule:
    # Construct a transient model instance (never added to a session).
    return RegistrationRule(**kw)


def test_email_domain_extraction():
    assert _email_domain("1BG24MBA001@BGSCET.AC.IN") == "bgscet.ac.in"
    assert _email_domain("x@sub.example.com") == "sub.example.com"


def test_empty_rule_is_wildcard():
    rule = _rule(email_domain=None, usn_pattern=None, degree_level=None)
    assert _rule_matches(rule, "anyone@anywhere.com", None, DegreeLevel.PG)


def test_email_domain_condition():
    rule = _rule(email_domain="bgscet.ac.in", usn_pattern=None, degree_level=None)
    assert _rule_matches(rule, "a@bgscet.ac.in", None, DegreeLevel.PG)
    assert not _rule_matches(rule, "a@gmail.com", None, DegreeLevel.PG)


def test_usn_pattern_condition():
    rule = _rule(email_domain=None, usn_pattern=r"^1BG2[0-9]MBA[0-9]{3}$", degree_level=None)
    assert _rule_matches(rule, "a@x.com", "1BG24MBA045", DegreeLevel.PG)
    assert not _rule_matches(rule, "a@x.com", "9XX99ZZZ999", DegreeLevel.PG)
    # A required USN pattern with no USN supplied cannot match.
    assert not _rule_matches(rule, "a@x.com", None, DegreeLevel.PG)


def test_usn_pattern_ignores_case():
    """The form upper-cases every USN (2026-10-16), so a rule an author wrote
    in lower case must still match; without IGNORECASE it matched nobody and
    sent every applicant it was written for to manual review."""
    lower = _rule(email_domain=None, usn_pattern=r"^1mp25ba[0-9]{3}$", degree_level=None)
    assert _rule_matches(lower, "a@x.com", "1MP25BA033", DegreeLevel.PG)
    upper = _rule(email_domain=None, usn_pattern=r"^1MP25BA[0-9]{3}$", degree_level=None)
    assert _rule_matches(upper, "a@x.com", "1mp25ba033", DegreeLevel.PG)


def test_degree_level_condition():
    rule = _rule(email_domain=None, usn_pattern=None, degree_level=DegreeLevel.PG)
    assert _rule_matches(rule, "a@x.com", None, DegreeLevel.PG)
    assert not _rule_matches(rule, "a@x.com", None, DegreeLevel.UG)


def test_all_conditions_must_hold_together():
    rule = _rule(
        email_domain="bgscet.ac.in",
        usn_pattern=r"^1BG2[0-9]MBA[0-9]{3}$",
        degree_level=DegreeLevel.PG,
    )
    # Every condition satisfied.
    assert _rule_matches(rule, "z@bgscet.ac.in", "1BG24MBA001", DegreeLevel.PG)
    # One condition off (domain) → no match.
    assert not _rule_matches(rule, "z@gmail.com", "1BG24MBA001", DegreeLevel.PG)
