"""Closed transport models for untrusted extraction proposals."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping

from tabletop.api._contract import (
    freeze_mapping,
    freeze_tuple,
    freeze_value,
    optional_non_empty_str,
    require_non_empty_str,
    to_jsonable,
)


class InvalidProposedExtractionError(ValueError):
    """Raised when proposed extraction data violates its transport schema."""


def _reject_unknown_fields(
    value: Mapping[str, Any],
    model_type: type[Any],
) -> None:
    expected = {item.name for item in fields(model_type)}
    unknown = set(value) - expected
    if unknown:
        names = ", ".join(sorted(unknown))
        raise InvalidProposedExtractionError(
            f"{model_type.__name__} has unknown fields: {names}"
        )


def _require_mapping(value: Any, model_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidProposedExtractionError(
            f"{model_name} must be a mapping, got {type(value).__name__}"
        )
    return value


def _validate_scope(
    *,
    scope_name: str,
    scope: str,
    setting_id: str | None,
    campaign_id: str | None,
) -> None:
    if scope not in {"setting", "campaign"}:
        raise InvalidProposedExtractionError(
            f"{scope_name} must be 'setting' or 'campaign', got {scope!r}"
        )
    if scope == "setting" and (setting_id is None or campaign_id is not None):
        raise InvalidProposedExtractionError(
            f"{scope_name}='setting' requires setting_id and forbids campaign_id"
        )
    if scope == "campaign" and campaign_id is None:
        raise InvalidProposedExtractionError(
            f"{scope_name}='campaign' requires campaign_id"
        )


@dataclass(frozen=True, kw_only=True)
class ProposedEntity:
    """A model-proposed entity with storage-shaped, closed fields."""

    entity_id: str
    owner_scope: str
    setting_id: str | None
    campaign_id: str | None
    overrides_id: str | None
    entity_type: str | None
    name: str
    system_state: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty_str(
            self.entity_id, "entity_id", InvalidProposedExtractionError
        )
        require_non_empty_str(
            self.owner_scope, "owner_scope", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.setting_id, "setting_id", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.campaign_id, "campaign_id", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.overrides_id, "overrides_id", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.entity_type, "entity_type", InvalidProposedExtractionError
        )
        require_non_empty_str(self.name, "name", InvalidProposedExtractionError)
        _validate_scope(
            scope_name="owner_scope",
            scope=self.owner_scope,
            setting_id=self.setting_id,
            campaign_id=self.campaign_id,
        )
        object.__setattr__(
            self,
            "system_state",
            freeze_mapping(
                self.system_state,
                "system_state",
                InvalidProposedExtractionError,
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            freeze_mapping(
                self.metadata,
                "metadata",
                InvalidProposedExtractionError,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "owner_scope": self.owner_scope,
            "setting_id": self.setting_id,
            "campaign_id": self.campaign_id,
            "overrides_id": self.overrides_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "system_state": to_jsonable(self.system_state),
            "metadata": to_jsonable(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProposedEntity:
        data = _require_mapping(value, cls.__name__)
        _reject_unknown_fields(data, cls)
        try:
            return cls(**data)
        except TypeError as exc:
            raise InvalidProposedExtractionError(
                f"invalid {cls.__name__} fields: {exc}"
            ) from exc


@dataclass(frozen=True, kw_only=True)
class ProposedFact:
    """A model-proposed fact that can only remain proposed."""

    fact_id: str
    fact_scope: str
    setting_id: str | None
    campaign_id: str | None
    subject_id: str | None
    predicate: str
    value: Any
    canon_state: str = "proposed"
    visibility: str = "GM"
    valid_from: str | None = None
    valid_until: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_str(self.fact_id, "fact_id", InvalidProposedExtractionError)
        require_non_empty_str(
            self.fact_scope, "fact_scope", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.setting_id, "setting_id", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.campaign_id, "campaign_id", InvalidProposedExtractionError
        )
        optional_non_empty_str(
            self.subject_id, "subject_id", InvalidProposedExtractionError
        )
        require_non_empty_str(
            self.predicate, "predicate", InvalidProposedExtractionError
        )
        if self.canon_state != "proposed":
            raise InvalidProposedExtractionError(
                "canon_state must be 'proposed' for extracted facts"
            )
        require_non_empty_str(
            self.visibility, "visibility", InvalidProposedExtractionError
        )
        for field_name in (
            "valid_from",
            "valid_until",
            "source_document_id",
            "source_chunk_id",
        ):
            optional_non_empty_str(
                getattr(self, field_name),
                field_name,
                InvalidProposedExtractionError,
            )
        _validate_scope(
            scope_name="fact_scope",
            scope=self.fact_scope,
            setting_id=self.setting_id,
            campaign_id=self.campaign_id,
        )
        object.__setattr__(
            self,
            "value",
            freeze_value(self.value, "value", InvalidProposedExtractionError),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_scope": self.fact_scope,
            "setting_id": self.setting_id,
            "campaign_id": self.campaign_id,
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "value": to_jsonable(self.value),
            "canon_state": self.canon_state,
            "visibility": self.visibility,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "source_document_id": self.source_document_id,
            "source_chunk_id": self.source_chunk_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProposedFact:
        data = _require_mapping(value, cls.__name__)
        _reject_unknown_fields(data, cls)
        try:
            return cls(**data)
        except TypeError as exc:
            raise InvalidProposedExtractionError(
                f"invalid {cls.__name__} fields: {exc}"
            ) from exc

@dataclass(frozen=True, kw_only=True)
class ProposedExtraction:
    """One extraction slice containing only closed entity and fact proposals."""

    job_id: str
    slice_index: int
    extractor_version: str
    entities: tuple[ProposedEntity, ...] = ()
    facts: tuple[ProposedFact, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty_str(self.job_id, "job_id", InvalidProposedExtractionError)
        if (
            isinstance(self.slice_index, bool)
            or not isinstance(self.slice_index, int)
            or self.slice_index < 0
        ):
            raise InvalidProposedExtractionError(
                "slice_index must be a non-negative integer"
            )
        require_non_empty_str(
            self.extractor_version,
            "extractor_version",
            InvalidProposedExtractionError,
        )
        object.__setattr__(
            self,
            "entities",
            freeze_tuple(
                self.entities,
                ProposedEntity,
                "entities",
                InvalidProposedExtractionError,
            ),
        )
        object.__setattr__(
            self,
            "facts",
            freeze_tuple(
                self.facts,
                ProposedFact,
                "facts",
                InvalidProposedExtractionError,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "slice_index": self.slice_index,
            "extractor_version": self.extractor_version,
            "entities": [entity.to_dict() for entity in self.entities],
            "facts": [fact.to_dict() for fact in self.facts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProposedExtraction:
        data = _require_mapping(value, cls.__name__)
        _reject_unknown_fields(data, cls)
        converted = dict(data)
        try:
            converted["entities"] = tuple(
                ProposedEntity.from_dict(item)
                for item in converted.get("entities", ())
            )
            converted["facts"] = tuple(
                ProposedFact.from_dict(item) for item in converted.get("facts", ())
            )
            return cls(**converted)
        except TypeError as exc:
            raise InvalidProposedExtractionError(
                f"invalid {cls.__name__} fields: {exc}"
            ) from exc
