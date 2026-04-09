"""
Analytics engine for the Chamber Sentinel CIED Telemetry Simulator (Module 4).

Provides side-by-side comparison metrics between the current
(persist-by-default) and Chambers (burn-by-default) architectures.
"""

from src.analytics.adverse_event_impact import AdverseEventImpactAnalyzer
from src.analytics.attack_surface import AttackSurfaceCalculator
from src.analytics.clinical_availability import ClinicalAvailabilityMonitor
from src.analytics.comparator import ArchitectureComparator
from src.analytics.persistence_tracker import PersistenceTracker
from src.analytics.regulatory_compliance import RegulatoryComplianceScorer

__all__ = [
    "AdverseEventImpactAnalyzer",
    "AttackSurfaceCalculator",
    "ArchitectureComparator",
    "ClinicalAvailabilityMonitor",
    "PersistenceTracker",
    "RegulatoryComplianceScorer",
]
