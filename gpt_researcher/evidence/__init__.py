"""Evidence layer: sourced, tiered and quote-verified evidence products."""

from .layer import EvidenceLayer
from .models import (
    SCHEMA_VERSION,
    EvidenceArtifact,
    EvidenceItem,
    RejectedItem,
    SourceProfile,
)
from .tiering import TierClassifier, TierRule, TierRules
from ..utils.domains import normalize_domain

__all__ = [
    "EvidenceLayer",
    "EvidenceArtifact",
    "EvidenceItem",
    "RejectedItem",
    "SourceProfile",
    "SCHEMA_VERSION",
    "TierClassifier",
    "TierRule",
    "TierRules",
    "normalize_domain",
]
