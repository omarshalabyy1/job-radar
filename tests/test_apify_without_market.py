"""The Apify boards (LinkedIn, Indeed, Glassdoor ...) bring your own jobs too, so they are read whether or not the website's
market collection is on: features.market used to switch the whole step off (found 2026-11-14, before turning market off)."""
from job_radar import steps
from tests.test_steps import RolledBack, conn  # noqa: F401  (the rolled-back warehouse connection)


def test_extract_apify_still_reads_its_boards_with_the_market_collection_off(conn, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setitem(steps.FEATURES, "market", False)
    asked = []
    monkeypatch.setattr(steps.sources.apify, "apify_jobs", lambda seen, store: asked.append(seen))
    monkeypatch.setattr(steps.sources.apify, "spend_line", lambda: "apify: spent $0.00 on 0 runs")
    steps.extract_apify(RolledBack(conn))
    out = capsys.readouterr().out
    assert len(asked) == 1 and "off in settings.yaml" not in out and "apify: spent" in out
