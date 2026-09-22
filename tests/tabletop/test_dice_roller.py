import random

from tabletop.api.resolution import RollResult
from tabletop.dice.roller import roll


def test_roll_returns_a_roll_result_carrying_the_expression():
    result = roll("d20", rng=random.Random(12345))
    assert isinstance(result, RollResult)
    assert result.expression == "d20"
    assert 1 <= result.total <= 20


def test_same_seed_produces_the_same_roll():
    first = roll("4d6dl1+2", rng=random.Random(9))
    second = roll("4d6dl1+2", rng=random.Random(9))
    assert first.to_dict() == second.to_dict()


def test_details_show_every_die_and_what_was_dropped():
    result = roll("4d6dl1", rng=random.Random(9))
    rolled = result.details["rolls"]
    kept = result.details["kept"]
    dropped = result.details["dropped"]
    assert len(rolled) == 4
    assert len(kept) == 3
    assert len(dropped) == 1
    assert sorted(kept + dropped) == sorted(rolled)
    assert result.total == sum(kept)


def test_advantage_keeps_the_higher_of_two():
    result = roll("2d20kh1", rng=random.Random(4))
    assert result.total == max(result.details["rolls"])
