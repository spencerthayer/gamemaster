"""The default suite must not build or boot the image."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.docker


def test_docker_marker_is_opt_in() -> None:
    """Reached only when GAMEMASTER_RUN_DOCKER=1. Otherwise conftest skips it."""

    assert True
