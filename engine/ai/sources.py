"""Narrow read-only interfaces the AI tools use for everything that is not a canonical metric.

Main wires real implementations (intelligence outputs, system data-quality checks). The AI agent
only depends on these protocols. Each method receives org_id from the executor, never from Claude.

Conventions the tools and validator rely on:
  * money fields end in `_paise` (integer paise); other numerics are counts/ratios/probabilities.
  * a model result carries `status` in {"OK", "WEAK", "INSUFFICIENT_DATA"} (CONTRACTS section 6).
  * intervals are `ci_low_paise` / `ci_high_paise` with `interval_level` (0.8 = 80% interval).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class AnomalySource(Protocol):
    def anomalies(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        """Anomalies detected for the IST week starting `week_start` (STL + robust z-score)."""

    def root_causes(self, org_id: str, anomaly_id: str) -> dict[str, Any] | None:
        """Driver decomposition for one anomaly, or None if unknown for this org."""


class ModelSource(Protocol):
    def model_results(self, org_id: str) -> dict[str, Any]:
        """Latest model_runs summary: {"status": OK|WEAK|INSUFFICIENT_DATA, "reason", "models": [...]}."""


class RecommendationSource(Protocol):
    def recommendations(self, org_id: str, week_start: date) -> list[dict[str, Any]]:
        """Stored recommendation rows (payload, expected_impact_paise, ci_*, prob_beats_current, ...)."""

    def experiment_candidates(self, org_id: str) -> list[dict[str, Any]]:
        """Proposed experiments (hypothesis, design, risk_paise, predicted_effect)."""


class DataQualitySource(Protocol):
    def data_quality(self, org_id: str, start: date, end: date) -> dict[str, Any]:
        """System-level checks: duplicates, missing COGS, unreconciled settlements, stale integrations."""
