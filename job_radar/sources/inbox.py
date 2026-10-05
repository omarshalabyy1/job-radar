"""Your inboxes: the jobs in your job alerts and other emails, read-only over IMAP."""

from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..config import HOURS_OLD, role_of
from .base import row


IMAP_HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
              "yahoo.com": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com",
              "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
              "live.com": "outlook.office365.com"}


# Every email is read. One from a job site gives every link titled like one of your roles (any
# LinkedIn email: only its job links); any other email (a recruiter, a newsletter) only its links to
# a job page, so an article such as "10 KPIs Every Data Analyst..." is no job, whatever the subject.
JOB_SENDERS = (r"linkedin|jobalerts|jobs-listings|jobs-noreply|indeed|wuzzuf|bayt|glassdoor|naukrigulf|gulftalent"
               r"|akhtaboot|tanqeeb|forasna|jooble|himalayas|remotive|weworkremotely|wellfound|recruit"
               r"|talent|careers?@|hiring")


JOB_PAGE = (r"/jobs?/|/careers?/|/vacanc|/positions?/|viewjob|job-?listing|[?&]jk=|greenhouse\.io|lever\.co"
            r"|ashbyhq\.com|workable\.com")


# a link that only says "Learn more" under a job card (Wellfound): the card is the text above it
GENERIC_LINK = r"learn more|view job|see job|view details|more details|apply|apply now|see more"

# LinkedIn's job alert senders: every job in their emails is kept, whatever its title
LINKEDIN_ALERTS = r"(jobalerts-noreply|jobs-listings)@linkedin\.com"

# an email bigger than this carries attachments, not job alerts: skipped, to spare Gmail's daily
# IMAP download allowance (2,500 MB)
MAIL_BYTES = 5_000_000


def mailboxes(seen: set[str]) -> list[dict]:
    """Jobs in every email of your inboxes and spam folders (LinkedIn, Indeed, Wuzzuf, Wellfound,
    Bayt alerts, an employer's email ...), over IMAP and read-only: nothing is marked read, moved or
    deleted, and nothing leaves the laptop. An email already read for jobs (its Message-ID in
    `seen`) is not read again.

    MAILBOXES in .env: address:app-password[:imap-host], comma-separated; the host is known for
    Gmail, Yahoo, iCloud and Outlook addresses."""
    accounts = [a.strip() for a in os.environ.get("MAILBOXES", "").split(",") if a.strip()]
    if not accounts:
        print("email: no MAILBOXES, skipped")
        return []
    rows = []
    for account in accounts:
        address, password, *host = account.split(":")
        try:
            rows += read_mailbox(address, password.replace(" ", ""),
                                 host[0] if host else IMAP_HOSTS[address.split("@")[-1].lower()], seen)
        except Exception as e:  # one mailbox down is a short day
            print(f"WARNING email {address}: {e!r}"[:300])
    return rows


def read_mailbox(address: str, password: str, host: str, seen: set[str]) -> list[dict]:
    rows = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(address, password)
        # the inbox, then the spam folder: the one the server flags \Junk (Gmail's [Gmail]/Spam)
        spam = [re.search(r'"?([^"]*)"?$', line.decode()).group(1) for line in imap.list()[1] if b"\\Junk" in line]
        since_day = (date.today() - timedelta(hours=HOURS_OLD)).strftime("%d-%b-%Y")
        for folder in ["INBOX", *spam]:
            imap.select(f'"{folder}"', readonly=True)
            for uid in imap.uid("search", None, "SINCE", since_day)[1][0].split():
                info, head = imap.uid("fetch", uid, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])")[1][0]
                h = email.message_from_bytes(head, policy=email.policy.default)
                message_id = str(h["Message-ID"] or f"{address}/{folder}/{uid.decode()}")
                size = int(re.search(rb"RFC822\.SIZE (\d+)", info).group(1))
                if message_id in seen or str(h["Subject"]).startswith("Job radar") or size > MAIL_BYTES:
                    continue  # read before, its own digest, or attachments
                msg = email.message_from_bytes(imap.uid("fetch", uid, "(BODY.PEEK[])")[1][0][1],
                                               policy=email.policy.default)
                body = msg.get_body(preferencelist=("html",))
                alert = re.search(LINKEDIN_ALERTS, str(h["From"]), re.I) is not None
                jobs = jobs_in_email(body.get_content(), alert) if body else []
                if not re.search(JOB_SENDERS, str(h["From"]), re.I):
                    jobs = [job for job in jobs if re.search(JOB_PAGE, job[3], re.I)]
                    if not jobs:  # not a job email
                        continue
                posted = str(parsedate_to_datetime(str(h["Date"])).date()) if h["Date"] else None
                meta = {"mailbox": address, "folder": folder, "message_id": message_id, "from": str(h["From"]),
                        "subject": str(h["Subject"])}
                for title, company, location, url in jobs:
                    # the place comes from the location or the title in transform, else "Unknown location"
                    rows.append(row("email", None, title, company, location, url, posted,
                                    None, {**meta, "title": title, "company": company, "location": location, "url": url}))
                print(f"email {address} {folder}: {h['Subject']!s:.60} -> {len(jobs)} jobs")
    return rows


def jobs_in_email(page: str, alert: bool = False) -> list[tuple[str, str, str, str]]:
    """(title, company, location, url) of each link in a job email whose words are one of the roles;
    in a LinkedIn alert (alert), each of its job links whatever the title.
    Job alerts put the company and location on the line under the title ("Company · Location");
    a LinkedIn alert puts the whole card in one link: title, "Company · Location", then extras; a
    Wellfound alert puts the card above a "Learn more" link: title, then company."""
    soup = BeautifulSoup(page, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        links.append((a.get_text("\n", strip=True), a["href"]))
        a.replace_with(f"\n@@{len(links) - 1}@@\n")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    jobs, urls, after_link = [], set(), 0
    for i, line in enumerate(lines):
        link = re.fullmatch(r"@@(\d+)@@", line)
        if not link:
            continue
        above, after_link = lines[after_link:i], i + 1  # the text since the previous link
        text, url = links[int(link.group(1))]
        title, _, card = text.partition("\n")
        if re.fullmatch(GENERIC_LINK, title, re.I):  # the card is the text above: its first role line, then the rest
            first = next((k for k, text_line in enumerate(above) if role_of(text_line)), None)
            if first is None:
                continue
            title, card = above[first], "\n".join(above[first + 1:])
        url = canonical_url(url)
        # from LinkedIn only its job pages (not profiles, posts or searches); in its alert, each kept
        # whatever its title: LinkedIn chose it for your alert
        linkedin_job = "linkedin.com/jobs/view/" in url
        if not role_of(title, alert=alert and linkedin_job) or url in urls or ("linkedin.com" in url and not linkedin_job):
            continue
        urls.add(url)
        after = card.split("\n")[0] if card else lines[i + 1] if i + 1 < len(lines) else ""
        if (next_link := re.fullmatch(r"@@(\d+)@@", after)):  # the company is a link of its own
            after = links[int(next_link.group(1))][0].split("\n")[0]
        company, location = (part.strip(" -") for part in (re.split(r"\s+[·•|–-]\s+", after, maxsplit=1) + [""])[:2])
        if not location and not card and i + 2 < len(lines) and not lines[i + 2].startswith("@@"):
            location = lines[i + 2]  # the location on a line of its own
        jobs.append((title, company if not role_of(company) else "", location, url))
    return jobs


def canonical_url(url: str) -> str:
    """One link per job: a LinkedIn alert's tracking link as the plain job page, a tracking link
    that carries the job page's address in its path (Wuzzuf's) as that page, and any link without
    its utm_ tracking parameters (which differ from one email to the next)."""
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    if inner := re.search(r"/(https?(?::|%3A)%2F%2F[^/&]+)", url, re.I):
        return canonical_url(unquote(inner.group(1)))
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")])))
