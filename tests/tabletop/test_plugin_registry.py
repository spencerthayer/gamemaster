"""Phase 7 registry tests: ownership, duplicate protection, lifecycle."""

import pytest

from tabletop.api.capabilities import Capability
from tabletop.api.errors import DuplicatePluginError, PluginNotFoundError
from tabletop.api.plugin import GameSystemInfo, GameSystemPlugin, TABLETOP_PLUGIN_API_VERSION
from tabletop.plugins.registry import PluginRegistry


class StubPlugin(GameSystemPlugin):
    """Minimal in-memory plugin for registry behavior tests."""

    shutdown_error: Exception | None = None

    def __init__(self, system_id: str, caps: frozenset[Capability] = frozenset()):
        self._info = GameSystemInfo(
            id=system_id,
            name=system_id.title(),
            api_version=TABLETOP_PLUGIN_API_VERSION,
        )
        self._caps = caps

    @property
    def info(self) -> GameSystemInfo:
        return self._info

    def capabilities(self) -> frozenset[Capability]:
        return self._caps

    def resolve(self, action, context):  # pragma: no cover - Phase 8 model
        raise NotImplementedError

    def shutdown(self) -> None:
        if self.shutdown_error is not None:
            raise self.shutdown_error


def test_register_get_contains_list_roundtrip():
    registry = PluginRegistry()
    plugin = StubPlugin("alpha")
    registry.register(plugin)
    assert registry.contains("alpha")
    assert registry.get("alpha") is plugin
    assert registry.list() == (plugin,)


def test_list_is_sorted_by_id_not_insertion_order():
    registry = PluginRegistry()
    zeta = StubPlugin("zeta")
    alpha = StubPlugin("alpha")
    registry.register(zeta)
    registry.register(alpha)
    assert [plugin.info.id for plugin in registry.list()] == ["alpha", "zeta"]


def test_duplicate_ids_fail_closed():
    registry = PluginRegistry()
    registry.register(StubPlugin("alpha"))
    with pytest.raises(DuplicatePluginError, match="already registered"):
        registry.register(StubPlugin("alpha"))
    assert registry.contains("alpha")


def test_missing_id_raises_predictably():
    registry = PluginRegistry()
    with pytest.raises(PluginNotFoundError):
        registry.get("missing")
    with pytest.raises(PluginNotFoundError):
        registry.unregister("missing")


def test_unregister_removes_plugin():
    registry = PluginRegistry()
    registry.register(StubPlugin("alpha"))
    registry.unregister("alpha")
    assert not registry.contains("alpha")


def test_capabilities_come_from_plugin_object_not_metadata():
    registry = PluginRegistry()
    registry.register(StubPlugin("dicey", frozenset({Capability.DICE})))
    assert registry.capabilities("dicey") == frozenset({Capability.DICE})


def test_shutdown_all_reports_failures_without_blocking_siblings():
    registry = PluginRegistry()
    registry.register(StubPlugin("alpha"))

    class Boom(Exception):
        pass

    zeta = StubPlugin("zeta")
    zeta.shutdown_error = Boom("shutdown exploded")
    registry.register(zeta)

    failures = registry.shutdown_all()

    assert [system_id for system_id, _ in failures] == ["zeta"]
    assert isinstance(failures[0][1], Boom)
