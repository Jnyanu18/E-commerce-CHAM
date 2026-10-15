"""HTTP API over the metric registry. No metric logic lives here: it only validates, authorises and delegates
to `metrics.METRICS[name].callable(...)` and returns `MetricResult.to_dict()` unchanged (docs/CONTRACTS.md s3-4).
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Iterator

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from metrics import METRICS
from metrics.repository import PostgresRepository
from metrics.types import DateRange, MetricError

from .auth import AuthError, dev_bypass, is_member, verify_jwt
from .config import settings

router = APIRouter()


def get_conn() -> Iterator[Any]:
    """Per-request service-role connection (RLS bypassed): org membership is enforced in `authorise`."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        yield conn


def authorise(org_id: str, authorization: str | None, conn) -> None:
    if dev_bypass():
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = verify_jwt(authorization[7:], os.environ.get("SUPABASE_JWT_SECRET", ""))
    except AuthError as e:
        raise HTTPException(e.status, e.detail)
    if not is_member(conn, claims["sub"], org_id):
        raise HTTPException(403, "not a member of this org")  # same answer whether or not the org exists


@router.get("/metrics")
def list_metrics() -> dict:
    return {"metrics": [{"name": s.name, "description": s.description, "unit": s.unit,
                         "allowed_group_by": list(s.allowed_group_by), "allowed_filters": list(s.allowed_filters)}
                        for s in METRICS.values()]}


@router.get("/metrics/{metric}")
def get_metric(metric: str, org_id: str, start: date, end: date, group_by: str | None = None,
               filters: str | None = Query(None, description="JSON-encoded object, e.g. {\"channel\":[\"shopify\"]}"),
               authorization: str | None = Header(None), conn=Depends(get_conn)) -> JSONResponse:
    spec = METRICS.get(metric)
    if spec is None:
        raise HTTPException(404, f"unknown metric {metric!r}")
    try:
        parsed = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise HTTPException(400, "filters must be a JSON-encoded object")
    if parsed is not None and not isinstance(parsed, dict):
        raise HTTPException(400, "filters must be a JSON-encoded object")
    authorise(org_id, authorization, conn)
    try:
        result = spec.callable(org_id, DateRange(start, end), parsed, group_by, repo=PostgresRepository(conn))
    except MetricError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(result.to_dict())
