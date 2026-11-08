"""scripts/heartbeat.py: a run older than 26 hours (or none, or an unreachable warehouse) must alert."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import heartbeat  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def test_stale_boundary_is_26_hours():
    assert not heartbeat.stale(NOW - timedelta(hours=25, minutes=59), NOW)
    assert heartbeat.stale(NOW - timedelta(hours=26, minutes=1), NOW)


def test_no_run_at_all_is_stale():
    assert heartbeat.stale(None, NOW)


def test_unreachable_warehouse_alerts(monkeypatch):
    pushed = []
    monkeypatch.setattr(heartbeat, "last_success", lambda: (_ for _ in ()).throw(OSError("connection refused")))
    monkeypatch.setattr(heartbeat.digest, "push", lambda title, message, url=None: pushed.append(message) or True)
    assert heartbeat.main() == 1
    assert "unreachable" in pushed[0]
