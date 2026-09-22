"""Campaign data models.

Typed models for campaigns, sessions, scenes, entities, and facts shared by
the store, event log, and orchestration layers. Phase 11 of the execution
plan fills this in.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CanonState(str, Enum):
    """Whether a fact is proposed or accepted as canon."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


class KnowledgeState(str, Enum):
    """Whether a confirmed fact has been revealed."""

    UNREVEALED = "unrevealed"
    KNOWN = "known"


class FactScope(str, Enum):
    """The setting or campaign that owns a fact."""

    SETTING = "setting"
    CAMPAIGN = "campaign"


@dataclass(frozen=True, kw_only=True)
class Fact:
    """A setting or campaign fact with independent canon and knowledge state."""

    fact_id: str
    fact_scope: FactScope
    setting_id: str | None
    campaign_id: str | None
    subject_id: str | None
    predicate: str
    value: str
    canon_state: CanonState = CanonState.PROPOSED
    knowledge_state: KnowledgeState = KnowledgeState.UNREVEALED
    visibility: str = "GM"
    valid_from: str | None = None
    valid_until: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    import_job_id: str | None = None
    extraction_method: str | None = None
    source_ownership: str = "attached"
    created_at: str
