"""research: deep research on Claude Managed Agents, run by hand.

    python -m job_radar.research <job> [key=value ...]

A job is a prompt in settings.yaml (research.jobs). The agent runs on Anthropic's servers, searches and reads the web, and
its report is written to output/<out>. Values for the prompt's {keys} come from the command line, else the job's
defaults; a list under `each` runs the job once per item when its key is not given (startups: one run per region).
job_id=N adds that job's company, title, location and description, and your newest CV, from the warehouse (interview).
A job with `csv: <file>` also has its report's last ```csv block merged into output/<file>, a name already there replaced
(startups: the tracker's Startups page reads that file).
The agent and its environment are made once and kept in output/research/ids.json."""

import io
import itertools
import json
import re
import sys
from datetime import date

import anthropic
import pandas as pd

from .config import OUTPUT, SETTINGS

CFG = SETTINGS["research"]
IDS = OUTPUT / "research" / "ids.json"
SYSTEM = ("You are a research agent working for one person. Search the web and read the sources in full. Prefer primary "
          "sources: the company's own site, careers page and filings, then reputable press. Cite every claim with its URL. "
          "Say where sources disagree or where you found nothing; never fill a gap with a guess. "
          "Your last message is the finished report in markdown and nothing else.")
WEB = [{"name": name, "enabled": True, "permission_policy": {"type": "auto"}} for name in ("web_search", "web_fetch")]
NETWORK = "unrestricted"  # "limited" refused every web search; only the two web tools are on, so nothing else can use it
TOOLS = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}, "configs": WEB}]  # the web only


def agent_ids(client) -> dict:
    """The agent and environment ids: made on the first run, and again when the model, effort or system prompt changes."""
    config = [CFG["model"], CFG["effort"], SYSTEM, NETWORK]
    saved = json.loads(IDS.read_text(encoding="utf-8")) if IDS.exists() else {}
    if saved.get("config") != config:
        env = client.beta.environments.create(
            name="job-radar-research", config={"type": "cloud", "networking": {"type": NETWORK}})
        agent = client.beta.agents.create(name="job-radar research", system=SYSTEM, tools=TOOLS,
                                          model={"id": CFG["model"], "effort": CFG["effort"]})
        saved = {"config": config, "env": env.id, "agent": agent.id, "version": agent.version}
        IDS.parent.mkdir(parents=True, exist_ok=True)
        IDS.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    return saved


def job_values(job_id: str) -> dict:
    """What the warehouse knows of one job, and your newest CV."""
    from .db import connect

    with connect() as conn:
        row = conn.execute("SELECT company, title, location, description FROM core.job WHERE job_id = %s", (job_id,)).fetchone()
        cv = conn.execute("SELECT text FROM core.cv ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit(f"research: no job {job_id} in the warehouse")
    return {"company": row[0], "title": row[1], "location": row[2], "description": (row[3] or "")[:6000],
            "cv": cv[0] if cv else ""}


def ask(client, ids: dict, prompt: str) -> str:
    """One session: the prompt in, the agent's last message out. A call that needs your approval is denied."""
    session = client.beta.sessions.create(agent={"type": "agent", "id": ids["agent"], "version": ids["version"]},
                                          environment_id=ids["env"], title=prompt[:60])
    last = ""
    with client.beta.sessions.events.stream(session_id=session.id) as stream:  # stream first, then send
        client.beta.sessions.events.send(session_id=session.id, events=[
            {"type": "user.message", "content": [{"type": "text", "text": prompt}]}])
        for event in stream:
            if event.type == "agent.message":
                last = "".join(b.text for b in event.content if b.type == "text") or last
            elif event.type in ("agent.tool_use", "agent.mcp_tool_use") and event.evaluated_permission == "ask":
                client.beta.sessions.events.send(session_id=session.id, events=[
                    {"type": "user.tool_confirmation", "tool_use_id": event.id, "result": "deny"}])
            elif event.type == "session.status_terminated":
                break
            elif event.type == "session.status_idle" and event.stop_reason.type != "requires_action":
                break
    return last


def merge_csv(report: str, path) -> int:
    """Merge the report's last ```csv block into the file at `path`, in its columns; a name already there is replaced."""
    blocks = re.findall(r"```csv\n(.*?)```", report, re.S)
    if not blocks:
        return 0
    new = pd.read_csv(io.StringIO(blocks[-1]), dtype=str).fillna("")
    old = pd.read_csv(path, dtype=str).fillna("") if path.exists() else new.iloc[:0]
    old = old[~old["name"].str.strip().str.lower().isin(new["name"].str.strip().str.lower())]  # the old file stays as it is
    both = pd.concat([old, new.reindex(columns=old.columns, fill_value="")])
    path.parent.mkdir(parents=True, exist_ok=True)
    both.to_csv(path, index=False, encoding="utf-8")
    return len(new)


class Values(dict):
    def __missing__(self, key):  # a {key} nobody gave stays empty
        return ""


def main() -> None:
    name, *pairs = sys.argv[1:] or [""]
    job = CFG["jobs"].get(name)
    if not job or any("=" not in p for p in pairs):
        raise SystemExit(f"{__doc__}\nJobs: {', '.join(CFG['jobs'])}")
    given = dict(p.split("=", 1) for p in pairs)
    values = {**job.get("defaults", {}), "date": str(date.today()), **given}
    if "job_id" in given:
        values.update(job_values(given["job_id"]))
    each = {k: v for k, v in job.get("each", {}).items() if k not in given}
    client = anthropic.Anthropic()
    ids = agent_ids(client)
    for combo in itertools.product(*each.values()):
        run = Values({**values, **dict(zip(each, combo))})
        report = ask(client, ids, job["prompt"].format_map(run))
        if not report:
            print(f"research {name} {dict(zip(each, combo))}: the agent sent no report")
            continue
        path = OUTPUT / re.sub(r'[<>:"|?*\\]', "", job["out"].format_map(run))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
        print(f"research {name} {dict(zip(each, combo))}: {path}")
        if job.get("csv"):
            print(f"  {merge_csv(report, OUTPUT / job['csv'])} rows merged into {job['csv']}")


if __name__ == "__main__":
    main()
