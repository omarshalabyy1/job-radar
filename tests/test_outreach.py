r"""outreach_drafts: a cold email (never sent) to a startup's founder for each of its roles scored 7+, only when
data/contacts.csv has their published email. Without the network or the warehouse:

    .venv\Scripts\python -m pytest tests\test_outreach.py
"""

from types import SimpleNamespace

import anthropic

from job_radar import outreach
from job_radar.applications import FollowUp

CV = "Alex Example. Built a pipeline of 3 sources with Airflow."
JOBS = [  # job_id, title, company, description, score
    (1, "Data Engineer", "Nile AI", "Airflow and Spark pipelines.", 8),
    (2, "Analytics Engineer", "Big Bank", "dbt.", 9),          # not a startup
    (3, "Data Engineer", "Delta Data", "Airflow.", 9),         # a startup with no contact email
]


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, applied=()):
        self.applied = list(applied)  # the jobs you marked applied: (job_id, title, company, description, score)

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if "s.status = 'applied'" in sql:
            return Result([j for j in self.applied if j[4] >= params[0]])
        if "FROM mart.job_status" in sql:
            return Result(JOBS)
        if "core.cv" in sql:
            return Result([(CV,)])
        raise AssertionError(sql)


USAGE = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0)
BRIEF = "Nile AI sells demand forecasts to Cairo grocers; seed round 2026 (https://nile.ai/about)."


class FakeClaude:
    """parse: the draft; create: the web research brief, paused once (pause_turn) before it ends."""
    def __init__(self, parsed, pauses=1):
        self.parsed, self.calls, self.kwargs, self.searches, self.pauses = parsed, 0, None, [], pauses
        self.messages = self

    def parse(self, **kwargs):
        self.calls, self.kwargs = self.calls + 1, kwargs
        return SimpleNamespace(parsed_output=self.parsed, usage=USAGE)

    def create(self, **kwargs):
        self.searches.append(kwargs)
        if self.pauses:
            self.pauses -= 1
            return SimpleNamespace(content=[SimpleNamespace(type="server_tool_use", text=None)], stop_reason="pause_turn", usage=USAGE)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=BRIEF)], stop_reason="end_turn", usage=USAGE)


def setup(monkeypatch, tmp_path, claude, on=True):
    (tmp_path / "startups.csv").write_text("region,name,city,size,kind,what,website\n"
                                           "Egypt,Nile AI,Cairo,8,AI/data,Forecasts for grocers,https://nile.ai\n"
                                           "Egypt,Delta Data,Cairo,5,AI/data,Data tools,\n", encoding="utf-8")
    (tmp_path / "contacts.csv").write_text(
        "company,side,name,title,location,linkedin,email,why\n"
        "Nile AI,Founder,Mona Adel,CEO and co-founder,Cairo,,mona@nile.ai,Runs hiring (https://nile.ai/team)\n"
        "Delta Data,Founder,Omar Ali,CEO,Cairo,,,No published email\n"
        "Big Bank,Recruiting,Sara,Recruiter,Cairo,,sara@bank.com,Recruiter\n"
        "Acme Corp,Hiring,Lina Samir,Head of Data,Cairo,,lina@acme.example,Leads the data team (https://acme.example/team)\n",
        encoding="utf-8")
    monkeypatch.setattr(outreach, "STARTUPS", tmp_path / "startups.csv")
    monkeypatch.setattr(outreach, "CONTACTS", tmp_path / "contacts.csv")
    monkeypatch.setattr(outreach, "OUTPUT", tmp_path / "output")
    monkeypatch.setitem(outreach.SETTINGS, "features", {"outreach_drafts": on})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: claude)


GOOD = FollowUp(subject="Your pipelines, run without hands", body="Hi Mona, your team needs data it can trust every "
                "morning. I built a pipeline of 3 sources with Airflow that runs with no one watching it. Could we talk this "
                "week? Omar Shalaby")


def test_a_draft_is_written_once_for_a_startup_role_with_a_founder_email(monkeypatch, tmp_path):
    claude = FakeClaude(GOOD)
    setup(monkeypatch, tmp_path, claude)
    outreach.outreach_drafts(FakeConn())
    files = list((tmp_path / "output" / "outreach").glob("*.md"))
    assert [f.name for f in files] == ["nile-ai-data-engineer.md"] and claude.calls == 1  # not the bank, not Delta (no email)
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("To: Mona Adel <mona@nile.ai>\nSubject: Your pipelines, run without hands\n\nHi Mona")
    assert "Omar Shalaby\nomarlabs.dev · github.com/omarshalabyy1\n" in text and "Not sent." in text
    brand = claude.kwargs["system"][0]["text"]  # Omar's brand voice and the humanizer's rules
    assert "Start from the reader's problem" in brand and "never job-seeking" in brand and "Null Labs AI Inc." in brand
    assert "Sound like a person" in brand and "what Omar would do for them" in brand
    # the startup was researched first, on the web, once (a pause_turn resumed), and the brief went into the draft
    assert len(claude.searches) == 2 and {t["name"] for t in claude.searches[0]["tools"]} == {"web_search", "web_fetch"}
    assert claude.searches[1]["messages"][-1]["role"] == "assistant"  # the paused turn sent back, no extra user message
    assert BRIEF in claude.kwargs["messages"][0]["content"] and "Forecasts for grocers" in claude.searches[0]["messages"][0]["content"]
    assert (tmp_path / "output" / "outreach" / "briefs" / "nile-ai.md").read_text(encoding="utf-8").startswith(BRIEF)
    outreach.outreach_drafts(FakeConn())  # the next run: the file is the record, no new call
    assert claude.calls == 1 and len(claude.searches) == 2


def test_switched_off_means_no_call_and_no_file(monkeypatch, tmp_path):
    claude = FakeClaude(GOOD)
    setup(monkeypatch, tmp_path, claude, on=False)
    outreach.outreach_drafts(FakeConn())
    assert claude.calls == 0 and not claude.searches and not (tmp_path / "output").exists()


def test_no_brief_means_no_draft(monkeypatch, tmp_path):
    claude = FakeClaude(GOOD, pauses=9)  # still paused after every resume: no brief, so no blind draft
    setup(monkeypatch, tmp_path, claude)
    outreach.outreach_drafts(FakeConn())
    assert claude.calls == 0 and not list((tmp_path / "output").rglob("*.md"))


def test_a_draft_too_long_or_naming_a_gap_is_thrown_away(monkeypatch, tmp_path):
    for body in ("word " * 121, "I have not yet used Spark, but could we talk?",
                 "Your team needs clean data — I built that at Argus. Could we talk?"):  # a dash: the humanizer's rule
        claude = FakeClaude(FollowUp(subject="Hello", body=body))
        setup(monkeypatch, tmp_path, claude)
        outreach.outreach_drafts(FakeConn())
        assert claude.calls == 1 and not list((tmp_path / "output" / "outreach").glob("*.md"))


def test_the_founders_research_asks_only_for_startups_with_a_role_and_no_email(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, FakeClaude(GOOD))
    assert outreach.without_contact(FakeConn()) == ["Delta Data"]


def test_a_job_you_applied_to_scored_8_or_more_gets_one_note_to_its_contact(monkeypatch, tmp_path):
    """Any company, startup or not, with a Founder or Hiring email; once per job, even when it is also a startup role."""
    claude = FakeClaude(GOOD, pauses=0)
    setup(monkeypatch, tmp_path, claude)
    applied = [(10, "BI Developer", "Acme Corp", "Power BI.", 9),   # not a startup: a note to its Hiring contact
               (11, "Data Analyst", "Acme Corp", "SQL.", 7),        # scored under 8: none
               (12, "Analytics Engineer", "Big Bank", "dbt.", 9),   # only a Recruiting row: none
               (1, "Data Engineer", "Nile AI", "Airflow.", 8)]      # also a startup role: still one draft
    outreach.outreach_drafts(FakeConn(applied))
    files = sorted(f.name for f in (tmp_path / "output" / "outreach").glob("*.md"))
    assert files == ["acme-corp-bi-developer.md", "nile-ai-data-engineer.md"] and claude.calls == 2
    text = (tmp_path / "output" / "outreach" / "acme-corp-bi-developer.md").read_text(encoding="utf-8")
    assert text.startswith("To: Lina Samir <lina@acme.example>\n") and "Not sent." in text
    assert "Sound like a person" in claude.kwargs["system"][0]["text"]  # the same brand and humanizer rules
    outreach.outreach_drafts(FakeConn(applied))  # the next run: the files are the record
    assert claude.calls == 2
