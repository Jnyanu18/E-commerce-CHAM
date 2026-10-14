"""CSV engine tests. Fixtures are CLEARLY SYNTHETIC ('syntheticmkt' columns invented for the tests);
they are NOT any real marketplace's report layout."""
import pytest

from connectors.csv_import import (check_marketplace, convert, import_csv, parse_bytes, preview,
                                   validate_mapping, CellError)
from connectors.errors import CsvValidationError
from connectors.store import InMemoryStore

from .conftest import ORG

MAPPING = {
    "key_columns": ["Ref", "Line"],
    "fields": {
        "order_ref": {"column": "Ref", "type": "str", "required": True},
        "sku": {"column": "Item Code", "type": "str", "required": True},
        "qty": {"column": "Units", "type": "int", "required": True},
        "sale": {"column": "Sale Amt", "type": "money", "required": True},
        "fee": {"column": "Fee", "type": "money", "allow_negative": True},
        "placed": {"column": "Placed On", "type": "datetime", "formats": ["%d-%m-%Y %H:%M"]},
        "day": {"column": "Day", "type": "date", "formats": ["%d/%m/%Y"]},
        "state": {"column": "State", "type": "choice", "choices": {"delivered": "DELIVERED", "returned": "RTO"}},
    },
    "redact_columns": ["Buyer Phone"],
}
HDR = "Ref,Line,Item Code,Units,Sale Amt,Fee,Placed On,Day,State,Buyer Phone,Notes\n"
GOOD1 = 'R1,1,SKU-A,2,"₹1,299.50",-45.10,14-10-2026 08:30,14/10/2026,Delivered,9999999999,"multi\nline"\n'
GOOD2 = "R2,1,SKU-B,1,Rs. 499,10,14-10-2026 23:59,15/10/2026,returned,8888888888,\n"
F = HDR + GOOD1 + GOOD2


def test_preview_maps_types_utc_and_paise():
    rep = preview(F, MAPPING, "SyntheticMkt", "orders", ORG)
    assert rep.ok and rep.valid_rows == 2 and rep.total_rows == 2
    m = rep.sample[0]["mapped"]
    assert m["sale"] == 129950 and m["fee"] == -4510 and m["qty"] == 2 and m["state"] == "DELIVERED"
    assert m["placed"] == "2026-10-14T03:00:00Z"        # 08:30 IST -> UTC
    assert rep.sample[1]["mapped"]["placed"] == "2026-10-14T18:29:00Z" and rep.sample[1]["mapped"]["sale"] == 49900
    assert rep.unmapped_columns == ["Buyer Phone", "Notes"]


def test_import_idempotent_redacts_pii_saves_mapping_enqueues():
    s = InMemoryStore()
    r1 = import_csv(F, MAPPING, "syntheticmkt", "orders", ORG, s)
    assert r1.imported.inserted == 2 and r1.transform_enqueued and len(s.jobs) == 1
    p = s.raw[(ORG, "csv_syntheticmkt", "R1|1")]
    assert "Buyer Phone" not in p["row"] and p["row"]["Notes"] == "multi\nline" and p["row"]["Units"] == "2"
    assert p["_import"]["row_number"] == 1
    assert s.get_csv_mapping(ORG, "syntheticmkt", "orders") == MAPPING
    r2 = import_csv(F, MAPPING, "syntheticmkt", "orders", ORG, s)
    assert r2.imported.unchanged == 2 and r2.already_imported == 2 and len(s.jobs) == 1 and len(s.raw) == 2


def test_row_level_errors_block_import_by_default():
    bad = HDR + GOOD1 + "R3,1,,x,-5,abc,2026-10-14,31/02/2026,lost,1,\n"
    s = InMemoryStore()
    rep = import_csv(bad, MAPPING, "syntheticmkt", "orders", ORG, s)
    assert rep.invalid_rows == 1 and rep.valid_rows == 1 and rep.imported is None and not s.raw
    codes = {(e.row, e.column, e.code) for e in rep.errors}
    assert {(2, "Item Code", "required"), (2, "Units", "invalid_int"), (2, "Sale Amt", "negative"),
            (2, "Fee", "invalid_number"), (2, "Placed On", "invalid_date"), (2, "Day", "invalid_date"),
            (2, "State", "invalid_choice")} <= codes


def test_allow_partial_imports_valid_rows_only():
    bad = HDR + GOOD1 + "R3,1,,1,5,1,14-10-2026 08:30,14/10/2026,delivered,1,\n"
    s = InMemoryStore()
    rep = import_csv(bad, MAPPING, "syntheticmkt", "orders", ORG, s, allow_partial=True)
    assert rep.imported.inserted == 1 and len(s.raw) == 1 and rep.invalid_rows == 1


def test_duplicates_identical_ignored_conflicting_blocks():
    s = InMemoryStore()
    rep = import_csv(HDR + GOOD1 + GOOD1, MAPPING, "m", "orders", ORG, s)
    assert rep.duplicate_rows == 1 and rep.imported.inserted == 1
    conflict = GOOD1.replace("SKU-A", "SKU-Z")
    s2 = InMemoryStore()
    rep2 = import_csv(HDR + GOOD1 + conflict, MAPPING, "m", "orders", ORG, s2)
    assert rep2.conflicting_duplicates == 1 and rep2.imported is None and not s2.raw


def test_missing_mapped_column_is_file_level_error():
    rep = import_csv(F.replace("Units", "Qty"), MAPPING, "m", "orders", ORG, InMemoryStore())
    assert not rep.ok and rep.errors[0].row == 0 and rep.errors[0].code == "missing_column" and rep.imported is None


def test_empty_header_only_and_bad_inputs():
    for data in ("", "   \n", b"\xff\xfe\x00bad"):
        with pytest.raises(CsvValidationError):
            parse_bytes(data)
    rep = import_csv(HDR, MAPPING, "m", "orders", ORG, InMemoryStore())
    assert rep.total_rows == 0 and rep.imported is None
    with pytest.raises(CsvValidationError):
        parse_bytes("a,a\n1,2\n")


def test_bom_crlf_blank_lines_and_ragged():
    data = ("﻿" + HDR + GOOD1 + "\r\n\r\n" + GOOD2.replace("\n", "\r\n")).encode()
    rep = preview(data, MAPPING, "m", "orders", ORG)
    assert rep.valid_rows == 2
    ragged = HDR + GOOD1.rstrip("\n") + ",extra,cells\n"
    assert preview(ragged, MAPPING, "m", "orders", ORG).errors[0].code == "ragged_row"


def test_short_row_missing_required_flagged():
    rep = preview(HDR + "R9,1\n", MAPPING, "m", "orders", ORG)
    assert rep.invalid_rows == 1 and any(e.code == "required" for e in rep.errors)


def test_error_list_truncated_but_counts_complete():
    rows = "".join(f"R{i},1,,1,1,1,,,,,\n" for i in range(700))
    rep = preview(HDR + rows, MAPPING, "m", "orders", ORG)
    assert rep.invalid_rows == 700 and len(rep.errors) == 500 and rep.errors_truncated


def test_convert_boundaries():
    money = {"type": "money"}
    assert convert("0.00", money) == 0 and convert("(10.00)", {"type": "money", "allow_negative": True}) == -1000
    assert convert("1,00,000.50", money) == 10000050     # Indian digit grouping
    with pytest.raises(CellError):
        convert("1.005", money)                          # sub-paise precision rejected, not rounded
    with pytest.raises(CellError):
        convert("NaN", money)
    with pytest.raises(CellError):
        convert("2.5", {"type": "int"})
    assert convert("", {"type": "str"}) is None
    assert convert("2026-01-01 00:00:00", {"type": "datetime", "tz": "UTC"}) == "2026-01-01T00:00:00Z"


def test_mapping_and_marketplace_validation():
    for bad in ({}, {"key_columns": [], "fields": {"a": {"column": "A"}}}, {"key_columns": ["A"], "fields": {}},
                {"key_columns": ["A"], "fields": {"a": {"column": "A", "type": "nope"}}},
                {"key_columns": ["A"], "fields": {"a": {"column": "A", "type": "choice"}}}):
        with pytest.raises(CsvValidationError):
            validate_mapping(bad)
    assert check_marketplace(" Blinkit ") == "blinkit"
    with pytest.raises(CsvValidationError):
        check_marketplace("../etc")
