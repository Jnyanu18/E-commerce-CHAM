"""Load a synthetic brand into Postgres as RAW records plus user-entered master data (org, skus, COGS).
The ledger transform (run separately) turns raw into the canonical ledger. Idempotent: re-loading upserts."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from .raw import to_raw
from .synthetic import SKUS, generate


def load(conn, org_id: str | None = None, *, name: str = "Synthetic Brand", data: dict | None = None,
         **gen_kwargs) -> tuple[str, dict]:
    org_id = org_id or str(uuid.uuid4())
    data = data or generate(**gen_kwargs)
    conn.execute("insert into orgs(id, name) values (%s, %s) on conflict (id) do nothing", (org_id, name))
    for sku, cat, _mrp, _cogs in SKUS:
        conn.execute("insert into skus(org_id, sku, name, category) values (%s,%s,%s,%s) "
                     "on conflict (org_id, sku) do nothing", (org_id, sku, sku, cat))
    for c in data["sku_cogs"]:
        conn.execute("insert into sku_cogs(org_id, sku, effective_from, cogs_paise) values (%s,%s,%s,%s) "
                     "on conflict (org_id, sku, effective_from) do update set cogs_paise = excluded.cogs_paise",
                     (org_id, c["sku"], c["effective_from"], c["cogs_paise"]))
    with conn.cursor() as cur:
        cur.executemany(
            "insert into raw_records(org_id, source, external_id, payload) values (%s,%s,%s,%s) "
            "on conflict (org_id, source, external_id) do update set payload = excluded.payload",
            [(org_id, r["source"], r["external_id"], Jsonb(r["payload"])) for r in to_raw(data)])
    return org_id, data
