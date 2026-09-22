"""Dice roller.

``roll(expression, rng=None) -> RollResult`` with injected, seedable RNG for
deterministic tests.
"""

from __future__ import annotations

import random
from typing import assert_never

from tabletop.api.resolution import RollResult
from tabletop.dice.parser import KeepRule, parse


def roll(expression: str, rng: random.Random | None = None) -> RollResult:
    if rng is None:
        rng = random.Random()

    parsed = parse(expression)
    term = parsed.terms[0]
    rolls = [rng.randint(1, term.sides) for _ in range(term.count)]
    kept, dropped = _apply_keep_rule(rolls, term.keep)
    total = sum(kept) + parsed.modifier

    return RollResult(
        expression=expression,
        total=total,
        details={
            "rolls": rolls,
            "kept": kept,
            "dropped": dropped,
            "modifier": parsed.modifier,
        },
    )


def _apply_keep_rule(
    rolls: list[int], keep: KeepRule | None
) -> tuple[list[int], list[int]]:
    if keep is None:
        return list(rolls), []

    indexed = list(enumerate(rolls))

    match keep.kind:
        case "kh":
            sorted_indexed = sorted(indexed, key=lambda item: (-item[1], item[0]))
            kept_indices = {index for index, _ in sorted_indexed[: keep.count]}
        case "kl":
            sorted_indexed = sorted(indexed, key=lambda item: (item[1], item[0]))
            kept_indices = {index for index, _ in sorted_indexed[: keep.count]}
        case "dh":
            sorted_indexed = sorted(indexed, key=lambda item: (-item[1], item[0]))
            dropped_indices = {index for index, _ in sorted_indexed[: keep.count]}
            kept_indices = set(range(len(rolls))) - dropped_indices
        case "dl":
            sorted_indexed = sorted(indexed, key=lambda item: (item[1], item[0]))
            dropped_indices = {index for index, _ in sorted_indexed[: keep.count]}
            kept_indices = set(range(len(rolls))) - dropped_indices
        case _:
            assert_never(keep.kind)

    kept = [rolls[index] for index in range(len(rolls)) if index in kept_indices]
    dropped = [rolls[index] for index in range(len(rolls)) if index not in kept_indices]
    return kept, dropped
