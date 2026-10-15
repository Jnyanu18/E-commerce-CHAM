"""PsycopgIntelRepo against a REAL Postgres (migrations applied). Skipped unless DATABASE_URL is set and reachable, e.g.
DATABASE_URL=postgresql://postgres@localhost:54399/intelligence_agent  (create via scripts/db_reset.sh intelligence_agent)."""
import os
from datetime import timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

from intelligence.experiments import design_geo_holdout, measure_did, persist_experiment, record_measurement  # noqa: E402
from intelligence.optimiser import Constraints, optimise, persist_recommendation  # noqa: E402
from intelligence.persistence import PsycopgIntelRepo, persist_mmm_result  # noqa: E402
from models import run_mmm  # noqa: E402
from tests.intelligence.test_experiments import W0, _did_data, cities  # noqa: E402
from tests.models.synth import make  # noqa: E402
from tests.models.test_mmm import _planted_draws  # noqa: E402

ORG = "00000000-0000-0000-0000-00000000a1a1"


@pytest.fixture()
def conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    try:
        c = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("database unreachable")
    c.execute("delete from orgs where id=%s", (ORG,))
    c.execute("insert into orgs (id, name) values (%s, 'intel-test')", (ORG,))
    yield c
    c.execute("delete from orgs where id=%s", (ORG,))
    c.close()


def test_mmm_experiment_recommendation_roundtrip(conn):
    repo = PsycopgIntelRepo(conn)
    ok = run_mmm(make(78), fit=lambda d, c, cfg, cal: _planted_draws())
    rid = persist_mmm_result(repo, ORG, ok)
    row = conn.execute("select sufficient_data, model_type, diagnostics->'verdict'->>'status', data_start from model_runs where id=%s", (rid,)).fetchone()
    assert row[0] is True and row[1] == "mmm_v0" and row[2] == "ok" and row[3] is not None
    cur = conn.execute("select channel, array_length(spend_grid_paise,1), pg_typeof(spend_grid_paise)::text, lower_paise[2] <= expected_revenue_paise[2] "
                       "from channel_response_curves where model_run_id=%s order by channel", (rid,)).fetchall()
    assert [c[0] for c in cur] == ["google", "meta"] and all(c[1] > 5 and c[2] == "bigint[]" and c[3] for c in cur)

    bad = persist_mmm_result(repo, ORG, run_mmm(make(30)))
    r2 = conn.execute("select sufficient_data, diagnostics->'verdict'->>'reason', diagnostics->'verdict'->>'fallback' from model_runs where id=%s", (bad,)).fetchone()
    assert r2[0] is False and "30 weeks" in r2[1] and r2[2] == "descriptive"
    assert conn.execute("select count(*) from channel_response_curves where model_run_id=%s", (bad,)).fetchone()[0] == 0
    with pytest.raises(ValueError):
        repo.save_curves(ORG, bad, ok["curves"])

    d = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"])
    eid = persist_experiment(repo, ORG, d)
    e = conn.execute("select treatment_cities, control_cities, status, risk_paise, start_date, predicted_effect->>'note' from experiments where id=%s", (eid,)).fetchone()
    assert e[0] == ["Pune"] and e[1] == ["Surat"] and e[2] == "proposed" and e[3] > 0 and e[4] == d["start_date"]
    record_measurement(repo, ORG, eid, measure_did(*_did_data()))
    m = conn.execute("select status, (measured_effect->>'point')::bigint from experiments where id=%s", (eid,)).fetchone()
    assert m[0] == "completed" and m[1] < 0

    rec = optimise(ok, {"meta": 1_000_000, "google": 1_800_000}, Constraints())
    recid = persist_recommendation(repo, ORG, rec, rid)
    r = conn.execute("select type, expected_impact_paise, ci_low_paise <= expected_impact_paise, prob_beats_current between 0 and 1, model_run_id=%s "
                     "from recommendations where id=%s", (rid, recid)).fetchone()
    assert r[0] == "budget_shift" and r[1] > 0 and r[2] and r[3] and r[4]
    exp_rec = optimise(run_mmm(make(30)), {"meta": 1_000_000})
    pid = persist_recommendation(repo, ORG, exp_rec, bad, eid)
    t = conn.execute("select type, expected_impact_paise, experiment_id from recommendations where id=%s", (pid,)).fetchone()
    assert t[0] == "run_experiment" and t[1] is None and str(t[2]) == eid


def test_other_tenant_update_blocked(conn):
    repo = PsycopgIntelRepo(conn)
    d = design_geo_holdout(cities(), "meta", W0 + timedelta(weeks=24), treatment_candidates=["Pune"])
    eid = persist_experiment(repo, ORG, d)
    with pytest.raises(KeyError):
        repo.update_experiment_measured("00000000-0000-0000-0000-00000000b2b2", eid, {"point": 1})
