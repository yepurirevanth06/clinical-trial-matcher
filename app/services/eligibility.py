"""Eligibility rule engine.

Walks a trial's criteria tree against a patient record and returns a verdict
per node, so a caller can show *why* a patient passed or failed rather than a
bare boolean.

Three-valued on purpose. A patient with no recorded eGFR evaluated against
"eGFR >= 60" has not failed -- the value was never measured. Collapsing that to
False would tell a coordinator the patient is ineligible when the truth is
"needs a lab draw". PASS / FAIL / UNKNOWN keeps the distinction the whole way
up the tree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from enum import Enum
from typing import Any

from app.models.criteria import CriteriaNode, NodeType, Operator
from app.models.patient import Patient


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class NodeResult:
    """One node's outcome, mirroring the tree shape so the API can return it."""

    verdict: Verdict
    reason: str
    node_type: NodeType
    raw_text: str | None = None
    is_exclusion: bool = False
    children: list[NodeResult] = dc_field(default_factory=list)


# --- field registry ---------------------------------------------------------
#
# `field` comes out of the database, so resolving it with getattr() would be
# arbitrary attribute access driven by data we do not control. A whitelist also
# gives derived fields (age) and nested ones (lab_values.*) somewhere to live
# without special-casing them in the walker.

_MISSING = object()


def _age(patient: Patient, as_of: date) -> Any:
    if patient.birth_date is None:
        return _MISSING
    years = as_of.year - patient.birth_date.year
    # Subtract a year if the birthday has not happened yet this year.
    if (as_of.month, as_of.day) < (patient.birth_date.month, patient.birth_date.day):
        years -= 1
    return years


FIELD_REGISTRY: dict[str, Callable[[Patient, date], Any]] = {
    "age": _age,
    "sex": lambda p, _: p.sex.value if p.sex is not None else _MISSING,
    "conditions": lambda p, _: p.conditions or [],
    "medications": lambda p, _: p.medications or [],
}


def resolve_field(name: str, patient: Patient, as_of: date) -> Any:
    """Return the patient's value for `name`, or _MISSING.

    lab_values.<key> is handled as a prefix rather than an entry per lab: the
    set of labs is open-ended and driven by whatever Synthea emitted.
    """
    if name in FIELD_REGISTRY:
        return FIELD_REGISTRY[name](patient, as_of)
    if name.startswith("lab_values."):
        key = name.split(".", 1)[1]
        labs = patient.lab_values or {}
        return labs.get(key, _MISSING)
    # Unknown field: the trial referenced something this model does not carry.
    # UNKNOWN, not an error -- criteria text is not under our control.
    return _MISSING


# --- comparison -------------------------------------------------------------


def _as_number(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compare(op: Operator | None, actual: Any, expected: str) -> tuple[Verdict, str]:
    """Apply one operator. Returns the verdict and a human-readable reason."""
    # operator is nullable on the model because branch nodes do not carry one.
    # A leaf without an operator is malformed data, so surface it for review
    # rather than crashing or guessing.
    if op is None:
        return Verdict.UNKNOWN, "leaf node has no operator"

    if actual is _MISSING:
        return Verdict.UNKNOWN, "patient has no value recorded for this field"

    if op in (Operator.EQ, Operator.NEQ):
        hit = str(actual).lower() == expected.lower()
        if op is Operator.NEQ:
            hit = not hit
        return (
            (Verdict.PASS if hit else Verdict.FAIL),
            f"{actual!r} {'==' if op is Operator.EQ else '!='} {expected!r}",
        )

    if op in (Operator.GT, Operator.GTE, Operator.LT, Operator.LTE):
        # `value` is a String column, so the numeric comparison needs a cast.
        # A non-numeric bound is a data problem, not a patient problem: UNKNOWN
        # rather than FAIL, so it shows up as needing review.
        left, right = _as_number(str(actual)), _as_number(expected)
        if left is None or right is None:
            return Verdict.UNKNOWN, f"cannot compare {actual!r} numerically with {expected!r}"
        hit = {
            Operator.GT: left > right,
            Operator.GTE: left >= right,
            Operator.LT: left < right,
            Operator.LTE: left <= right,
        }[op]
        symbol = {Operator.GT: ">", Operator.GTE: ">=", Operator.LT: "<", Operator.LTE: "<="}[op]
        return (Verdict.PASS if hit else Verdict.FAIL), f"{left} {symbol} {right}"

    if op is Operator.IN:
        # Comma-separated because `value` is String(255). Values containing a
        # comma are not supported; that is a documented limitation, not a bug.
        allowed = [v.strip().lower() for v in expected.split(",")]
        hit = str(actual).lower() in allowed
        return (Verdict.PASS if hit else Verdict.FAIL), f"{actual!r} in {allowed}"

    if op is Operator.CONTAINS:
        # For list fields (conditions, medications). Substring match on strings
        # so "E11" matches "E11.9" -- ICD codes are hierarchical.
        haystack = actual if isinstance(actual, list) else [actual]
        hit = any(expected.lower() in str(item).lower() for item in haystack)
        return (Verdict.PASS if hit else Verdict.FAIL), f"{expected!r} in {haystack}"

    return Verdict.UNKNOWN, f"unsupported operator {op}"


# --- tree walk --------------------------------------------------------------


def _combine_and(verdicts: list[Verdict]) -> Verdict:
    # A single FAIL sinks the conjunction regardless of unknowns. Only if
    # nothing failed does an UNKNOWN propagate.
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL
    if Verdict.UNKNOWN in verdicts:
        return Verdict.UNKNOWN
    return Verdict.PASS


def _combine_or(verdicts: list[Verdict]) -> Verdict:
    # Mirror image: one PASS satisfies the disjunction even if others are
    # unknown.
    if Verdict.PASS in verdicts:
        return Verdict.PASS
    if Verdict.UNKNOWN in verdicts:
        return Verdict.UNKNOWN
    return Verdict.FAIL


def _invert(v: Verdict) -> Verdict:
    if v is Verdict.PASS:
        return Verdict.FAIL
    if v is Verdict.FAIL:
        return Verdict.PASS
    return Verdict.UNKNOWN


def _is_known_field(name: str) -> bool:
    return name in FIELD_REGISTRY or name.startswith("lab_values.")


def evaluate(node: CriteriaNode, patient: Patient, as_of: date | None = None) -> NodeResult:
    """Evaluate one node (and its subtree) against a patient.

    as_of is explicit so age is deterministic; defaulting to today would make
    tests fail on a patient's birthday.
    """
    as_of = as_of or date.today()

    if node.node_type is NodeType.LEAF:
        actual = resolve_field(node.field or "", patient, as_of)
        verdict, reason = compare(node.operator, actual, node.value or "")
        if node.field and not _is_known_field(node.field):
            reason = f"unrecognised field {node.field!r}"
        result = NodeResult(
            verdict=verdict,
            reason=reason,
            node_type=node.node_type,
            raw_text=node.raw_text,
            is_exclusion=node.is_exclusion,
        )
    else:
        kids = sorted(node.children, key=lambda c: c.ordering)
        child_results = [evaluate(c, patient, as_of) for c in kids]
        verdicts = [c.verdict for c in child_results]

        if node.node_type is NodeType.AND:
            verdict = _combine_and(verdicts)
        elif node.node_type is NodeType.OR:
            verdict = _combine_or(verdicts)
        elif node.node_type is NodeType.NOT:
            # NOT over several children is ambiguous; treat it as NOT(AND(...)).
            verdict = _invert(_combine_and(verdicts))
        else:
            verdict = Verdict.UNKNOWN

        result = NodeResult(
            verdict=verdict,
            reason=f"{node.node_type.value} over {len(child_results)} child node(s)",
            node_type=node.node_type,
            raw_text=node.raw_text,
            is_exclusion=node.is_exclusion,
            children=child_results,
        )

    # Exclusion applies after the subtree resolves: matching an exclusion
    # criterion means the patient is ineligible, so a PASS becomes a FAIL.
    if node.is_exclusion:
        result.verdict = _invert(result.verdict)
        result.reason = f"exclusion inverted: {result.reason}"

    return result
