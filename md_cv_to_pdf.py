"""Render a job-radar style CV markdown file to a one-page A4 PDF with job-radar's own CV layout.
Usage: python md_cv_to_pdf.py <cv.md>  (writes <cv>.pdf next to it)"""
import re
import sys
from pathlib import Path

from job_radar.tailor import Draft, Education, Entry, Link, SkillGroup, cv_html, pdf_of, pdf_problems

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def link(s: str) -> Link:
    m = LINK.fullmatch(s.strip())
    return Link(text=m.group(1), url=m.group(2)) if m else Link(text=s.strip(), url="")


def head(line: str) -> tuple[str, str, str]:
    """'**Title** — org | when' -> (title, org, when)."""
    m = re.match(r"\*\*(.+?)\*\* — (.+)", line)
    org, _, when = m.group(2).rpartition(" | ")
    return m.group(1), org, when


src = Path(sys.argv[1])
lines = src.read_text(encoding="utf-8").splitlines()
sections, current = {}, None
for line in lines[3:]:
    if line.startswith("## "):
        current = sections.setdefault(line[3:].strip(), [])
    elif current is not None and line.strip():
        current.append(line.rstrip())


def entries(rows: list[str]) -> list[Entry]:
    out = []
    for r in rows:
        if r.startswith("**"):
            t, o, w = head(r)
            out.append(Entry(title=t, org=o, when=w, bullets=[], technologies="", repo_url=""))
        elif r.startswith("- "):
            out[-1].bullets.append(r[2:])
        elif r.startswith("*Technologies:"):
            out[-1].technologies = r.strip("*").split(":", 1)[1].strip()
    return out


edu = []
for r in sections.get("Education", []):
    if r.startswith("**"):
        t, o, w = head(r)
        edu.append(Education(degree=t, school=o, when=w, note=""))
    elif r.startswith("- "):
        edu[-1].note = r[2:]

d = Draft(
    name=lines[0].lstrip("# ").strip(), title_line=lines[1].strip(),
    contact=[link(c) for c in lines[2].split(" • ")],
    summary=" ".join(sections["Professional Summary"]),
    skills=[SkillGroup(label=m.group(1), items=m.group(2).strip())
            for m in (re.match(r"\*\*(.+?):\*\* (.+)", r) for r in sections["Core Skills"]) if m],
    experience=entries(sections["Professional Experience"]), projects=entries(sections.get("Projects", [])),
    education=edu, certifications=[],
    languages=next((r.split(":**", 1)[1].strip() for r in sections.get("Additional", []) if "Languages" in r), ""),
    file_tag="", cover_letter_md="", notes_md="", form_answers_md="")

page = cv_html(d)
pdf = pdf_of(page)
out = src.with_suffix(".pdf")
out.write_bytes(pdf)
print(out, "problems:", pdf_problems(pdf, page) or "none")
