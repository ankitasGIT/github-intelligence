import uuid
from datetime import datetime
from typing import Any

from analytics import Confidence
from pydantic import BaseModel


class DefinitionRead(BaseModel):
    key: str
    name: str
    definition: str
    formula: str
    unit: str
    # Section anchor in docs/metric-definitions.md.
    doc_anchor: str


class AffectedItemRead(BaseModel):
    kind: str  # "pull_request" | "issue" | "milestone"
    number: int
    # From the stored source row; null if it has since been deleted from the database.
    title: str | None
    url: str
    detail: dict[str, Any]


class RecommendationRead(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    action: str
    rank: int
    title: str
    detail: str
    item_numbers: list[int]


class FindingRead(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    key: str
    rule: str
    rule_version: str
    rank: int
    severity: str  # "high" | "medium" | "low"
    confidence: Confidence
    title: str
    summary: str
    evidence: dict[str, Any]
    affected_items: list[AffectedItemRead]
    metric_refs: list[str]
    recommendations: list[RecommendationRead]
    analysis_at: datetime


class FindingDetailRead(FindingRead):
    """A finding plus the definitions needed to read it without leaving the page."""

    rule_definition: DefinitionRead
    metric_definitions: list[DefinitionRead]


class DefinitionsRead(BaseModel):
    metrics: list[DefinitionRead]
    rules: list[DefinitionRead]
