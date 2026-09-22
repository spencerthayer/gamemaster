"""Deterministic document-shape detection from countable text signals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


SHAPE_SCORE_THRESHOLD = 0.30
PARAGRAPH_WORD_TARGET = 30
QUOTE_DENSITY_TARGET = 0.03
SPEECH_VERB_DENSITY_TARGET = 0.02
MIN_TABLE_ROWS = 3

STRUCTURED_HEADING_WEIGHT = 0.60
STRUCTURED_LABEL_WEIGHT = 0.40
PROSE_PARAGRAPH_WEIGHT = 0.55
PROSE_QUOTATION_WEIGHT = 0.25
PROSE_SPEECH_WEIGHT = 0.20
TABLE_CONSISTENCY_WEIGHT = 0.80
TABLE_ROW_WEIGHT = 0.20

_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_WORD_PATTERN = re.compile(r"\b[\w'-]+\b")
_QUOTATION_PATTERN = re.compile(r'["“”]')
_SPEECH_VERB_PATTERN = re.compile(
    r"\b(?:said|asked|replied|answered|told|called|cried|"
    r"whispered|shouted|murmured)\b",
    re.IGNORECASE,
)


class DocumentShape(str, Enum):
    """Generic routes supported by document ingestion."""

    STRUCTURED_RULES = "structured_rules"
    PROSE = "prose"
    REFERENCE_TABLE = "reference_table"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ShapeSignals:
    """Raw counts and derived ratios used by the detector."""

    nonempty_line_count: int
    heading_line_count: int
    heading_line_ratio: float
    paragraph_count: int
    paragraph_word_count: int
    average_paragraph_length: float
    quotation_mark_count: int
    quotation_density: float
    speech_verb_count: int
    speech_verb_density: float
    delimited_row_count: int
    consistent_column_row_count: int
    column_consistency: float
    colon_label_line_count: int
    colon_label_ratio: float


@dataclass(frozen=True)
class ShapeDetection:
    """Chosen route, winning score, and evidence behind that choice."""

    shape: DocumentShape
    score: float
    signals: ShapeSignals
    scores: Mapping[DocumentShape, float]

    @property
    def requires_manual_review(self) -> bool:
        return self.shape is DocumentShape.UNSUPPORTED

    @property
    def parser(self) -> str | None:
        if self.requires_manual_review:
            return None
        return self.shape.value


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _paragraphs(text: str) -> list[str]:
    return [block for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]


def _column_signatures(lines: list[str]) -> list[tuple[str, int]]:
    signatures: list[tuple[str, int]] = []
    for line in lines:
        delimiter = "\t" if "\t" in line else "|" if "|" in line else None
        if delimiter is None:
            continue
        column_count = len(line.split(delimiter))
        if column_count >= 2:
            signatures.append((delimiter, column_count))
    return signatures


def _signals(text: str) -> ShapeSignals:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line_count = len(lines)
    headings = sum(bool(_HEADING_PATTERN.match(line)) for line in lines)
    labels = sum(line.endswith(":") for line in lines)

    paragraphs = _paragraphs(text)
    paragraph_words = [len(_WORD_PATTERN.findall(block)) for block in paragraphs]
    total_words = len(_WORD_PATTERN.findall(text))
    quotation_marks = len(_QUOTATION_PATTERN.findall(text))
    speech_verbs = len(_SPEECH_VERB_PATTERN.findall(text))

    signatures = _column_signatures(lines)
    signature_counts = {
        signature: signatures.count(signature) for signature in set(signatures)
    }
    consistent_rows = max(signature_counts.values(), default=0)

    return ShapeSignals(
        nonempty_line_count=line_count,
        heading_line_count=headings,
        heading_line_ratio=_ratio(headings, line_count),
        paragraph_count=len(paragraphs),
        paragraph_word_count=sum(paragraph_words),
        average_paragraph_length=_ratio(sum(paragraph_words), len(paragraphs)),
        quotation_mark_count=quotation_marks,
        quotation_density=_ratio(quotation_marks, total_words),
        speech_verb_count=speech_verbs,
        speech_verb_density=_ratio(speech_verbs, total_words),
        delimited_row_count=len(signatures),
        consistent_column_row_count=consistent_rows,
        column_consistency=_ratio(consistent_rows, len(signatures)),
        colon_label_line_count=labels,
        colon_label_ratio=_ratio(labels, line_count),
    )


def _bounded_ratio(value: float, target: float) -> float:
    return min(value / target, 1.0)


def _score(signals: ShapeSignals) -> dict[DocumentShape, float]:
    structured = (
        signals.heading_line_ratio * STRUCTURED_HEADING_WEIGHT
        + signals.colon_label_ratio * STRUCTURED_LABEL_WEIGHT
    )
    prose = (
        _bounded_ratio(
            signals.average_paragraph_length,
            PARAGRAPH_WORD_TARGET,
        )
        * PROSE_PARAGRAPH_WEIGHT
        + _bounded_ratio(signals.quotation_density, QUOTE_DENSITY_TARGET)
        * PROSE_QUOTATION_WEIGHT
        + _bounded_ratio(
            signals.speech_verb_density,
            SPEECH_VERB_DENSITY_TARGET,
        )
        * PROSE_SPEECH_WEIGHT
    )
    table = 0.0
    if signals.delimited_row_count >= MIN_TABLE_ROWS:
        table = (
            signals.column_consistency * TABLE_CONSISTENCY_WEIGHT
            + _bounded_ratio(signals.delimited_row_count, MIN_TABLE_ROWS)
            * TABLE_ROW_WEIGHT
        )
    return {
        DocumentShape.STRUCTURED_RULES: structured,
        DocumentShape.PROSE: prose,
        DocumentShape.REFERENCE_TABLE: table,
    }


def detect_shape(text: str) -> ShapeDetection:
    """Return the highest-scoring generic document route or manual review."""

    signals = _signals(text)
    scores = _score(signals)
    winning_shape = max(scores, key=scores.__getitem__)
    winning_score = scores[winning_shape]
    shape = (
        winning_shape
        if winning_score > SHAPE_SCORE_THRESHOLD
        else DocumentShape.UNSUPPORTED
    )
    return ShapeDetection(
        shape=shape,
        score=winning_score,
        signals=signals,
        scores=MappingProxyType(scores),
    )
