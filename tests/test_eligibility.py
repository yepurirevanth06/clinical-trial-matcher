"""Eligibility rule engine.

Pure functions over hand-built trees -- no database, no fixtures. The point of
testing this in isolation is that the three-valued logic is the part most
likely to be wrong in a way that looks fine.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.criteria import CriteriaNode, NodeType, Operator
from app.models.patient import Patient, Sex
from app.services.eligibility import Verdict, evaluate

AS_OF = date(2026, 6, 15)


def _patient(**kw) -> Patient:
    """Unsaved Patient. Never added to a session, so no DB is involved."""
    defaults = dict(
        external_id="P1",
        birth_date=date(1990, 1, 1),
        sex=Sex.FEMALE,
        conditions=[],
        medications=[],
        lab_values={},
    )
    return Patient(**{**defaults, **kw})


def _leaf(field, op, value, *, exclusion=False, raw=None) -> CriteriaNode:
    return CriteriaNode(
        node_type=NodeType.LEAF,
        field=field,
        operator=op,
        value=value,
        is_exclusion=exclusion,
        raw_text=raw,
        ordering=0,
    )


def _branch(kind, children, *, exclusion=False) -> CriteriaNode:
    node = CriteriaNode(node_type=kind, is_exclusion=exclusion, ordering=0)
    node.children = children
    return node


# --- leaves -----------------------------------------------------------------


def test_age_is_computed_not_stored():
    p = _patient(birth_date=date(2000, 1, 1))
    assert evaluate(_leaf("age", Operator.GTE, "18"), p, AS_OF).verdict is Verdict.PASS
    assert evaluate(_leaf("age", Operator.GTE, "30"), p, AS_OF).verdict is Verdict.FAIL


def test_age_before_birthday_this_year():
    """The off-by-one lives here: on 2026-06-15 someone born 2008-12-01 is 17,
    not 18, because their birthday has not happened yet."""
    p = _patient(birth_date=date(2008, 12, 1))
    assert evaluate(_leaf("age", Operator.GTE, "18"), p, AS_OF).verdict is Verdict.FAIL

    q = _patient(birth_date=date(2008, 3, 1))  # birthday already passed
    assert evaluate(_leaf("age", Operator.GTE, "18"), q, AS_OF).verdict is Verdict.PASS


def test_sex_equality_is_case_insensitive():
    p = _patient(sex=Sex.FEMALE)
    assert evaluate(_leaf("sex", Operator.EQ, "FEMALE"), p, AS_OF).verdict is Verdict.PASS
    assert evaluate(_leaf("sex", Operator.NEQ, "female"), p, AS_OF).verdict is Verdict.FAIL


def test_in_operator_splits_on_commas():
    p = _patient(sex=Sex.OTHER)
    assert evaluate(_leaf("sex", Operator.IN, "male, other"), p, AS_OF).verdict is Verdict.PASS
    assert evaluate(_leaf("sex", Operator.IN, "male,female"), p, AS_OF).verdict is Verdict.FAIL


def test_contains_matches_icd_prefix():
    """ICD-10 is hierarchical, so a criterion naming E11 should match E11.9."""
    p = _patient(conditions=["E11.9", "I10"])
    assert evaluate(_leaf("conditions", Operator.CONTAINS, "E11"), p, AS_OF).verdict is Verdict.PASS
    assert evaluate(_leaf("conditions", Operator.CONTAINS, "C50"), p, AS_OF).verdict is Verdict.FAIL


def test_lab_values_resolve_by_dotted_path():
    p = _patient(lab_values={"egfr": 42.0})
    assert evaluate(_leaf("lab_values.egfr", Operator.LT, "60"), p, AS_OF).verdict is Verdict.PASS


# --- the three-valued part --------------------------------------------------


def test_missing_lab_is_unknown_not_fail():
    """The whole reason for three-valued logic. An unmeasured eGFR means the
    patient needs a lab draw, not that they are ineligible."""
    p = _patient(lab_values={})
    result = evaluate(_leaf("lab_values.egfr", Operator.GTE, "60"), p, AS_OF)
    assert result.verdict is Verdict.UNKNOWN


def test_unrecognised_field_is_unknown_and_says_so():
    p = _patient()
    result = evaluate(_leaf("ecog_status", Operator.LTE, "1"), p, AS_OF)
    assert result.verdict is Verdict.UNKNOWN
    assert "ecog_status" in result.reason


def test_non_numeric_bound_is_unknown_not_fail():
    """A criterion whose value will not parse is a data problem, so it surfaces
    for review rather than silently excluding the patient."""
    p = _patient(lab_values={"egfr": 42.0})
    result = evaluate(_leaf("lab_values.egfr", Operator.GTE, "sixty"), p, AS_OF)
    assert result.verdict is Verdict.UNKNOWN


@pytest.mark.parametrize(
    ("kids", "expected"),
    [
        ([Verdict.PASS, Verdict.PASS], Verdict.PASS),
        ([Verdict.PASS, Verdict.FAIL], Verdict.FAIL),
        ([Verdict.PASS, Verdict.UNKNOWN], Verdict.UNKNOWN),
        # A FAIL sinks the conjunction even alongside an UNKNOWN.
        ([Verdict.UNKNOWN, Verdict.FAIL], Verdict.FAIL),
    ],
)
def test_and_truth_table(kids, expected):
    p = _patient(birth_date=date(1990, 1, 1), lab_values={"a": 1.0})
    leaves = []
    for v in kids:
        if v is Verdict.PASS:
            leaves.append(_leaf("age", Operator.GTE, "18"))
        elif v is Verdict.FAIL:
            leaves.append(_leaf("age", Operator.GTE, "99"))
        else:
            leaves.append(_leaf("lab_values.missing", Operator.GTE, "1"))
    assert evaluate(_branch(NodeType.AND, leaves), p, AS_OF).verdict is expected


@pytest.mark.parametrize(
    ("kids", "expected"),
    [
        ([Verdict.FAIL, Verdict.FAIL], Verdict.FAIL),
        ([Verdict.FAIL, Verdict.PASS], Verdict.PASS),
        ([Verdict.FAIL, Verdict.UNKNOWN], Verdict.UNKNOWN),
        # One PASS satisfies the disjunction regardless of unknowns.
        ([Verdict.UNKNOWN, Verdict.PASS], Verdict.PASS),
    ],
)
def test_or_truth_table(kids, expected):
    p = _patient(birth_date=date(1990, 1, 1))
    leaves = []
    for v in kids:
        if v is Verdict.PASS:
            leaves.append(_leaf("age", Operator.GTE, "18"))
        elif v is Verdict.FAIL:
            leaves.append(_leaf("age", Operator.GTE, "99"))
        else:
            leaves.append(_leaf("lab_values.missing", Operator.GTE, "1"))
    assert evaluate(_branch(NodeType.OR, leaves), p, AS_OF).verdict is expected


def test_not_leaves_unknown_alone():
    """NOT(unknown) is still unknown -- negating a thing you do not know does
    not tell you anything."""
    p = _patient(lab_values={})
    inner = _leaf("lab_values.egfr", Operator.GTE, "60")
    assert evaluate(_branch(NodeType.NOT, [inner]), p, AS_OF).verdict is Verdict.UNKNOWN


# --- exclusions -------------------------------------------------------------


def test_exclusion_inverts_after_the_subtree_resolves():
    """Matching an exclusion criterion means ineligible, so a PASS becomes a
    FAIL."""
    p = _patient(conditions=["I10"])
    node = _leaf("conditions", Operator.CONTAINS, "I10", exclusion=True)
    result = evaluate(node, p, AS_OF)
    assert result.verdict is Verdict.FAIL
    assert "exclusion" in result.reason


def test_exclusion_on_a_subtree_inverts_the_whole_branch():
    p = _patient(conditions=["I10"], birth_date=date(1990, 1, 1))
    branch = _branch(
        NodeType.AND,
        [
            _leaf("conditions", Operator.CONTAINS, "I10"),
            _leaf("age", Operator.GTE, "18"),
        ],
        exclusion=True,
    )
    assert evaluate(branch, p, AS_OF).verdict is Verdict.FAIL


# --- explanation shape ------------------------------------------------------


def test_result_tree_mirrors_the_criteria_tree_and_keeps_raw_text():
    """raw_text is preserved so an explanation can quote what the trial
    actually said rather than a reconstruction of it."""
    p = _patient(birth_date=date(1990, 1, 1))
    leaf = _leaf("age", Operator.GTE, "18", raw="Age 18 years or older")
    result = evaluate(_branch(NodeType.AND, [leaf]), p, AS_OF)

    assert len(result.children) == 1
    assert result.children[0].raw_text == "Age 18 years or older"
    assert result.children[0].verdict is Verdict.PASS
