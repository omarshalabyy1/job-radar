"""Roles open to intern and junior titles only (settings roles: entry_only)."""
from job_radar import config
from job_radar.config import above_entry, role_of


def test_an_entry_only_role_keeps_intern_trainee_and_junior_titles_and_drops_the_rest(monkeypatch):
    rank = role_of("AI Engineering Intern")
    monkeypatch.setattr(config, "ENTRY_ONLY", {rank})
    for title in ("AI Engineering Intern", "Junior AI Engineer", "Machine Learning Trainee", "Graduate AI Developer"):
        assert not above_entry(role_of(title), title), title
    for title in ("AI Engineer", "Senior LLM Engineer", "Machine Learning Engineer"):
        assert above_entry(role_of(title), title), title
    assert not above_entry(role_of("Data Engineer"), "Data Engineer")  # other roles keep every level
