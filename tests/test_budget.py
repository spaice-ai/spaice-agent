"""Tests for spaice_agent.budget."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from spaice_agent.budget import BudgetExceeded, DailyCounter, Ledger


# ============================================================
# Ledger
# ============================================================

class TestLedger:
    def test_creates_file_and_parent_dirs(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        assert led.ledger_path.exists()
        assert led.ledger_path.parent.is_dir()

    def test_record_appends_one_line(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        led.record({"tool": "search", "cost_usd": 0.01, "model": "exa"})
        lines = led.ledger_path.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "search"
        assert entry["cost_usd"] == 0.01
        assert "ts" in entry  # auto-injected

    def test_record_preserves_ts(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        fixed = "2026-05-03T12:00:00+10:00"
        led.record({"ts": fixed, "cost_usd": 0.02})
        entry = json.loads(led.ledger_path.read_text().splitlines()[0])
        assert entry["ts"] == fixed

    def test_record_does_not_mutate_input(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        e = {"cost_usd": 0.01}
        led.record(e)
        assert "ts" not in e  # caller's dict unchanged

    def test_multiple_records(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        for i in range(5):
            led.record({"cost_usd": 0.01 * i})
        assert len(led.ledger_path.read_text().splitlines()) == 5

    def test_read_since_filters_by_timestamp(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        old = "2026-01-01T00:00:00+10:00"
        new = datetime.now().astimezone().isoformat(timespec="seconds")
        led.record({"ts": old, "cost_usd": 0.01})
        led.record({"ts": new, "cost_usd": 0.02})
        cutoff = datetime(2026, 3, 1, tzinfo=timezone(timedelta(hours=10)))
        results = led.read_since(cutoff)
        assert len(results) == 1
        assert results[0]["cost_usd"] == 0.02

    def test_read_since_skips_malformed(self, tmp_path, caplog):
        led = Ledger("jarvis", base_dir=tmp_path)
        with led.ledger_path.open("a") as f:
            f.write("this is not json\n")
            f.write('{"ts": "2026-05-03T12:00:00+10:00", "cost_usd": 0.05}\n')
            f.write('{"ts": "garbage-timestamp", "cost_usd": 0.10}\n')
        cutoff = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=10)))
        results = led.read_since(cutoff)
        # Only one valid entry
        assert len(results) == 1
        assert results[0]["cost_usd"] == 0.05

    def test_total_cost_usd(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        for i in range(3):
            led.record({"cost_usd": 0.10})
        cutoff = datetime.now().astimezone() - timedelta(hours=1)
        total = led.total_cost_usd(cutoff)
        assert abs(total - 0.30) < 1e-9

    def test_read_empty_ledger(self, tmp_path):
        led = Ledger("jarvis", base_dir=tmp_path)
        cutoff = datetime.now().astimezone() - timedelta(hours=1)
        assert led.read_since(cutoff) == []
        assert led.total_cost_usd(cutoff) == 0.0

    def test_concurrent_writes_no_corruption(self, tmp_path):
        """Simulate 10 threads each writing 20 records. All 200 must land intact."""
        led = Ledger("jarvis", base_dir=tmp_path)

        def worker(n):
            for i in range(20):
                led.record({"thread": n, "i": i, "cost_usd": 0.001})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = led.ledger_path.read_text().splitlines()
        assert len(lines) == 200
        parsed = [json.loads(l) for l in lines]
        seen = {(e["thread"], e["i"]) for e in parsed}
        assert len(seen) == 200  # every write distinct, nothing lost or duplicated

    def test_per_agent_isolation(self, tmp_path):
        a = Ledger("jarvis", base_dir=tmp_path)
        b = Ledger("aurora", base_dir=tmp_path)
        a.record({"cost_usd": 0.1})
        b.record({"cost_usd": 0.2})
        assert a.ledger_path != b.ledger_path
        assert "jarvis" in str(a.ledger_path)
        assert "aurora" in str(b.ledger_path)
        # Each only has its own entry
        assert len(a.ledger_path.read_text().splitlines()) == 1
        assert len(b.ledger_path.read_text().splitlines()) == 1


# ============================================================
# DailyCounter
# ============================================================

class TestDailyCounter:
    def test_initial_count_zero(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        assert dc.current_count("search") == 0

    def test_increment_returns_new_count(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        assert dc.increment("search") == 1
        assert dc.increment("search") == 2
        assert dc.increment("search") == 3

    def test_increment_persists(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        dc.increment("consensus")
        dc.increment("consensus")
        # Reinstantiate and read
        dc2 = DailyCounter("jarvis", base_dir=tmp_path)
        assert dc2.current_count("consensus") == 2

    def test_independent_tool_counters(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        dc.increment("search")
        dc.increment("search")
        dc.increment("consensus")
        assert dc.current_count("search") == 2
        assert dc.current_count("consensus") == 1

    def test_can_fire_below_cap(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        assert dc.can_fire("search", cap=10)
        dc.increment("search")
        assert dc.can_fire("search", cap=10)

    def test_can_fire_at_cap(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        for _ in range(10):
            dc.increment("search")
        assert not dc.can_fire("search", cap=10)

    def test_can_fire_cap_zero(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        assert not dc.can_fire("search", cap=0)

    def test_can_fire_cap_negative(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        assert dc.can_fire("search", cap=-1)  # unlimited

    def test_rollover(self, tmp_path):
        """If stored date is yesterday, counters reset."""
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        # Force a yesterday-state
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        stale_state = {"date": yesterday, "counters": {"search": 999, "consensus": 42}}
        dc.state_path.write_text(json.dumps(stale_state))
        # Read should trigger rollover
        assert dc.current_count("search") == 0
        assert dc.current_count("consensus") == 0
        # And can_fire respects fresh count
        assert dc.can_fire("search", cap=10)

    def test_rollover_during_increment(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        stale_state = {"date": yesterday, "counters": {"search": 50}}
        dc.state_path.write_text(json.dumps(stale_state))
        # Increment should roll over AND count this one
        assert dc.increment("search") == 1

    def test_per_agent_isolation(self, tmp_path):
        a = DailyCounter("jarvis", base_dir=tmp_path)
        b = DailyCounter("aurora", base_dir=tmp_path)
        a.increment("search")
        a.increment("search")
        b.increment("search")
        assert a.current_count("search") == 2
        assert b.current_count("search") == 1

    def test_corrupt_state_file_resets(self, tmp_path, caplog):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        dc.state_path.write_text("this is not json")
        assert dc.current_count("search") == 0
        # Increment still works after reset
        assert dc.increment("search") == 1

    def test_empty_state_file_handled(self, tmp_path):
        dc = DailyCounter("jarvis", base_dir=tmp_path)
        dc.state_path.write_text("")
        assert dc.current_count("search") == 0
        assert dc.increment("search") == 1

    def test_concurrent_increments_no_lost_updates(self, tmp_path):
        """20 threads × 50 increments each = 1000 total. No lost updates."""
        dc = DailyCounter("jarvis", base_dir=tmp_path)

        def worker():
            for _ in range(50):
                dc.increment("search")

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert dc.current_count("search") == 1000


class TestBudgetExceeded:
    def test_is_runtime_error(self):
        with pytest.raises(RuntimeError):
            raise BudgetExceeded("over $40/day")

    def test_importable(self):
        from spaice_agent.budget import BudgetExceeded as BE
        assert BE is BudgetExceeded
