"""Generic CSV import engine for marketplace / quick-commerce reports.

IMPORTANT: this is a *generic* engine. It ships NO built-in column layouts for Amazon, Flipkart, Blinkit,
Zepto or Instamart: no sample report from any of them was available, and formats are never guessed
(CLAUDE.md). A user supplies (or a future preset file derived from a real sample provides) a mapping.
Samples needed are listed in NOTES.md.

Flow: parse_bytes -> preview (mapping applied, row-level validation report, duplicate detection) ->
import_csv (idempotent upsert into raw_records with source `csv_<marketplace>`; mapping saved in
csv_mappings). Row payload = {"row": <original strings verbatim>, "mapped": <typed canonical values>,
"_import": {...provenance}}. Money is integer paise, timestamps UTC ISO-8601.

Mapping spec (JSON, stored in csv_mappings.mapping):
{
  "key_columns": ["Order ID", "SKU"],           # composite external_id (joined with '|')
  "fields": {
    "<canonical_name>": {"column": "Header", "type": "str|int|decimal|money|date|datetime|choice",
                          "required": true, "allow_negative": false,
                          "formats": ["%d-%m-%Y"],      # date/datetime; explicit, never guessed
                          "tz": "IST|UTC",              # datetime without offset; default IST
                          "choices": {"delivered": "DELIVERED"}}   # case-insensitive normalisation
  },
  "delimiter": ",",                             # optional
  "redact_columns": ["Customer Phone"]          # optional: dropped from the stored verbatim row (PII)
}
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.money import rupees_to_paise

from .errors import CsvValidationError
from .store import RawRecord, Store, UpsertResult

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000
MAX_LISTED_ERRORS = 500
IST = timezone(timedelta(hours=5, minutes=30))
FIELD_TYPES = {"str", "int", "decimal", "money", "date", "datetime", "choice"}
MARKETPLACE_RE = re.compile(r"^[a-z0-9]{1,32}(_[a-z0-9]{1,32})*$")
ISO_DATE = ["%Y-%m-%d"]
ISO_DATETIME = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"]


@dataclass
class RowError:
    row: int            # 1-based data row number (header excluded); 0 = file level
    column: str | None
    code: str
    message: str
    value: str | None = None

    def as_dict(self):
        return self.__dict__.copy()


@dataclass
class CsvReport:
    marketplace: str
    report_type: str
    file_sha256: str
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    duplicate_rows: int = 0             # same key inside the file, identical content
    conflicting_duplicates: int = 0     # same key inside the file, different content
    already_imported: int = 0           # keys already present in raw_records (preview only)
    errors: list[RowError] = field(default_factory=list)
    errors_truncated: bool = False
    unmapped_columns: list[str] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)
    imported: UpsertResult | None = None
    transform_enqueued: bool = False

    def add_error(self, e: RowError):
        if len(self.errors) < MAX_LISTED_ERRORS:
            self.errors.append(e)
        else:
            self.errors_truncated = True

    @property
    def ok(self) -> bool:
        return self.invalid_rows == 0 and self.conflicting_duplicates == 0 and not any(e.row == 0 for e in self.errors)


# ---------------------------------------------------------------- mapping
def validate_mapping(mapping: dict) -> None:
    if not isinstance(mapping, dict):
        raise CsvValidationError("mapping must be an object")
    keys, fields = mapping.get("key_columns"), mapping.get("fields")
    if not keys or not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys):
        raise CsvValidationError("mapping.key_columns must be a non-empty list of column names")
    if not fields or not isinstance(fields, dict):
        raise CsvValidationError("mapping.fields must be a non-empty object")
    for name, spec in fields.items():
        if not isinstance(spec, dict) or not spec.get("column"):
            raise CsvValidationError(f"field '{name}' needs a 'column'")
        if spec.get("type", "str") not in FIELD_TYPES:
            raise CsvValidationError(f"field '{name}' has unknown type '{spec.get('type')}'")
        if spec.get("type") == "choice" and not spec.get("choices"):
            raise CsvValidationError(f"field '{name}' of type choice needs 'choices'")
        if spec.get("tz", "IST") not in ("IST", "UTC"):
            raise CsvValidationError(f"field '{name}' tz must be IST or UTC")


def check_marketplace(marketplace: str) -> str:
    m = marketplace.strip().lower()
    if not MARKETPLACE_RE.match(m):
        raise CsvValidationError("marketplace must be lowercase letters/digits/underscore")
    return m


# ---------------------------------------------------------------- parsing
def parse_bytes(data: bytes | str, delimiter: str = ",") -> tuple[list[str], list[dict]]:
    """Return (headers, rows-as-dicts of original strings). Handles BOM, quoted newlines, CRLF."""
    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise CsvValidationError(f"file larger than {MAX_BYTES} bytes")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise CsvValidationError("file is not valid UTF-8 (re-export as UTF-8 CSV)") from None
    else:
        text = data.lstrip("﻿")
    if not text.strip():
        raise CsvValidationError("file is empty")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        raise CsvValidationError("file is empty") from None
    if not any(headers):
        raise CsvValidationError("header row is blank")
    dups = sorted({h for h in headers if h and headers.count(h) > 1})
    if dups:
        raise CsvValidationError(f"duplicate column headers: {dups}")
    rows = []
    for raw in reader:
        if not any(c.strip() for c in raw):
            continue  # blank line
        if len(rows) >= MAX_ROWS:
            raise CsvValidationError(f"more than {MAX_ROWS} rows; split the file")
        d = {h: (raw[i] if i < len(raw) else None) for i, h in enumerate(headers)}
        d["__extra__"] = raw[len(headers):] if len(raw) > len(headers) else None
        rows.append(d)
    return headers, rows


# ---------------------------------------------------------------- cell conversion
class CellError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


_MONEY_JUNK = re.compile(r"(?i)^\s*(rs\.?|inr|₹)\s*|[,\s₹]")


def convert(value: str | None, spec: dict):
    t = spec.get("type", "str")
    v = None if value is None else value.strip()
    if v is None or v == "":
        if spec.get("required", False):
            raise CellError("required", "value is required")
        return None
    if t == "str":
        return v
    if t == "int":
        try:
            n = int(Decimal(v.replace(",", "")))
            if Decimal(v.replace(",", "")) != n:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise CellError("invalid_int", "not a whole number") from None
        if n < 0 and not spec.get("allow_negative", False):
            raise CellError("negative", "negative value not allowed")
        return n
    if t in ("decimal", "money"):
        s = _MONEY_JUNK.sub("", v) if t == "money" else v.replace(",", "")
        neg = s.startswith("(") and s.endswith(")")   # accounting negative (1,234.00)
        s = s.strip("()")
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise CellError("invalid_number", "not a number") from None
        if not d.is_finite():
            raise CellError("invalid_number", "not a finite number")
        d = -d if neg else d
        if d < 0 and not spec.get("allow_negative", False):
            raise CellError("negative", "negative value not allowed")
        if t == "decimal":
            return str(d)
        if d != d.quantize(Decimal("0.01")):
            raise CellError("precision", "more than 2 decimal places for a rupee amount")
        return rupees_to_paise(d)
    if t == "choice":
        m = {k.lower(): val for k, val in spec["choices"].items()}
        if v.lower() not in m:
            raise CellError("invalid_choice", f"not one of {sorted(spec['choices'])}")
        return m[v.lower()]
    if t in ("date", "datetime"):
        fmts = spec.get("formats") or (ISO_DATE if t == "date" else ISO_DATETIME)
        for f in fmts:
            try:
                dt = datetime.strptime(v, f)
            except ValueError:
                continue
            if t == "date":
                return dt.date().isoformat()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc if spec.get("tz") == "UTC" else IST)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        raise CellError("invalid_date", f"does not match formats {fmts}")
    raise CellError("bad_spec", f"unsupported type {t}")  # pragma: no cover


# ---------------------------------------------------------------- engine
def _check_columns(headers: list[str], mapping: dict, report: CsvReport) -> bool:
    needed = list(mapping["key_columns"]) + [s["column"] for s in mapping["fields"].values()]
    missing = sorted({c for c in needed if c not in headers})
    for c in missing:
        report.add_error(RowError(0, c, "missing_column", f"mapped column '{c}' not found in file"))
    used = set(needed)
    report.unmapped_columns = [h for h in headers if h and h not in used]
    return not missing


def process(data: bytes | str, mapping: dict, marketplace: str, report_type: str, org_id: str,
            store: Store | None = None, sample_size: int = 10) -> tuple[CsvReport, list[RawRecord]]:
    validate_mapping(mapping)
    marketplace = check_marketplace(marketplace)
    source = f"csv_{marketplace}"
    raw_bytes = data if isinstance(data, bytes) else data.encode()
    report = CsvReport(marketplace, report_type, hashlib.sha256(raw_bytes).hexdigest())
    headers, rows = parse_bytes(data, mapping.get("delimiter", ","))
    report.total_rows = len(rows)
    if not _check_columns(headers, mapping, report):
        report.invalid_rows = len(rows)
        return report, []
    recs: dict[str, RawRecord] = {}
    for n, row in enumerate(rows, start=1):
        errs: list[RowError] = []
        if row.get("__extra__"):
            errs.append(RowError(n, None, "ragged_row", "row has more cells than headers"))
        key_parts = [(row.get(c) or "").strip() for c in mapping["key_columns"]]
        for c, kp in zip(mapping["key_columns"], key_parts):
            if not kp:
                errs.append(RowError(n, c, "missing_key", "key column is empty"))
        mapped = {}
        for name, spec in mapping["fields"].items():
            try:
                mapped[name] = convert(row.get(spec["column"]), spec)
            except CellError as e:
                errs.append(RowError(n, spec["column"], e.code, str(e), (row.get(spec["column"]) or "")[:100]))
        if errs:
            report.invalid_rows += 1
            for e in errs:
                report.add_error(e)
            continue
        ext_id = "|".join(key_parts)
        redact = set(mapping.get("redact_columns") or [])
        original = {h: row.get(h) for h in headers if h not in redact}
        payload = {"row": original, "mapped": mapped,
                   "_import": {"file_sha256": report.file_sha256, "row_number": n, "report_type": report_type}}
        if ext_id in recs:
            prev = recs[ext_id].payload
            if prev["row"] == original:
                report.duplicate_rows += 1
                report.add_error(RowError(n, None, "duplicate_row", f"identical duplicate of key '{ext_id}' (ignored)"))
            else:
                report.conflicting_duplicates += 1
                report.add_error(RowError(n, None, "conflicting_duplicate",
                                          f"key '{ext_id}' appears again with different values"))
            continue
        recs[ext_id] = RawRecord(org_id, source, ext_id, payload)
        report.valid_rows += 1
        if len(report.sample) < sample_size:
            report.sample.append({"key": ext_id, "mapped": mapped})
    if store is not None and recs:
        report.already_imported = len(store.existing_external_ids(org_id, source, list(recs)))
    return report, list(recs.values())


def preview(data, mapping, marketplace, report_type, org_id, store: Store | None = None) -> CsvReport:
    return process(data, mapping, marketplace, report_type, org_id, store)[0]


def import_csv(data, mapping, marketplace, report_type, org_id, store: Store, *,
               allow_partial: bool = False, save_mapping: bool = True, integration_id: str | None = None) -> CsvReport:
    """Idempotent import. By default (allow_partial=False) nothing is written if ANY row is invalid or
    conflicting, so a file is all-or-nothing and a corrected re-upload simply upserts."""
    report, recs = process(data, mapping, marketplace, report_type, org_id, store)
    blocking = any(e.row == 0 for e in report.errors) or report.conflicting_duplicates
    if blocking or (report.invalid_rows and not allow_partial) or not recs:
        return report
    source = f"csv_{check_marketplace(marketplace)}"
    report.imported = store.upsert_raw(recs)
    if save_mapping:
        store.save_csv_mapping(org_id, check_marketplace(marketplace), report_type, mapping)
    if integration_id:
        store.update_integration(integration_id, status="active", last_synced_at=datetime.now(timezone.utc),
                                 clear_error=True)
    if report.imported.changed:
        store.enqueue_transform(org_id, {"trigger": "connector", "provider": "csv", "source": source,
                                         "file_sha256": report.file_sha256})
        report.transform_enqueued = True
    return report
