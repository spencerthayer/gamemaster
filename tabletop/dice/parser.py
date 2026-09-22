"""Dice expression parser.

Parses d20, 2d6, 3d6+2, 2d20kh1, 2d20kl1, 4d6dl1, 1d100 into an AST. No
D&D or any-system semantics at this layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tabletop.api.errors import DiceExpressionError

_EXPRESSION = re.compile(
    r"\A(?P<count>\d*)d(?P<sides>\d+)"
    r"(?:(?P<keep_kind>kh|kl|dh|dl)(?P<keep_count>\d+))?"
    r"(?:(?P<sign>[+-])(?P<modifier>\d+))?\Z"
)


@dataclass(frozen=True)
class KeepRule:
    kind: str
    count: int


@dataclass(frozen=True)
class DiceTerm:
    count: int
    sides: int
    keep: KeepRule | None = None


@dataclass(frozen=True)
class ParsedExpression:
    terms: tuple[DiceTerm, ...]
    modifier: int = 0


def parse(expression: str) -> ParsedExpression:
    if not isinstance(expression, str) or not expression:
        raise DiceExpressionError()
    if expression != expression.strip():
        raise DiceExpressionError()

    match = _EXPRESSION.match(expression)
    if match is None:
        raise DiceExpressionError()

    count_str = match.group("count")
    count = int(count_str) if count_str else 1
    sides = int(match.group("sides"))

    if count < 1:
        raise DiceExpressionError()
    if sides < 2:
        raise DiceExpressionError()

    keep: KeepRule | None = None
    keep_kind = match.group("keep_kind")
    if keep_kind is not None:
        keep_count = int(match.group("keep_count"))
        if keep_count < 1 or keep_count >= count:
            raise DiceExpressionError()
        keep = KeepRule(kind=keep_kind, count=keep_count)

    modifier = 0
    sign = match.group("sign")
    if sign is not None:
        mod_value = int(match.group("modifier"))
        modifier = mod_value if sign == "+" else -mod_value

    term = DiceTerm(count=count, sides=sides, keep=keep)
    return ParsedExpression(terms=(term,), modifier=modifier)
