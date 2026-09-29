"""Evidence layer: sourced, tiered and quote-verified evidence products."""

from .adjudication import (
    VERDICT_ACCEPTED,
    VERDICT_CONFLICT_PENDING,
    VERDICT_CROSS_VALIDATED,
    VERDICT_INSUFFICIENT_PENDING,
    AdjudicationRules,
    Adjudicator,
    JudgementCache,
)
from .layer import EvidenceLayer
from .models import (
    PENDING_STATUSES,
    SCHEMA_VERSION,
    SCHEMA_VERSION_ADJUDICATED,
    VERDICT_STATUSES,
    EvidenceArtifact,
    EvidenceGroup,
    EvidenceItem,
    RejectedItem,
    SourceProfile,
)
from .tiering import TierClassifier, TierRule, TierRules
from ..utils.domains import normalize_domain

__all__ = [
    "EvidenceLayer",
    "EvidenceArtifact",
    "EvidenceGroup",
    "EvidenceItem",
    "RejectedItem",
    "SourceProfile",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_ADJUDICATED",
    "VERDICT_STATUSES",
    "PENDING_STATUSES",
    "VERDICT_ACCEPTED",
    "VERDICT_CROSS_VALIDATED",
    "VERDICT_CONFLICT_PENDING",
    "VERDICT_INSUFFICIENT_PENDING",
    "AdjudicationRules",
    "Adjudicator",
    "JudgementCache",
    "TierClassifier",
    "TierRule",
    "TierRules",
    "normalize_domain",
]
