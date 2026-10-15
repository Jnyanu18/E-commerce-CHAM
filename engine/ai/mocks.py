"""Deterministic in-memory implementations of the source protocols (tests, demos, until main wires real ones).

Data is keyed by org_id so isolation can be tested; an org with no entry returns "nothing".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class StaticAnomalySource:
    by_org: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    causes_by_org: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def anomalies(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        return [a for a in self.by_org.get(org_id, []) if a.get("week_start") in (None, week_start.isoformat())]

    def root_causes(self, org_id: str, anomaly_id: str):
        return self.causes_by_org.get(org_id, {}).get(anomaly_id)


@dataclass
class StaticModelSource:
    by_org: dict[str, dict[str, Any]] = field(default_factory=dict)

    def model_results(self, org_id: str) -> dict[str, Any]:
        return self.by_org.get(org_id, {"status": "INSUFFICIENT_DATA", "reason": "no model run for this org", "models": []})


@dataclass
class StaticRecommendationSource:
    recs_by_org: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    experiments_by_org: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def recommendations(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        return list(self.recs_by_org.get(org_id, []))

    def experiment_candidates(self, org_id: str) -> list[dict[str, Any]]:
        return list(self.experiments_by_org.get(org_id, []))


@dataclass
class StaticDataQualitySource:
    by_org: dict[str, dict[str, Any]] = field(default_factory=dict)

    def data_quality(self, org_id: str, start: date, end: date) -> dict[str, Any]:
        return self.by_org.get(org_id, {"status": "ok", "issues": []})
