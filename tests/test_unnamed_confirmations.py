"""A confirmation whose subject names no job title or no company ("Thank you for applying at Kasha") is read in full:
its text says "the Data Scientist position". The pattern's own kind and company stand; Claude fills only what is missing."""
from types import SimpleNamespace

from job_radar import applications


def test_only_a_confirmation_missing_its_title_or_company_is_read_in_full():
    assert applications.unnamed({"kind": "applied", "company": "Kasha", "title": None})
    assert applications.unnamed({"kind": "applied", "company": None, "title": "Data Analyst"})
    assert not applications.unnamed({"kind": "applied", "company": "Kasha", "title": "Data Scientist"})
    assert not applications.unnamed({"kind": "rejected", "company": "Kasha", "title": None})  # a reply joins its application


def test_what_the_subject_told_stands_and_the_text_fills_the_rest(monkeypatch):
    answer = SimpleNamespace(parsed_output=SimpleNamespace(kind="other", company="BambooHR", title="Data Scientist."))
    monkeypatch.setattr(applications.claude, "client", lambda: SimpleNamespace(messages=SimpleNamespace(parse=lambda **_: answer)))
    mail = {"message_id": "<1>", "sender": "Kasha <notifications@app.bamboohr.com>", "to": "me@gmail.com",
            "subject": "Thank you for applying at Kasha", "text": "your interest in the Data Scientist position"}
    named = applications.ask_claude([{**mail, "kind": "applied", "company": "Kasha", "title": None}], {"model": "m"})[0]
    assert (named["kind"], named["company"], named["title"]) == ("applied", "Kasha", "Data Scientist")
    plain = applications.ask_claude([mail], {"model": "m"})[0]  # no pattern fitted: all three are Claude's
    assert (plain["kind"], plain["company"], plain["title"]) == ("other", "BambooHR", "Data Scientist")
