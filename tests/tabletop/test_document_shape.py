"""Document shape detection is deterministic and inspectable."""

from dataclasses import asdict
from unittest.mock import patch

import pytest

from tabletop.documents import shape as shape_module
from tabletop.documents.shape import DocumentShape, detect_shape


@pytest.mark.parametrize(
    ("text", "expected_shape"),
    [
        (
            """# Movement
Speed:
30 feet

## Difficult Ground
Cost:
Two units of movement

## Climbing
Check:
Required when the surface is slippery
""",
            DocumentShape.STRUCTURED_RULES,
        ),
        (
            """Mara crossed the quiet courtyard after sunset, watching the empty
windows while the rain gathered in the cracks between the stones. The letter in
her pocket had named this place, but it had not explained why the gate stood open.

"Wait here," Iven said, lowering his lantern. "Someone is inside." Mara replied
that they had already come too far to turn back, and together they entered the hall.
""",
            DocumentShape.PROSE,
        ),
        (
            """Name | Category | Cost
Torch | Gear | 1
Rope | Gear | 2
Lantern | Gear | 5
""",
            DocumentShape.REFERENCE_TABLE,
        ),
        ("alpha beta\ngamma delta\n", DocumentShape.UNSUPPORTED),
    ],
)
def test_detects_document_shape_from_countable_signals(text, expected_shape):
    result = detect_shape(text)

    assert result.shape is expected_shape
    assert result.score >= 0
    assert asdict(result.signals)


def test_detection_is_pure_and_deterministic():
    text = "Name\tKind\tValue\nMap\tTool\t3\nKey\tTool\t1\n"

    with (
        patch("builtins.open", side_effect=AssertionError("unexpected file I/O")),
        patch("socket.socket", side_effect=AssertionError("unexpected network I/O")),
    ):
        first = detect_shape(text)
        second = detect_shape(text)

    assert first == second
    assert first.shape is DocumentShape.REFERENCE_TABLE


def test_unsupported_requires_manual_review_without_a_parser_guess():
    result = detect_shape("alpha beta\ngamma delta\n")

    assert result.shape is DocumentShape.UNSUPPORTED
    assert result.requires_manual_review is True
    assert result.parser is None


def test_score_must_be_above_threshold(monkeypatch):
    text = "# Heading\nLabel:\nvalue\n"
    score = detect_shape(text).score
    monkeypatch.setattr(shape_module, "SHAPE_SCORE_THRESHOLD", score)

    result = detect_shape(text)

    assert result.shape is DocumentShape.UNSUPPORTED
