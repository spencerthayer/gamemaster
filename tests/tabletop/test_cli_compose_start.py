"""Campaign start and stop Compose contract tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tabletop.api.workspace import Workspace
from tabletop.campaign import sender_binding
from tabletop.campaign.membership import MembershipStore
from tabletop.campaign.store import CampaignStore
from tabletop.cli import handlers
from tabletop.cli.parser import build_parser
from tabletop.cli.player_service_spec import (
    PLAYER_CREDENTIAL_SLOTS,
    PLAYER_SERVICE_SPEC,
    player_compose_definition,
)
from tabletop.storage.sqlite import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _campaign(tmp_path: Path, *, bind: bool = True) -> tuple[object, Path]:
    database = tmp_path / "campaign.sqlite3"
    conn = connect(database)
    migrate(conn)
    CampaignStore(conn).create_campaign("night", "Night", "freeform")
    members = MembershipStore(conn)
    members.add_participant("night", "gm1", "GM", "gm")
    members.add_participant("night", "ada-player", "Ada", "player")
    if bind:
        members.bind_principal("night", "ada-player", "telegram", "12345")
    return conn, database


def _runtime_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        "\n".join(
            (
                "OMEGA_AUTH_SECRET=global-secret",
                "TELEGRAM_TOKEN=ambient-generic",
                "TELEGRAM_TOKEN_ADA_PLAYER=ada-secret",
                "TELEGRAM_TOKEN_BO=bo-secret",
                "SLACK_TOKEN=ambient-slack",
                "WS_TOKEN=ambient-ws",
                "ASI_API_KEY=ambient-api",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(handlers, "RUNTIME_ENV_FILE", runtime)
    return runtime


def _args(**values: object) -> SimpleNamespace:
    defaults = {"campaign_id": "night", "gm": False, "participant_id": None, "channel": None}
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_run_compose_forwards_env(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    completed = SimpleNamespace(returncode=0)

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        seen["command"] = command
        seen.update(kwargs)
        return completed

    monkeypatch.setattr(handlers.subprocess, "run", run)
    env = {"TABLETOP_CAMPAIGN": "night", "TABLETOP_DATABASE_PATH": "/container/db"}
    assert handlers._run_compose("omega", "start", env=env) == 0
    assert seen["env"] is env
    assert seen["cwd"] == REPO_ROOT
    assert seen["command"][-3:] == ["up", "-d", "omega"]


def test_run_compose_stop_has_service_last(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        handlers.subprocess,
        "run",
        lambda command, **kwargs: seen.extend(command) or SimpleNamespace(returncode=0),
    )
    assert handlers._run_compose("omega-player-ada-player", "stop", env={}) == 0
    assert seen[-2:] == ["stop", "omega-player-ada-player"]

def test_parser_requires_authoritative_start_channel() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["campaign", "start", "night", "--gm"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["campaign", "start", "night", "--gm", "--channel", "Telegram"]
        )
    stop = parser.parse_args(["campaign", "stop", "night", "--participant", "ada-player"])
    assert not hasattr(stop, "channel")


def test_stop_parser_does_not_require_channel() -> None:
    args = build_parser().parse_args(["campaign", "stop", "night", "--gm"])
    assert args.gm is True
    assert args.participant_id is None


def test_base_compose_contains_only_static_omega() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"omega"}
    assert not any(name.startswith("omega-player-") for name in compose["services"])
    omega_environment = compose["services"]["omega"]["environment"]
    assert "TABLETOP_PARTICIPANT" in omega_environment
    assert "OMEGA_EXPECTED_SENDER" in omega_environment
    assert "OMEGA_COMMCHANNEL" in omega_environment
    assert not any(name.endswith("_GM") for name in omega_environment)


def test_generated_player_definition_matches_canonical_spec(tmp_path: Path) -> None:
    override = player_compose_definition("ada-player")
    service = override["services"]["omega-player-ada-player"]
    assert service["image"] == PLAYER_SERVICE_SPEC.image
    assert service["init"] is PLAYER_SERVICE_SPEC.init
    assert service["restart"] == PLAYER_SERVICE_SPEC.restart
    assert service["security_opt"] == list(PLAYER_SERVICE_SPEC.security_options)
    assert service["environment"]["TABLETOP_WORKSPACE"] == "player"
    assert PLAYER_CREDENTIAL_SLOTS <= set(service["environment"])
    serialized = yaml.safe_dump(override)
    assert "TABLETOP_LOCAL_OPERATOR" not in serialized
    assert "WS_TOKEN_ADA_PLAYER" not in serialized
    assert "OMEGA_AUTH_SECRET_ADA_PLAYER" not in serialized
    assert override["volumes"]["omega-memory-ada-player"]["name"] == (
        "gamemaster-omega-memory-ada-player"
    )


def test_generated_override_is_atomic_and_private(tmp_path: Path) -> None:
    output = tmp_path / "compose"
    first = handlers._generate_compose_override(
        participant_id="ada-player", runtime_compose_dir=output
    )
    second = handlers._generate_compose_override(
        participant_id="ada-player", runtime_compose_dir=output
    )
    assert first == second
    assert output.stat().st_mode & 0o777 == 0o700
    assert first.stat().st_mode & 0o777 == 0o600
    assert list(output.glob("*.tmp")) == []
    assert len(list(yaml.safe_load_all(first.read_text(encoding="utf-8")))) == 1


def test_runtime_env_parser_matches_compose_fixture_syntax(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        "\n".join(
            (
                "BASE=base",
                'QUOTED="line\\nvalue" # comment',
                "EMPTY=",
                "ESCAPED=a\\ b",
                'INTERPOLATED=${BASE}-suffix',
                "DEFAULT=${MISSING:-fallback}",
            )
        ),
        encoding="utf-8",
    )
    values = handlers.parse_runtime_env_file(runtime, environ={})
    assert values == {
        "BASE": "base",
        "QUOTED": "line\nvalue",
        "EMPTY": "",
        "ESCAPED": "a\\ b",
        "INTERPOLATED": "base-suffix",
        "DEFAULT": "fallback",
    }

def test_runtime_env_expansion_handles_nested_defaults() -> None:
    expression = "${MM_BOT_TOKEN:-${MATTERMOST_TOKEN:-}}"
    assert handlers._expand_env_value(expression, {}) == ""
    assert handlers._expand_env_value(expression, {"MM_BOT_TOKEN": "outer"}) == "outer"


def test_start_env_resolves_participant_credentials_and_scrubs_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _runtime_file(tmp_path, monkeypatch)
    monkeypatch.setenv("TABLETOP_LOCAL_OPERATOR", "1")
    monkeypatch.setenv("WS_TOKEN", "DO_NOT_COPY")
    env = handlers.build_launch_env(
        campaign_id="night",
        participant_id="ada-player",
        role="player",
        channel="telegram",
        expected_sender="12345",
        action="start",
        container_database_path="/container/tabletop.sqlite3",
    )
    assert env["TELEGRAM_TOKEN"] == "ada-secret"
    assert env["TG_BOT_TOKEN"] == "ada-secret"
    assert "TELEGRAM_TOKEN_ADA_PLAYER" not in env
    assert "TELEGRAM_TOKEN_BO" not in env
    assert "WS_TOKEN" not in env
    assert "TABLETOP_LOCAL_OPERATOR" not in env
    assert env["TABLETOP_CAMPAIGN"] == "night"
    assert env["TABLETOP_DATABASE_PATH"] == "/container/tabletop.sqlite3"


def test_wrong_channel_does_not_reuse_telegram_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        "OMEGA_AUTH_SECRET=global\nTELEGRAM_TOKEN_ADA_PLAYER=ada-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(handlers, "RUNTIME_ENV_FILE", runtime)
    with pytest.raises(ValueError, match="missing required websocket credential"):
        handlers.build_launch_env(
            campaign_id="night",
            participant_id="ada-player",
            role="player",
            channel="websocket",
            expected_sender="12345",
            action="start",
            container_database_path="/container/db",
        )


def test_stop_environment_is_a_structural_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "WS_TOKEN": "secret",
        "ASI_API_KEY": "secret",
        "OMEGA_AUTH_SECRET": "secret",
        "TABLETOP_LOCAL_OPERATOR": "1",
    }.items():
        monkeypatch.setenv(name, value)
    env = handlers.build_launch_env(
        campaign_id="night",
        participant_id="ada-player",
        role=None,
        channel=None,
        expected_sender=None,
        action="stop",
        container_database_path="/container/db",
    )
    assert env == {
        "PATH": os.environ["PATH"],
        "TABLETOP_CAMPAIGN": "night",
        "TABLETOP_DATABASE_PATH": "/container/db",
        "TABLETOP_PARTICIPANT": "ada-player",
    }


def test_participant_credentials_are_unique(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime_file(tmp_path, monkeypatch)
    ada = handlers.build_launch_env(
        campaign_id="night",
        participant_id="ada-player",
        role="player",
        channel="telegram",
        expected_sender="1",
        action="start",
        container_database_path="/container/db",
    )
    bo = handlers.build_launch_env(
        campaign_id="night",
        participant_id="bo",
        role="player",
        channel="telegram",
        expected_sender="2",
        action="start",
        container_database_path="/container/db",
    )
    assert ada["TELEGRAM_TOKEN"] != bo["TELEGRAM_TOKEN"]


def test_gm_uses_role_scoped_source_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        "OMEGA_AUTH_SECRET=global\nTELEGRAM_TOKEN_GM=gm-secret\n", encoding="utf-8"
    )
    monkeypatch.setattr(handlers, "RUNTIME_ENV_FILE", runtime)
    env = handlers.build_launch_env(
        campaign_id="night",
        participant_id="gm1",
        role="gm",
        channel="telegram",
        expected_sender="gm",
        action="start",
        container_database_path="/container/db",
    )
    assert env["TELEGRAM_TOKEN"] == "gm-secret"
    assert "TELEGRAM_TOKEN_GM" not in env


def test_start_context_binds_authoritative_sender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, database = _campaign(tmp_path)
    conn.close()
    _runtime_file(tmp_path, monkeypatch)
    context = handlers._resolve_launch_context(
        "start",
        campaign_id="night",
        participant_id="ada-player",
        channel="telegram",
        host_database_path=database,
        container_database_path="/container/db",
        runtime_compose_dir=tmp_path / "compose",
    )
    assert context.service_name == "omega-player-ada-player"
    assert context.env["TABLETOP_PARTICIPANT"] == "ada-player"
    assert context.env["OMEGA_EXPECTED_SENDER"] == "12345"
    assert context.workspace is not None and context.workspace.value == "player"


def test_start_context_rejects_missing_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, database = _campaign(tmp_path, bind=False)
    conn.close()
    _runtime_file(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="no principal binding"):
        handlers._resolve_launch_context(
            "start",
            campaign_id="night",
            participant_id="ada-player",
            channel="telegram",
            host_database_path=database,
            container_database_path="/container/db",
            runtime_compose_dir=tmp_path / "compose",
        )


def test_stop_never_opens_database_and_keeps_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("stop opened SQLite")

    monkeypatch.setattr(handlers, "open_database", fail_open)
    context = handlers._resolve_launch_context(
        "stop",
        campaign_id="night",
        participant_id="ada-player",
        container_database_path="/container/db",
        runtime_compose_dir=tmp_path / "compose",
    )
    assert context.host_database_path is None
    assert context.service_name == "omega-player-ada-player"
    assert "OMEGA_COMMCHANNEL" not in context.env
    assert "OMEGA_EXPECTED_SENDER" not in context.env


def test_cmd_stop_does_not_open_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("stop opened SQLite")

    seen: dict[str, object] = {}
    monkeypatch.setattr(handlers, "open_database", fail_open)
    monkeypatch.setattr(
        handlers,
        "_runtime_configuration",
        lambda: ("/container/db", tmp_path / "compose"),
    )
    monkeypatch.setattr(
        handlers,
        "_run_compose",
        lambda service, action, **kwargs: seen.update(
            {"service": service, "action": action, **kwargs}
        )
        or 0,
    )
    assert handlers.cmd_campaign_stop(_args(participant_id="ada-player")) == 0
    assert seen["service"] == "omega-player-ada-player"
    assert seen["action"] == "stop"


def test_gm_start_uses_authoritative_gm_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, database = _campaign(tmp_path)
    MembershipStore(conn).bind_principal("night", "gm1", "irc", "gm-external")
    conn.close()
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        "OMEGA_AUTH_SECRET=global\nTELEGRAM_TOKEN_GM=gm-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(handlers, "RUNTIME_ENV_FILE", runtime)
    context = handlers._resolve_launch_context(
        "start",
        campaign_id="night",
        gm=True,
        channel="irc",
        host_database_path=database,
        container_database_path="/container/db",
        runtime_compose_dir=tmp_path / "compose",
    )
    assert context.service_name == "omega"
    assert context.participant_id == "gm1"
    assert context.env["OMEGA_EXPECTED_SENDER"] == "gm-external"
    assert context.env["OMEGA_COMMCHANNEL"] == "irc"


def test_gm_stop_has_no_participant_identity(tmp_path: Path) -> None:
    context = handlers._resolve_launch_context(
        "stop",
        campaign_id="night",
        gm=True,
        container_database_path="/container/db",
        runtime_compose_dir=tmp_path / "compose",
    )
    assert context.service_name == "omega"
    assert "TABLETOP_PARTICIPANT" not in context.env


def test_container_path_and_runtime_directory_are_separate(tmp_path: Path) -> None:
    output = tmp_path / "runtime-compose"
    override = handlers._generate_compose_override(
        participant_id="ada-player", runtime_compose_dir=output
    )
    assert override.parent == output
    text = override.read_text(encoding="utf-8")
    assert "${TABLETOP_DATABASE_PATH}" in text
    assert "/host/tabletop.sqlite3" not in text


def test_stale_candidate_sender_is_rejected_by_domain_api(tmp_path: Path) -> None:
    conn, database = _campaign(tmp_path)
    try:
        with pytest.raises(sender_binding.SenderBindingError, match="does not match"):
            sender_binding.verify_startup_binding(
                conn,
                workspace=Workspace.PLAYER,
                campaign_id="night",
                participant_id="ada-player",
                environ={
                    "TABLETOP_CAMPAIGN": "night",
                    "TABLETOP_PARTICIPANT": "ada-player",
                    "OMEGA_COMMCHANNEL": "telegram",
                    "OMEGA_EXPECTED_SENDER": "43",
                },
            )
    finally:
        conn.close()


def test_archived_campaign_is_refused_before_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, database = _campaign(tmp_path)
    CampaignStore(conn).archive_campaign("night")
    conn.close()
    _runtime_file(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="archived"):
        handlers._resolve_launch_context(
            "start",
            campaign_id="night",
            participant_id="ada-player",
            channel="telegram",
            host_database_path=database,
            container_database_path="/container/db",
            runtime_compose_dir=tmp_path / "compose",
        )


def test_readiness_refusal_prevents_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    report = {
        "campaign_id": "night",
        "errors": ["pending import proposals"],
        "warnings": [],
        "notices": [],
        "ok": False,
        "exit_nonzero": True,
    }
    monkeypatch.setattr(handlers, "readiness_report", lambda *args, **kwargs: report)
    with pytest.raises(ValueError, match="readiness errors"):
        handlers._resolve_launch_context(
            "start",
            campaign_id="night",
            participant_id="ada-player",
            channel="telegram",
            host_database_path=Path("unused.sqlite3"),
            container_database_path="/container/db",
            runtime_compose_dir=Path("compose"),
        )


def test_source_names_are_absent_from_generated_yaml(tmp_path: Path) -> None:
    generated = yaml.safe_dump(player_compose_definition("ada-player"))
    for source_name in (
        "TELEGRAM_TOKEN_ADA_PLAYER",
        "OMEGA_AUTH_SECRET_ADA_PLAYER",
        "WS_TOKEN_ADA_PLAYER",
    ):
        assert source_name not in generated


def test_compose_config_command_uses_absolute_inputs(tmp_path: Path) -> None:
    override = handlers._generate_compose_override(
        participant_id="ada-player", runtime_compose_dir=tmp_path
    )
    command = handlers._compose_base_command(
        compose_files=(override,), runtime_env_file=handlers.RUNTIME_ENV_FILE
    )
    assert command[0:2] == ["docker", "compose"]
    assert "--project-directory" in command
    assert str(REPO_ROOT) in command
    assert str(handlers.RUNTIME_ENV_FILE.resolve()) in command
    assert command[-2:] == ["-f", str(override.resolve())]


@pytest.mark.skipif(
    not shutil.which("docker") or os.environ.get("GAMEMASTER_RUN_DOCKER") != "1",
    reason="Docker Compose validation is opt-in",
)
def test_compose_config_renders_generated_player(tmp_path: Path) -> None:
    override = handlers._generate_compose_override(
        participant_id="ada-player", runtime_compose_dir=tmp_path
    )
    command = [
        *handlers._compose_base_command(
            compose_files=(override,), runtime_env_file=handlers.RUNTIME_ENV_FILE
        ),
        "config",
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "TABLETOP_CAMPAIGN": "night",
            "TABLETOP_DATABASE_PATH": "/container/db",
            "TABLETOP_PARTICIPANT": "ada-player",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = yaml.safe_load(completed.stdout)
    assert set(rendered["services"]) == {"omega", "omega-player-ada-player"}
    assert rendered["services"]["omega-player-ada-player"]["environment"][
        "TABLETOP_DATABASE_PATH"
    ] == "/container/db"


def test_invalid_participant_cannot_escape_runtime_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        handlers._generate_compose_override(
            participant_id="../escape", runtime_compose_dir=tmp_path
        )
    assert not (tmp_path.parent / "docker-compose.override-escape.yml").exists()
