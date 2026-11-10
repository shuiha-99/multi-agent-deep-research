"""Human-checkable business fixture; not evidence of real-world effectiveness."""
import csv
import json
from pathlib import Path

import pytest


SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_sample_order_grain_and_expected_business_numbers():
    rows = list(csv.DictReader((SAMPLES / "orders.csv").open(encoding="utf-8")))
    assert len(rows) == 19
    assert len({row["order_id"] for row in rows}) == 16
    assert sum(int(row["quantity"]) for row in rows) == 60
    assert sum(float(row["paid_amount"]) for row in rows) == 3900
    assert sum(float(row["refund_amount"]) for row in rows) == 180
    assert sum(float(row["paid_amount"]) - float(row["refund_amount"]) for row in rows) == 3720
    sales = [sum(float(row["paid_amount"]) for row in rows if row["order_date"].startswith(month)) for month in ["2026-01", "2026-02", "2026-03"]]
    assert sales == [900, 1200, 1800]
    assert sales[1] / sales[0] - 1 == pytest.approx(1 / 3)
    assert sales[2] / sales[1] - 1 == 0.5


def test_offline_sources_cannot_be_mistaken_for_live_urls():
    sources = json.loads((SAMPLES / "web_sources.json").read_text(encoding="utf-8"))
    assert len(sources) >= 2
    for source in sources:
        assert source["url"].startswith("sample://")
        assert source["metadata"]["is_demo"] is True
        assert "虚构" in source["content"]
        assert "<table>" in source["html"]
        assert (SAMPLES / source["path"]).is_file()
