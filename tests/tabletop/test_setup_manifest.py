"""The setup manifest is strict, safe, and never executes what it names."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabletop.campaign.setup import (
    CampaignSetupManifest,
    SetupManifestError,
    load_setup_manifest,
    parse_setup_manifest,
)

_BASE = {
    "campaign_id": "demo-campaign",
    "name": "Demo Campaign",
    "system_id": "freeform",
}


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "rules.md").write_text("Gate DC 15", encoding="utf-8")
    path = tmp_path / "campaign.setup.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _minimal(tmp_path: Path) -> CampaignSetupManifest:
    return parse_setup_manifest(dict(_BASE), base_dir=tmp_path)


# -- required shape ---------------------------------------------------------


def test_a_minimal_manifest_parses(tmp_path: Path) -> None:
    manifest = _minimal(tmp_path)
    assert manifest.campaign_id == "demo-campaign"
    assert manifest.system_id == "freeform"
    assert manifest.participants == ()
    assert manifest.starting_scene is None


def test_the_full_shape_parses(tmp_path: Path) -> None:
    (tmp_path / "rules.md").write_text("Gate DC 15", encoding="utf-8")
    manifest = parse_setup_manifest(
        {
            **_BASE,
            "setting_id": "demo-setting",
            "participants": [
                {
                    "participant_id": "gm-1",
                    "display_name": "The GM",
                    "role": "gm",
                    "character_ids": ["npc-vor"],
                    "principals": [["telegram", "12345"]],
                },
                {
                    "participant_id": "player-1",
                    "display_name": "Ada",
                    "character_ids": ["pc-ada"],
                },
            ],
            "characters": [
                {"entity_id": "pc-ada", "name": "Ada"},
                {"entity_id": "npc-vor", "name": "Vor", "participant_id": "gm-1"},
            ],
            "content": [{"path": "rules.md", "role": "rules"}],
            "starting_state": {"scene": {"lanterns": 3}},
            "starting_scene": {
                "scene_id": "scene-1",
                "name": "Flooded Hall",
                "present": ["pc-ada"],
            },
            "game_time": {"in_world_label": "Day 1"},
        },
        base_dir=tmp_path,
    )
    assert manifest.setting_id == "demo-setting"
    assert len(manifest.participants) == 2
    assert manifest.participants[0].role == "gm"
    assert manifest.characters[1] == ("npc-vor", "Vor", "gm-1")
    assert manifest.content[0].role == "rules"
    assert manifest.starting_scene is not None
    assert manifest.starting_scene.present == ("pc-ada",)


def test_an_unknown_field_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="unexpected fields"):
        parse_setup_manifest({**_BASE, "difficulty_class": 15}, base_dir=tmp_path)


def test_a_missing_required_field_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="missing required fields"):
        parse_setup_manifest({"campaign_id": "demo"}, base_dir=tmp_path)


def test_an_invalid_campaign_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="campaign_id must match"):
        parse_setup_manifest({**_BASE, "campaign_id": "Not A Slug"}, base_dir=tmp_path)


def test_an_invalid_system_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="system_id must match"):
        parse_setup_manifest({**_BASE, "system_id": "Freeform!"}, base_dir=tmp_path)


def test_more_than_one_gm_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="at most one gm"):
        parse_setup_manifest(
            {
                **_BASE,
                "participants": [
                    {"participant_id": "gm-1", "display_name": "A", "role": "gm"},
                    {"participant_id": "gm-2", "display_name": "B", "role": "gm"},
                ],
            },
            base_dir=tmp_path,
        )


def test_a_duplicate_participant_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="duplicate participant"):
        parse_setup_manifest(
            {
                **_BASE,
                "participants": [
                    {"participant_id": "p1", "display_name": "A"},
                    {"participant_id": "p1", "display_name": "B"},
                ],
            },
            base_dir=tmp_path,
        )


def test_an_unknown_content_role_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(SetupManifestError, match="unknown content role"):
        parse_setup_manifest(
            {**_BASE, "content": [{"path": "notes.md", "role": "lore"}]},
            base_dir=tmp_path,
        )


# -- safety -----------------------------------------------------------------


def test_an_absolute_content_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="must be relative"):
        parse_setup_manifest(
            {**_BASE, "content": [{"path": "/etc/passwd"}]}, base_dir=tmp_path
        )


def test_a_traversing_content_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="escapes the manifest directory"):
        parse_setup_manifest(
            {**_BASE, "content": [{"path": "../outside.md"}]}, base_dir=tmp_path
        )


def test_a_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "linked.md").symlink_to(outside)
    with pytest.raises(SetupManifestError, match="escapes the manifest directory"):
        parse_setup_manifest(
            {**_BASE, "content": [{"path": "linked.md"}]}, base_dir=tmp_path
        )


def test_an_executable_content_path_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "payload.py").write_text("import os", encoding="utf-8")
    with pytest.raises(SetupManifestError, match="executable material"):
        parse_setup_manifest(
            {**_BASE, "content": [{"path": "payload.py"}]}, base_dir=tmp_path
        )


def test_a_missing_content_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="does not exist"):
        parse_setup_manifest(
            {**_BASE, "content": [{"path": "absent.md"}]}, base_dir=tmp_path
        )


@pytest.mark.parametrize(
    "key", ["token", "api_key", "password", "secret", "private_key", "credentials"]
)
def test_a_credential_shaped_key_is_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(SetupManifestError, match="credential"):
        parse_setup_manifest({**_BASE, key: "hunter2"}, base_dir=tmp_path)


def test_a_nested_credential_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="credential"):
        parse_setup_manifest(
            {**_BASE, "game_time": {"label": "Day 1", "api_token": "x"}},
            base_dir=tmp_path,
        )


def test_unsafe_yaml_tags_are_rejected(tmp_path: Path) -> None:
    sentinel = tmp_path / "yaml-executed"
    path = tmp_path / "campaign.setup.yaml"
    path.write_text(
        f"campaign_id: !!python/object/apply:pathlib.Path.touch ['{sentinel}']\n"
        "name: Demo\n"
        "system_id: freeform\n",
        encoding="utf-8",
    )
    with pytest.raises(SetupManifestError):
        load_setup_manifest(path)
    assert not sentinel.exists()


def test_loading_a_missing_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupManifestError, match="not found"):
        load_setup_manifest(tmp_path / "absent.yaml")


def test_a_manifest_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "campaign.setup.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SetupManifestError, match="must be a mapping"):
        load_setup_manifest(path)


# -- read-only --------------------------------------------------------------


def test_loading_a_manifest_opens_no_database_and_writes_nothing(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        """
        campaign_id: demo-campaign
        name: Demo Campaign
        system_id: freeform
        content:
          - path: rules.md
            role: rules
        """,
    )
    manifest = load_setup_manifest(path)
    assert manifest.content[0].path == (tmp_path / "rules.md").resolve()
    # Inspection read the file; it created nothing.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "campaign.setup.yaml",
        "rules.md",
    ]


def test_the_manifest_serializes_to_plain_data(tmp_path: Path) -> None:
    manifest = parse_setup_manifest(
        {**_BASE, "starting_scene": {"scene_id": "scene-1", "name": "Hall"}},
        base_dir=tmp_path,
    )
    payload = manifest.to_dict()
    assert payload["campaign_id"] == "demo-campaign"
    assert payload["starting_scene"]["scene_id"] == "scene-1"


# -- malformed field types -------------------------------------------------


@pytest.mark.parametrize("field", ["participants", "characters", "content"])
@pytest.mark.parametrize("value", [1, "text", 3.5, {"a": 1}, True])
def test_a_scalar_where_a_list_belongs_is_a_manifest_error(
    tmp_path: Path, field: str, value: object
) -> None:
    """An operator mistake must be explained, not raised as a TypeError.

    Iterating a bare integer is a Python failure the operator cannot act on.
    """
    with pytest.raises(SetupManifestError, match="must be a list"):
        parse_setup_manifest({**_BASE, field: value}, base_dir=tmp_path)


@pytest.mark.parametrize("value", [1, "text", 3.5, [1, 2]])
def test_a_malformed_starting_scene_is_a_manifest_error(
    tmp_path: Path, value: object
) -> None:
    with pytest.raises(SetupManifestError):
        parse_setup_manifest({**_BASE, "starting_scene": value}, base_dir=tmp_path)


@pytest.mark.parametrize(
    "field", ["starting_state", "game_time", "setting_id", "name"]
)
def test_a_malformed_scalar_field_is_a_manifest_error(
    tmp_path: Path, field: str
) -> None:
    with pytest.raises(SetupManifestError):
        parse_setup_manifest({**_BASE, field: 42}, base_dir=tmp_path)


def test_a_duplicate_character_is_rejected_before_any_write(tmp_path: Path) -> None:
    """Two characters with one id would conflict only after the commit.

    The campaign and the first entity would already be written, contradicting
    the promise that a failure leaves nothing half-configured.
    """
    with pytest.raises(SetupManifestError, match="duplicate character"):
        parse_setup_manifest(
            {
                **_BASE,
                "characters": [
                    {"entity_id": "npc-1", "name": "Aldric"},
                    {"entity_id": "npc-1", "name": "Borin"},
                ],
            },
            base_dir=tmp_path,
        )


@pytest.mark.parametrize(
    "principals",
    [["telegram:12345"], [["telegram"]], [42], [[None, "1"]], [["", "1"]], [["tg", ""]]],
)
def test_a_malformed_principal_binding_is_rejected(
    tmp_path: Path, principals: object
) -> None:
    """A shorthand string would silently bind channel 't' to id 'e'."""
    with pytest.raises(SetupManifestError):
        parse_setup_manifest(
            {
                **_BASE,
                "participants": [
                    {
                        "participant_id": "player-1",
                        "display_name": "Ada",
                        "principals": principals,
                    }
                ],
            },
            base_dir=tmp_path,
        )


def test_a_well_formed_principal_binding_parses(tmp_path: Path) -> None:
    manifest = parse_setup_manifest(
        {
            **_BASE,
            "participants": [
                {
                    "participant_id": "player-1",
                    "display_name": "Ada",
                    "principals": [["telegram", "12345"]],
                }
            ],
        },
        base_dir=tmp_path,
    )
    assert manifest.participants[0].principals == (("telegram", "12345"),)
