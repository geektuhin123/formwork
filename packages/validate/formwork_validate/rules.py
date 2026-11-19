"""Rule evaluation with simpleeval. Never Python eval/exec: a rule is customer-supplied text."""

from __future__ import annotations

import ast
from typing import Any

from simpleeval import SimpleEval

MAX_RULE_LENGTH = 500


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= float(tol)


def total(rows: list[dict[str, Any]], name: str) -> float:
    """Sum a child field across array rows: total(line_items, 'amount')."""
    return float(sum(float(r[name]) for r in rows if isinstance(r, dict) and r.get(name) is not None))


FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "len": len,
    "approx": approx,
    "total": total,
    "float": float,
    "int": int,
    "str": str,
}


class RuleNotEvaluable(Exception):
    """An operand is missing or the rule is malformed: the rule says nothing about this record."""


def evaluate(rule: str, names: dict[str, Any]) -> bool:
    if len(rule) > MAX_RULE_LENGTH:
        raise RuleNotEvaluable("rule is too long")
    ev = SimpleEval(functions=FUNCTIONS, names=names)
    ev.nodes.pop(ast.Attribute, None)  # no attribute access: rules see plain values only
    try:
        tree = ast.parse(rule.strip(), mode="eval")
    except SyntaxError as exc:
        raise RuleNotEvaluable(f"syntax error: {exc.msg}") from exc
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - set(FUNCTIONS)
    missing = [n for n in used if names.get(n) is None]
    if missing:
        raise RuleNotEvaluable(f"missing {', '.join(sorted(missing))}")
    try:
        return bool(ev.eval(rule.strip()))
    except (
        Exception
    ) as exc:  # simpleeval raises its own family plus TypeError/ZeroDivisionError from the operands
        raise RuleNotEvaluable(f"{type(exc).__name__}: {exc}") from exc
