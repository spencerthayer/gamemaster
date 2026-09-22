"""SQLite-backed authoritative campaign store.

Campaign truth lives here, never in vector recall. Mutation methods on
``CampaignStore`` are internal persistence primitives. Application services
must eventually compose each authoritative mutation with its event in one
transaction; this module does not implement events.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, assert_never

from tabletop.api.errors import InvalidResolutionError
from tabletop.api.resolution import StateChange
from tabletop.api.visibility import Viewpoint
from tabletop.campaign.invariants import check_fact_invariants
from tabletop.campaign.models import CanonState, Fact, FactScope, KnowledgeState
from tabletop.campaign.state_paths import apply_json_change, route_state_change_path
from tabletop.campaign.visibility import visible_facts_clause
from tabletop.storage.sqlite import transaction


class CampaignStore:
    """Read and write authoritative campaign records in SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_campaign(
        self,
        campaign_id: str,
        name: str,
        system_id: str,
        *,
        setting_id: str | None = None,
        created_at: str | None = None,
        system_state: Mapping[str, Any] | None = None,
    ) -> None:
        if created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()
        encoded_state = _encode_json({} if system_state is None else system_state)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO campaigns "
                "(campaign_id, name, system_id, setting_id, created_at, system_state) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    name,
                    system_id,
                    setting_id,
                    created_at,
                    encoded_state,
                ),
            )

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT campaign_id, name, system_id, setting_id, created_at, system_state "
            "FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return _campaign_from_row(row) if row is not None else None

    def list_campaigns(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT campaign_id, name, system_id, setting_id, created_at, system_state "
            "FROM campaigns ORDER BY created_at, campaign_id"
        ).fetchall()
        return [_campaign_from_row(row) for row in rows]

    def upsert_entity(
        self,
        campaign_id: str,
        entity_id: str,
        name: str,
        *,
        entity_type: str | None = None,
        system_state: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        overrides_id: str | None = None,
    ) -> None:
        encoded_state = _encode_json({} if system_state is None else system_state)
        encoded_metadata = _encode_json({} if metadata is None else metadata)
        with transaction(self.conn):
            cursor = self.conn.execute(
                "UPDATE entities SET overrides_id = ?, entity_type = ?, name = ?, "
                "system_state = ?, metadata = ? "
                "WHERE owner_scope = 'campaign' AND campaign_id = ? AND entity_id = ?",
                (
                    overrides_id,
                    entity_type,
                    name,
                    encoded_state,
                    encoded_metadata,
                    campaign_id,
                    entity_id,
                ),
            )
            if cursor.rowcount == 0:
                self.conn.execute(
                    "INSERT INTO entities "
                    "(entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
                    "entity_type, name, system_state, metadata) "
                    "VALUES (?, 'campaign', NULL, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_id,
                        campaign_id,
                        overrides_id,
                        entity_type,
                        name,
                        encoded_state,
                        encoded_metadata,
                    ),
                )

    def get_entity(
        self, campaign_id: str, entity_id: str
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT entity_id, owner_scope, setting_id, campaign_id, overrides_id, "
            "entity_type, name, system_state, metadata FROM entities "
            "WHERE owner_scope = 'campaign' AND campaign_id = ? AND entity_id = ?",
            (campaign_id, entity_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["system_state"] = json.loads(result["system_state"])
        result["metadata"] = json.loads(result["metadata"])
        return result

    def add_fact(self, fact: Fact) -> None:
        check_fact_invariants(fact)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO facts "
                "(fact_id, fact_scope, setting_id, campaign_id, subject_id, predicate, "
                "value, canon_state, knowledge_state, visibility, valid_from, "
                "valid_until, source_document_id, source_chunk_id, import_job_id, "
                "extraction_method, source_ownership, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fact.fact_id,
                    fact.fact_scope.value,
                    fact.setting_id,
                    fact.campaign_id,
                    fact.subject_id,
                    fact.predicate,
                    fact.value,
                    fact.canon_state.value,
                    fact.knowledge_state.value,
                    fact.visibility,
                    fact.valid_from,
                    fact.valid_until,
                    fact.source_document_id,
                    fact.source_chunk_id,
                    fact.import_job_id,
                    fact.extraction_method,
                    fact.source_ownership,
                    fact.created_at,
                ),
            )

    def get_facts(
        self, campaign_id: str, *, viewpoint: Viewpoint
    ) -> list[Fact]:
        visibility_clause, visibility_params = visible_facts_clause(viewpoint)
        rows = self.conn.execute(
            "SELECT fact_id, fact_scope, setting_id, campaign_id, subject_id, "
            "predicate, value, canon_state, knowledge_state, visibility, valid_from, "
            "valid_until, source_document_id, source_chunk_id, import_job_id, "
            "extraction_method, source_ownership, created_at "
            f"FROM facts WHERE campaign_id = ? AND ({visibility_clause}) "
            "ORDER BY created_at, fact_id",
            (campaign_id, *visibility_params),
        ).fetchall()
        return [_fact_from_row(row) for row in rows]

    def apply_state_changes(
        self,
        campaign_id: str,
        changes: Iterable[StateChange],
        *,
        scene_id: str | None = None,
    ) -> None:
        with transaction(self.conn):
            self.apply_state_changes_in_transaction(
                campaign_id, changes, scene_id=scene_id
            )

    def apply_state_changes_in_transaction(
        self,
        campaign_id: str,
        changes: Iterable[StateChange],
        *,
        scene_id: str | None = None,
    ) -> None:
        """Apply state changes using a transaction already owned by the caller."""

        planned = tuple(
            route_state_change_path(campaign_id, change, scene_id)
            for change in changes
        )
        staged: dict[tuple[str, str], Any] = {}

        for change, target, relative_path in planned:
            if target not in staged:
                staged[target] = self._load_state_target(
                    campaign_id, target, scene_id
                )
            staged[target] = apply_json_change(
                staged[target], relative_path, change
            )

        for target, state in staged.items():
            self._write_state_target(campaign_id, target, scene_id, state)

    def _load_state_target(
        self,
        campaign_id: str,
        target: tuple[str, str],
        scene_id: str | None,
    ) -> Any:
        target_kind, target_id = target
        if target_kind == "campaign":
            row = self.conn.execute(
                "SELECT system_state FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            label = f"campaign {campaign_id!r}"
        elif target_kind == "entity":
            row = self.conn.execute(
                "SELECT system_state FROM entities "
                "WHERE owner_scope = 'campaign' AND campaign_id = ? AND entity_id = ?",
                (campaign_id, target_id),
            ).fetchone()
            label = f"entity {target_id!r} in campaign {campaign_id!r}"
        elif target_kind == "scene":
            row = self.conn.execute(
                "SELECT system_state FROM scenes "
                "WHERE campaign_id = ? AND scene_id = ?",
                (campaign_id, scene_id),
            ).fetchone()
            label = f"scene {scene_id!r} in campaign {campaign_id!r}"
        else:
            assert_never(target_kind)
        if row is None:
            raise InvalidResolutionError(f"{label} does not exist")
        return json.loads(row["system_state"])

    def _write_state_target(
        self,
        campaign_id: str,
        target: tuple[str, str],
        scene_id: str | None,
        state: Any,
    ) -> None:
        target_kind, target_id = target
        encoded = _encode_json(state)
        if target_kind == "campaign":
            self.conn.execute(
                "UPDATE campaigns SET system_state = ? WHERE campaign_id = ?",
                (encoded, campaign_id),
            )
        elif target_kind == "entity":
            self.conn.execute(
                "UPDATE entities SET system_state = ? "
                "WHERE owner_scope = 'campaign' AND campaign_id = ? AND entity_id = ?",
                (encoded, campaign_id, target_id),
            )
        elif target_kind == "scene":
            self.conn.execute(
                "UPDATE scenes SET system_state = ? "
                "WHERE campaign_id = ? AND scene_id = ?",
                (encoded, campaign_id, scene_id),
            )
        else:
            assert_never(target_kind)


def _encode_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _campaign_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["system_state"] = json.loads(result["system_state"])
    return result


def _fact_from_row(row: sqlite3.Row) -> Fact:
    return Fact(
        fact_id=row["fact_id"],
        fact_scope=FactScope(row["fact_scope"]),
        setting_id=row["setting_id"],
        campaign_id=row["campaign_id"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        value=row["value"],
        canon_state=CanonState(row["canon_state"]),
        knowledge_state=KnowledgeState(row["knowledge_state"]),
        visibility=row["visibility"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        source_document_id=row["source_document_id"],
        source_chunk_id=row["source_chunk_id"],
        import_job_id=row["import_job_id"],
        extraction_method=row["extraction_method"],
        source_ownership=row["source_ownership"],
        created_at=row["created_at"],
    )
