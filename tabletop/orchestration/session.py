"""Bounded session records and their ordered closing checklist."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, assert_never

from tabletop.api.events import GameEvent
from tabletop.campaign.event_store import EventStore, EventType
from tabletop.campaign.projections import (
    CampaignProjection,
    project_campaign,
    write_projection,
)
from tabletop.retrieval.lexical import LexicalRetriever
from tabletop.retrieval.models import RetrievalNamespace
from tabletop.storage.sqlite import transaction


class EndSessionStep(str, Enum):
    """One required step in the closing sequence."""

    CLOSE_EVENT_RANGE = "close_event_range"
    WRITE_SUMMARY = "write_summary"
    REGENERATE_PROJECTIONS = "regenerate_projections"
    UPDATE_RETRIEVAL = "update_retrieval"


END_SESSION_CHECKLIST: tuple[EndSessionStep, ...] = (
    EndSessionStep.CLOSE_EVENT_RANGE,
    EndSessionStep.WRITE_SUMMARY,
    EndSessionStep.REGENERATE_PROJECTIONS,
    EndSessionStep.UPDATE_RETRIEVAL,
)


@dataclass(frozen=True, slots=True)
class EventRange:
    """Inclusive event sequence bounds for one session."""

    start: int | None
    end: int | None

    def to_dict(self) -> dict[str, int | None]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class Session:
    """Complete persisted session record."""

    session_id: str
    campaign_id: str
    started_at: str
    ended_at: str | None
    participants: tuple[str, ...]
    transcript_reference: str | None
    event_range: EventRange
    summary: str | None
    important_facts: tuple[object, ...]
    open_threads: tuple[object, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "campaign_id": self.campaign_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "participants": list(self.participants),
            "transcript_reference": self.transcript_reference,
            "event_range": self.event_range.to_dict(),
            "summary": self.summary,
            "important_facts": list(self.important_facts),
            "open_threads": list(self.open_threads),
        }


SummaryProvider = Callable[[Session], str | None]
ProjectionWriter = Callable[[CampaignProjection, Path], None]
RetrievalUpdater = Callable[[sqlite3.Connection, Session], None]
StepObserver = Callable[[EndSessionStep], None]


class SessionLifecycle:
    """Close the active session without mutating campaign truth."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        projection_directory: Path | str,
        *,
        projection_writer: ProjectionWriter = write_projection,
        retrieval_updater: RetrievalUpdater | None = None,
        step_observer: StepObserver | None = None,
    ) -> None:
        self._connection = connection
        self._projection_directory = Path(projection_directory)
        self._projection_writer = projection_writer
        self._retrieval_updater = retrieval_updater or _update_retrieval
        self._step_observer = step_observer

    def end_session(
        self,
        campaign_id: str,
        *,
        summary_provider: SummaryProvider | None = None,
        ended_at: str | None = None,
    ) -> Session:
        """Run every closing step in the declared order."""

        session, completed_steps = self._pending_session(campaign_id)
        closed_at = (
            session.ended_at
            or ended_at
            or datetime.now(timezone.utc).isoformat()
        )

        for step_number, step in enumerate(END_SESSION_CHECKLIST, start=1):
            if step_number <= completed_steps:
                continue
            self._observe(step)
            session = self._run_step(
                step,
                session,
                closed_at=closed_at,
                summary_provider=summary_provider,
            )
            session = self._mark_step_complete(session, step_number)
        return session

    def _pending_session(self, campaign_id: str) -> tuple[Session, int]:
        row = self._connection.execute(
            "SELECT session_id, campaign_id, started_at, ended_at, participants, "
            "transcript_reference, event_start_sequence, event_end_sequence, "
            "summary, important_facts, open_threads, checklist_step FROM sessions "
            "WHERE campaign_id = ? "
            "AND (ended_at IS NULL OR checklist_step < ?) "
            "ORDER BY started_at DESC, session_id DESC LIMIT 1",
            (campaign_id, len(END_SESSION_CHECKLIST)),
        ).fetchone()
        if row is None:
            raise LookupError(f"no open or incomplete session for campaign: {campaign_id}")
        return _session_from_row(row), int(row["checklist_step"])

    def _run_step(
        self,
        step: EndSessionStep,
        session: Session,
        *,
        closed_at: str,
        summary_provider: SummaryProvider | None,
    ) -> Session:
        match step:
            case EndSessionStep.CLOSE_EVENT_RANGE:
                return replace(session, ended_at=closed_at)
            case EndSessionStep.WRITE_SUMMARY:
                summary = self._summary_or_none(summary_provider, session)
                self._write_summary(session.session_id, summary)
                return replace(session, summary=summary)
            case EndSessionStep.REGENERATE_PROJECTIONS:
                events = EventStore(self._connection).read(session.campaign_id)
                projection = (
                    project_campaign(events)
                    if events
                    else CampaignProjection(
                        campaign_id=session.campaign_id,
                        sequence=0,
                    )
                )
                self._projection_writer(projection, self._projection_directory)
                return session
            case EndSessionStep.UPDATE_RETRIEVAL:
                self._retrieval_updater(self._connection, session)
                self._connection.commit()
                return session
            case _:
                assert_never(step)

    def _close_event_range(self, session_id: str) -> EventRange:
        row = self._connection.execute(
            "SELECT MIN(sequence), MAX(sequence) FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return EventRange(start=row[0], end=row[1])

    def _mark_step_complete(self, session: Session, step_number: int) -> Session:
        step = END_SESSION_CHECKLIST[step_number - 1]
        with transaction(self._connection):
            if step is EndSessionStep.CLOSE_EVENT_RANGE:
                if session.ended_at is not None:
                    EventStore(self._connection).append_in_transaction(
                        self._connection,
                        session.campaign_id,
                        GameEvent(
                            event_type=EventType.SESSION_ENDED.value,
                            payload={
                                "session_id": session.session_id,
                                "ended_at": session.ended_at,
                            },
                        ),
                        session_id=session.session_id,
                        occurred_at=session.ended_at,
                    )
                event_range = self._close_event_range(session.session_id)
                cursor = self._connection.execute(
                    "UPDATE sessions SET ended_at = COALESCE(ended_at, ?), "
                    "event_start_sequence = ?, event_end_sequence = ?, "
                    "checklist_step = ? WHERE session_id = ? AND checklist_step < ?",
                    (
                        session.ended_at,
                        event_range.start,
                        event_range.end,
                        step_number,
                        session.session_id,
                        step_number,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LookupError(
                        "session checklist step was not recorded: "
                        f"{session.session_id}"
                    )
                return replace(session, event_range=event_range)
            cursor = self._connection.execute(
                "UPDATE sessions SET checklist_step = ? "
                "WHERE session_id = ? AND checklist_step < ?",
                (step_number, session.session_id, step_number),
            )
            if cursor.rowcount != 1:
                raise LookupError(
                    "session checklist step was not recorded: "
                    f"{session.session_id}"
                )
        return session

    def _write_summary(self, session_id: str, summary: str | None) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "UPDATE sessions SET summary = ? WHERE session_id = ?",
                (summary, session_id),
            )

    def _observe(self, step: EndSessionStep) -> None:
        if self._step_observer is not None:
            self._step_observer(step)

    @staticmethod
    def _summary_or_none(
        provider: SummaryProvider | None,
        session: Session,
    ) -> str | None:
        if provider is None:
            return None
        try:
            summary = provider(session)
        except Exception:
            return None
        if summary is None:
            return None
        normalized = summary.strip()
        return normalized or None


def _session_from_row(row: Mapping[str, object]) -> Session:
    return Session(
        session_id=str(row["session_id"]),
        campaign_id=str(row["campaign_id"]),
        started_at=str(row["started_at"]),
        ended_at=None if row["ended_at"] is None else str(row["ended_at"]),
        participants=tuple(_decode_list(row["participants"])),
        transcript_reference=(
            None
            if row["transcript_reference"] is None
            else str(row["transcript_reference"])
        ),
        event_range=EventRange(
            start=_optional_int(row["event_start_sequence"]),
            end=_optional_int(row["event_end_sequence"]),
        ),
        summary=None if row["summary"] is None else str(row["summary"]),
        important_facts=tuple(_decode_list(row["important_facts"])),
        open_threads=tuple(_decode_list(row["open_threads"])),
    )


def _decode_list(value: object) -> list[object]:
    decoded = json.loads(str(value))
    if not isinstance(decoded, list):
        raise ValueError("session list fields must contain JSON arrays")
    return decoded


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _update_retrieval(connection: sqlite3.Connection, session: Session) -> None:
    event_range = session.event_range
    text = "\n".join(
        (
            f"Session: {session.session_id}",
            f"Event range: {event_range.start} through {event_range.end}",
            f"Transcript: {session.transcript_reference or ''}",
            f"Summary: {session.summary or ''}",
            f"Important facts: {json.dumps(session.important_facts)}",
            f"Open threads: {json.dumps(session.open_threads)}",
        )
    )
    LexicalRetriever(connection).index(
        RetrievalNamespace.CAMPAIGN,
        chunk_id=f"session:{session.session_id}",
        document_id=session.session_id,
        document_title=f"Session {session.session_id}",
        section="Session record",
        text=text,
        source_path=session.transcript_reference or "",
        visibility="GM",
    )
