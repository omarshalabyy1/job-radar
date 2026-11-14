"""Sources switched off in settings.yaml are not read: `sources_off` in every collect (a site that refuses every client),
`sources_off_github` on GitHub Actions only (a site that answers the laptop but refuses GitHub's servers)."""
from job_radar import steps


def run(monkeypatch, github: bool) -> tuple[list[str], list[str]]:
    monkeypatch.setitem(steps.SETTINGS, "sources_off", ["dead_site"])
    monkeypatch.setitem(steps.SETTINGS, "sources_off_github", ["laptop_only"])
    monkeypatch.setenv("GITHUB_ACTIONS", "true") if github else monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(steps, "online", lambda: True)
    read, loaded = [], []
    monkeypatch.setattr(steps, "load", lambda conn, name, rows: loaded.append(name))

    def source(name):
        def f():
            read.append(name)
            return []
        f.__name__ = name
        return f

    steps.extract(None, source("dead_site"), source("laptop_only"), source("open_site"))
    return read, loaded


def test_a_source_off_everywhere_is_never_read(monkeypatch, capsys):
    read, loaded = run(monkeypatch, github=False)
    assert read == loaded == ["laptop_only", "open_site"]
    assert "dead_site: off in settings.yaml, not read" in capsys.readouterr().out


def test_a_source_off_on_github_is_read_by_the_laptop_only(monkeypatch, capsys):
    read, loaded = run(monkeypatch, github=True)
    assert read == loaded == ["open_site"]
    out = capsys.readouterr().out
    assert "laptop_only: off in settings.yaml, not read" in out and "WARNING" not in out
