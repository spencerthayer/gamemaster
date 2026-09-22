import pytest

from tabletop.api.errors import DiceExpressionError
from tabletop.dice.parser import DiceTerm, KeepRule, ParsedExpression, parse


def test_plain_die_defaults_to_one():
    assert parse("d20") == ParsedExpression(terms=(DiceTerm(count=1, sides=20),), modifier=0)


def test_count_sides_and_modifier():
    assert parse("3d6+2") == ParsedExpression(terms=(DiceTerm(count=3, sides=6),), modifier=2)


@pytest.mark.parametrize(
    "expression,kind,keep",
    [
        ("2d20kh1", "kh", 1),
        ("2d20kl1", "kl", 1),
        ("4d6dl1", "dl", 1),
    ],
)
def test_keep_and_drop_rules(expression, kind, keep):
    parsed = parse(expression)
    assert parsed.terms[0].keep == KeepRule(kind=kind, count=keep)


@pytest.mark.parametrize(
    "expression",
    ["", " d20", "d20 ", "0d6", "1d0", "d", "2d6kh3", "2d6kh0", "1d100x", "-1d6"],
)
def test_malformed_expressions_are_rejected(expression):
    with pytest.raises(DiceExpressionError):
        parse(expression)
