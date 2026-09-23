"""Plugin API extension tests for validate_entity_state."""

from __future__ import annotations

from tabletop.api.capabilities import Capability
from tabletop.api.plugin import (
    TABLETOP_PLUGIN_API_VERSION,
    GameSystemInfo,
    GameSystemPlugin,
    ValidationIssue,
    ValidationResult,
)
from tabletop.plugins.discovery import discover_plugins, load_plugin
from tabletop.cli.util import repo_root


class _Base(GameSystemPlugin):
    @property
    def info(self) -> GameSystemInfo:
        return GameSystemInfo(
            id="base",
            name="Base",
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DICE})

    def resolve(self, action, context):  # pragma: no cover
        raise NotImplementedError


def test_validate_entity_state_defaults_to_accept() -> None:
    result = _Base().validate_entity_state("character", {"hp": -1})
    assert result == ValidationResult.ok()


def test_validate_entity_state_override_rejects() -> None:
    class Strict(_Base):
        def validate_entity_state(self, entity_type, state):
            if entity_type == "companion" and "name" not in state:
                return ValidationResult(
                    valid=False,
                    issues=(
                        ValidationIssue(
                            path="name",
                            code="missing",
                            message="name is required",
                        ),
                    ),
                )
            return ValidationResult.ok()

    plugin = Strict()
    assert plugin.validate_entity_state("companion", {}).valid is False
    assert plugin.validate_entity_state("companion", {"name": "dog"}).valid is True
    assert plugin.validate_entity_state("character", {}).valid is True


def test_builtin_plugins_still_load_with_entity_hook() -> None:
    systems = repo_root() / "systems"
    for candidate in discover_plugins((systems,)):
        plugin = load_plugin(candidate)
        assert plugin.validate_entity_state("character", {}).valid is True
        plugin.shutdown()
