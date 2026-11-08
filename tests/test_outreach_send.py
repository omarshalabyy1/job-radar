"""job_radar/outreach_send.py: the outreach sender, dry run first (docs/superpowers/plans/2026-11-08-auto-apply.md, "Outreach sender").
One test per guard of that table. Nothing here reaches a mail server: smtplib.SMTP_SSL is replaced by a tripwire in every test, and the
few tests that need the live path give it a fake. No test prints or asserts on a real address or password."""

import csv
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from zoneinfo import ZoneInfo

import psycopg
import pytest

from job_radar import config, db, outreach as drafts, outreach_send
from job_radar.tailor import slug

CAIRO = ZoneInfo("Africa/Cairo")
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)  # a Wednesday, 12:00 in Cairo
CFG = {"enabled": True, "mode": "dry_run", "daily_total": 20, "per_mailbox": 10, "hourly": 5, "gap_minutes": [2, 10],
       "work_hours": [9, 17], "followup_days": [7, 14], "followup_grace_days": 3, "bounce_limit": 0.03,
       "bounce_window_days": 30, "pause_days": 7}
MAILBOXES = ["omar.a@gmail.com", "omar.b@gmail.com", "omar.c@gmail.com"]
PASSWORDS = {m: f"secret-{m[5]}" for m in MAILBOXES}
BODY = ("{first}, stock data that lands late costs a retailer sales. I rebuilt a three-source pipeline in Cairo so inventory "
        "numbers arrive by 6am, which removed four hours of manual reporting a day. In your first months I would do the same "
        "for your supplier feeds. Could we talk for 15 minutes this week?\n\nOmar Shalaby")
SOURCE = "https://{host}/contact"


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    clean(connection)
    yield connection
    connection.rollback()
    clean(connection)  # the live path commits before it sends: leave the throwaway database as it was found
    connection.close()


def clean(connection):
    for table in ("outreach_send", "outreach_pause", "suppression"):
        connection.execute(f"DELETE FROM core.{table}")
    connection.commit()


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    """Mailboxes, folders, the contacts file and the phone push all point into tmp_path; SMTP is a tripwire."""
    monkeypatch.setenv("MAILBOXES", ",".join(f"{m}:{p}" for m, p in PASSWORDS.items()))
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.setattr(outreach_send, "DRAFTS", tmp_path / "outreach")
    monkeypatch.setattr(outreach_send, "STOP_FILE", tmp_path / "outreach.stop")
    monkeypatch.setattr(drafts, "CONTACTS", tmp_path / "contacts.csv")
    pushes = []
    monkeypatch.setattr(outreach_send, "notify", lambda title, message: pushes.append((title, message)))

    class Tripwire:
        def __init__(self, *a, **k):
            raise AssertionError("SMTP was called")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Tripwire)
    (tmp_path / "outreach").mkdir()
    write_contacts(tmp_path, [])
    return pushes


@pytest.fixture
def pushes(world):
    return world


def write_contacts(tmp_path, rows):
    with (tmp_path / "contacts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["company", "side", "name", "title", "location", "linkedin", "email", "why", "source"])
        w.writeheader()
        for r in rows:
            w.writerow({"side": "Founder", "title": "CEO", "location": "Cairo", "linkedin": "", "why": "founder", **r})


def person(i, **over):
    """One company with a verified contact (own site as source) and its draft: returns (contact row, draft path)."""
    company, email = f"Co{i}", f"founder{i}@co{i}.example"
    return {"company": company, "name": f"Maya Founder{i}", "email": email, "source": SOURCE.format(host=f"co{i}.example"), **over}


def add(tmp_path, people, **draft_over):
    """Write the contacts file and one draft per person, in the format outreach.py writes."""
    write_contacts(tmp_path, people)
    paths = []
    for i, p in enumerate(people):
        title = "Data Engineer"
        path = outreach_send.DRAFTS / f"{slug(p['company'], title)}.md"
        subject = draft_over.get("subject", f"Late stock data at {p['company']}")
        body = draft_over.get("body", BODY).format(first="Maya")
        path.write_text(f"To: {p['name']} <{p['email']}>\nSubject: {subject}\n\n{body}\n{drafts.SIGNATURE}\n\n---\n"
                        f"Draft to {p['name']}, CEO at {p['company']}, for {title} (score {9 - i % 3}). Why them: founder. "
                        f"Research: output/outreach/briefs/x.md. Not sent.\n", encoding="utf-8")
        paths.append(path)
    return paths


def many(tmp_path, n, **draft_over):
    return add(tmp_path, [person(i) for i in range(n)], **draft_over)


def seed(conn, mailbox, recipient, when, followup_no=0, mode="dry_run", thread=None, subject="Late stock data", tag=""):
    message_id = f"<seed-{mailbox[:7]}-{recipient}-{followup_no}-{tag}-{when:%d%H%M%S}@mail.example>"
    return conn.execute(
        "INSERT INTO core.outreach_send (mailbox, recipient, thread_id, message_id, sent_at, mode, followup_no, subject)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (mailbox, recipient, thread or message_id, message_id, when, mode, followup_no, subject)).fetchone()[0]


def sends(conn, mode=None):
    return conn.execute("SELECT mailbox, recipient, thread_id, message_id, sent_at, mode, followup_no FROM core.outreach_send"
                        " WHERE (%s::text IS NULL OR mode = %s) ORDER BY sent_at, id", (mode, mode)).fetchall()


def reply(sender, subject="Re: Late stock data", text="Thanks, let's talk.", at=NOW, mailbox=MAILBOXES[0], message_id=None):
    return {"message_id": message_id or f"<{sender}-{subject}-{at:%d%H}@x>", "received_at": at, "sender": sender, "subject": subject,
            "text": text, "mailbox": mailbox, "kind": "other", "company": None, "title": None}


# ---------------------------------------------------------------------------------------------------------------- settings

def test_the_settings_ship_off_and_in_dry_run():
    s = config.SETTINGS["outreach"]
    assert s["enabled"] is False and s["mode"] == "dry_run"
    assert (s["daily_total"], s["per_mailbox"], s["hourly"]) == (20, 10, 5)
    assert s["gap_minutes"] == [2, 10] and s["followup_days"] == [7, 14] and s["bounce_limit"] == 0.03


# ------------------------------------------------------------------------------------------------------------- recipients

def test_a_recipient_with_a_source_on_the_companys_own_site_or_in_the_press_is_mailed(conn, tmp_path):
    own = person(1)
    press = person(2, source="https://techcrunch.example/2026/10/co2-raises-seed")
    add(tmp_path, [own, press])
    got = {s.recipient for s in outreach_send.eligible(conn, NOW, CFG)}
    assert got == {own["email"], press["email"]}


@pytest.mark.parametrize("over", [
    {"source": ""},                                                    # no source: nothing says it was published
    {"source": "https://www.linkedin.com/in/maya"},                    # never LinkedIn
    {"source": "https://uk.linkedin.com/in/maya", "why": "founder"},
    {"source": "https://rocketreach.co/maya-email"},                   # bought
    {"source": "https://apollo.io/people/maya"},
    {"source": "https://co1.example/team", "why": "email guessed from the firstname@ pattern"},  # guessed
    {"source": "not a url"},
    {"email": "maya-at-co1"},                                          # not an address
    {"side": "Engineer"},                                              # not a founder or hiring contact
])
def test_a_recipient_without_a_published_company_or_press_source_is_never_mailed(conn, tmp_path, over):
    add(tmp_path, [person(1, **over)])
    assert outreach_send.eligible(conn, NOW, CFG) == []


def test_the_source_may_sit_at_the_end_of_why_as_outreach_research_writes_it(conn, tmp_path):
    p = person(1, source="", why="founder, named on the about page https://co1.example/about")
    add(tmp_path, [p])
    assert [s.recipient for s in outreach_send.eligible(conn, NOW, CFG)] == [p["email"]]


def test_a_draft_to_an_address_not_in_contacts_is_never_mailed(conn, tmp_path):
    [path] = many(tmp_path, 1)
    path.write_text(path.read_text(encoding="utf-8").replace("founder0@co0.example", "someone.else@co0.example"), encoding="utf-8")
    assert outreach_send.eligible(conn, NOW, CFG) == []


def test_a_person_is_mailed_once_even_with_two_drafts(conn, tmp_path):
    [first] = many(tmp_path, 1)
    second = first.with_name("co0-analytics-engineer.md")
    second.write_text(first.read_text(encoding="utf-8").replace("Late stock data", "Another subject"), encoding="utf-8")
    assert len(outreach_send.eligible(conn, NOW, CFG)) == 1
    outreach_send.run(conn, NOW, CFG)
    assert outreach_send.eligible(conn, NOW + timedelta(hours=1), CFG) == []  # the second draft is not a second cold email


# ---------------------------------------------------------------------------------------- one recipient, one mailbox, one thread

def test_a_follow_up_comes_from_the_same_mailbox_and_in_the_same_thread(conn, tmp_path):
    many(tmp_path, 1)
    [first] = outreach_send.run(conn, NOW, CFG)
    (row,) = sends(conn)
    later = NOW + timedelta(days=7, hours=1)
    [follow] = outreach_send.eligible(conn, later, CFG)
    assert follow.followup_no == 1 and follow.recipient == row[1]
    assert follow.mailbox == row[0] == first.mailbox            # the first mailbox that wrote to them writes every follow-up
    assert follow.thread_id == row[3] and follow.in_reply_to == row[3]
    assert follow.subject == "Re: " + first.subject
    message = outreach_send.compose(follow, "<new@mail.example>", later)
    assert message["From"].endswith(f"<{follow.mailbox}>") and message["In-Reply-To"] == row[3] and row[3] in message["References"]


def test_follow_ups_are_planned_in_their_threads_mailbox_whatever_the_load_on_the_others(conn, tmp_path):
    many(tmp_path, 6)
    for i in range(6):                       # six recipients, each first written by whichever mailbox the sender chose
        outreach_send.run(conn, NOW + timedelta(minutes=15 * i), CFG)
    first_mailbox = {r[1]: r[0] for r in sends(conn)}
    assert len(first_mailbox) == 6 and len(set(first_mailbox.values())) > 1
    plan = outreach_send.eligible(conn, NOW + timedelta(days=7, hours=2), CFG)
    assert plan and all(s.mailbox == first_mailbox[s.recipient] for s in plan)


# ------------------------------------------------------------------------------------------------------------------ the caps

def test_the_per_mailbox_daily_cap_holds(conn, tmp_path, monkeypatch):
    many(tmp_path, 15)
    monkeypatch.setenv("MAILBOXES", f"{MAILBOXES[0]}:{PASSWORDS[MAILBOXES[0]]}")
    for i in range(10):
        seed(conn, MAILBOXES[0], f"old{i}@x.example", NOW - timedelta(hours=2, minutes=i))
    assert outreach_send.eligible(conn, NOW, CFG) == []
    assert outreach_send.eligible(conn, NOW, {**CFG, "per_mailbox": 11}) != []  # the cap is what stopped it


def test_the_total_daily_cap_holds_over_all_mailboxes(conn, tmp_path):
    many(tmp_path, 25)
    for i in range(20):                                  # 7 + 7 + 6 today, all more than an hour ago
        seed(conn, MAILBOXES[i % 3], f"old{i}@x.example", NOW - timedelta(hours=2, minutes=i))
    assert outreach_send.eligible(conn, NOW, CFG) == []
    assert outreach_send.eligible(conn, NOW, {**CFG, "daily_total": 21}) != []


def test_the_hourly_cap_holds(conn, tmp_path):
    many(tmp_path, 15)
    for i in range(5):
        seed(conn, MAILBOXES[i % 3], f"old{i}@x.example", NOW - timedelta(minutes=50 - 9 * i))
    assert outreach_send.eligible(conn, NOW + timedelta(minutes=5), CFG) == []    # five inside the hour before
    later = outreach_send.eligible(conn, NOW + timedelta(minutes=45), CFG)        # the first aged out of the hour
    assert later and all(s.at >= NOW + timedelta(minutes=45) for s in later)


def test_a_plan_has_gaps_of_2_to_10_minutes_and_never_more_than_the_hourly_cap(conn, tmp_path):
    many(tmp_path, 40)
    plan = outreach_send.eligible(conn, NOW, CFG)
    assert 1 < len(plan) <= 5
    assert plan[0].at == NOW
    gaps = [(b.at - a.at).total_seconds() for a, b in zip(plan, plan[1:])]
    assert all(120 <= g <= 600 for g in gaps)
    assert plan == outreach_send.eligible(conn, NOW, CFG)  # the gaps are drawn once, not again at every tick
    assert len({s.recipient for s in plan}) == len(plan)


def test_a_gap_must_pass_after_the_last_send_before_the_next_one(conn, tmp_path):
    many(tmp_path, 5)
    seed(conn, MAILBOXES[0], "old@x.example", NOW - timedelta(seconds=30))
    plan = outreach_send.eligible(conn, NOW, CFG)
    assert plan and plan[0].at >= NOW - timedelta(seconds=30) + timedelta(minutes=2)
    assert plan[0].at > NOW
    assert outreach_send.run(conn, NOW, CFG) == []         # not due yet: nothing is written


def test_a_day_of_ticks_stays_inside_every_cap_and_every_working_hour(conn, tmp_path):
    many(tmp_path, 40)
    t = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    while t < datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc):
        outreach_send.run(conn, t, CFG)
        t += timedelta(minutes=3)
    rows = sends(conn, "dry_run")
    assert len(rows) == 20                                  # the day's total, reached and never passed
    for m in MAILBOXES:
        assert sum(r[0] == m for r in rows) <= 10
    times = [r[4] for r in rows]
    assert all(b - a >= timedelta(minutes=2) for a, b in zip(times, times[1:]))
    for s in times:
        assert sum(s - timedelta(hours=1) < x <= s for x in times) <= 5
        assert 9 <= s.astimezone(CAIRO).hour < 17           # the recipients are in Cairo


def test_the_table_refuses_a_third_follow_up_and_a_repeat(conn):
    seed(conn, MAILBOXES[0], "a@x.example", NOW, followup_no=2)
    with pytest.raises(psycopg.errors.CheckViolation):
        seed(conn, MAILBOXES[0], "b@x.example", NOW, followup_no=3)
    conn.rollback()
    seed(conn, MAILBOXES[0], "a@x.example", NOW)
    with pytest.raises(psycopg.errors.UniqueViolation):
        seed(conn, MAILBOXES[1], "a@x.example", NOW, tag="again")  # one cold email per person and mode, any mailbox


# ------------------------------------------------------------------------------------------------------------- follow-ups

def follow_up_numbers(conn, now):
    return [s.followup_no for s in outreach_send.eligible(conn, now, CFG) if s.recipient == "founder0@co0.example"]


def test_follow_ups_go_on_day_7_and_day_14_and_never_a_third(conn, tmp_path):
    many(tmp_path, 1)
    t0 = NOW
    outreach_send.run(conn, t0, CFG)
    assert follow_up_numbers(conn, t0 + timedelta(days=6, hours=20)) == []
    assert follow_up_numbers(conn, t0 + timedelta(days=7, minutes=1)) == [1]
    assert follow_up_numbers(conn, t0 + timedelta(days=11)) == []           # past the grace days: it is no longer day 7
    first = sends(conn)[0]
    seed(conn, first[0], first[1], t0 + timedelta(days=7, hours=1), followup_no=1, thread=first[3])
    assert follow_up_numbers(conn, t0 + timedelta(days=13)) == []
    assert follow_up_numbers(conn, t0 + timedelta(days=14, minutes=1)) == [2]
    seed(conn, first[0], first[1], t0 + timedelta(days=14, hours=1), followup_no=2, thread=first[3])
    for days in (15, 21, 28, 60):
        assert follow_up_numbers(conn, t0 + timedelta(days=days)) == []


def test_a_follow_up_is_a_short_note_in_the_thread_that_passes_the_same_checks(conn, tmp_path):
    many(tmp_path, 1)
    outreach_send.run(conn, NOW, CFG)
    for days, number in ((7, 1), (14, 2)):
        if number == 2:
            first = sends(conn)[0]
            seed(conn, first[0], first[1], NOW + timedelta(days=7, hours=1), followup_no=1, thread=first[3])
        [follow] = outreach_send.eligible(conn, NOW + timedelta(days=days, hours=1), CFG)
        assert outreach_send.draft_problems(follow.subject, follow.body, follow=True) == []
        assert follow.body.rstrip().splitlines()[-1] == outreach_send.OPT_OUT


# ----------------------------------------------------------------------------------------------------------------- suppression

def test_an_address_in_the_suppression_list_is_never_mailed(conn, tmp_path):
    suppressed, free = person(1), person(2)
    add(tmp_path, [suppressed, free])
    conn.execute("INSERT INTO core.suppression (address, reason) VALUES (%s, 'unsubscribe')", (suppressed["email"].upper(),))
    assert [s.recipient for s in outreach_send.eligible(conn, NOW, CFG)] == [free["email"]]


def test_a_suppressed_address_gets_no_follow_up_either(conn, tmp_path):
    many(tmp_path, 1)
    outreach_send.run(conn, NOW, CFG)
    conn.execute("INSERT INTO core.suppression (address, reason) VALUES ('founder0@co0.example', 'reply')")
    assert follow_up_numbers(conn, NOW + timedelta(days=7, hours=1)) == []


def mailed(conn, address="maya@co1.example", mailbox=MAILBOXES[0], when=NOW - timedelta(days=2), mode="live"):
    seed(conn, mailbox, address, when, mode=mode)


@pytest.mark.parametrize("text, subject, reason", [
    ("Thanks Omar, let's talk Thursday.", "Re: Late stock data", "reply"),
    ("No.", "Re: Late stock data", "no"),
    ("No thanks, not interested.", "Re: Late stock data", "no"),
    ("Please unsubscribe me.", "Re: Late stock data", "unsubscribe"),
    ("Remove me from your list and do not contact me again.", "Re: Late stock data", "unsubscribe"),
    ("I am out of the office. Please contact priya@co1.example, I no longer work at Co1.", "Automatic reply: Late stock data",
     "out_of_office"),
])
def test_a_reply_a_no_an_unsubscribe_or_a_new_contact_adds_the_address_to_suppression(conn, text, subject, reason):
    mailed(conn)
    out = outreach_send.suppress_from_replies(conn, [reply("Maya <maya@co1.example>", subject, text)], now=NOW, cfg=CFG)
    assert out["suppressed"] == 1
    assert conn.execute("SELECT reason FROM core.suppression WHERE address = 'maya@co1.example'").fetchone() == (reason,)


def test_an_out_of_office_with_no_new_contact_is_no_answer(conn):
    mailed(conn)
    out = outreach_send.suppress_from_replies(
        conn, [reply("maya@co1.example", "Out of office: Late stock data", "I am away until Monday with limited email.")], now=NOW, cfg=CFG)
    assert out["suppressed"] == 0 and conn.execute("SELECT count(*) FROM core.suppression").fetchone() == (0,)


def test_a_bounce_adds_the_failed_address_not_the_mailer_daemon(conn):
    mailed(conn)
    bounce = reply("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)",
                   "Your message wasn't delivered to maya@co1.example because the address couldn't be found.")
    out = outreach_send.suppress_from_replies(conn, [bounce], now=NOW, cfg=CFG)
    assert out["bounces"] == 1
    assert conn.execute("SELECT address, reason FROM core.suppression").fetchall() == [("maya@co1.example", "bounce")]


def test_mail_from_someone_never_mailed_changes_nothing(conn):
    mailed(conn)
    stranger = reply("news@newsletter.example", text="Weekly digest, unsubscribe at the bottom.")
    assert outreach_send.suppress_from_replies(conn, [stranger], now=NOW, cfg=CFG)["suppressed"] == 0


def test_a_reply_older_than_our_first_email_is_not_an_answer_to_it(conn):
    mailed(conn)
    old = reply("maya@co1.example", text="Hello", at=NOW - timedelta(days=30))
    assert outreach_send.suppress_from_replies(conn, [old], now=NOW, cfg=CFG)["suppressed"] == 0


def test_suppressing_the_same_rows_again_changes_nothing(conn):
    mailed(conn)
    rows = [reply("maya@co1.example", text="No.")]
    outreach_send.suppress_from_replies(conn, rows, now=NOW, cfg=CFG)
    again = outreach_send.suppress_from_replies(conn, rows, now=NOW + timedelta(hours=1), cfg=CFG)
    assert again["suppressed"] == 0 and conn.execute("SELECT count(*), min(added_at) FROM core.suppression").fetchone() == (1, NOW)


def test_the_rows_the_application_email_reader_returns_are_accepted(conn):
    """read_mailbox's dicts (message_id, received_at, sender, subject, to, kind, company, title; `text` for a leftover) and
    core.application_email's (no text) both work: the sender and subject alone suppress a reply to our thread."""
    mailed(conn)
    row = {"message_id": "<r1@x>", "received_at": NOW, "sender": "Maya <maya@co1.example>", "subject": "Re: Late stock data",
           "to": MAILBOXES[0], "kind": "other", "company": None, "title": None}
    assert outreach_send.suppress_from_replies(conn, [row], now=NOW, cfg=CFG)["suppressed"] == 1


# --------------------------------------------------------------------------------------------------------- mailbox health

def addr(mailbox, i):
    return f"p{i}@{mailbox.split('@')[0].replace('.', '-')}.example"


def live_history(conn, mailbox, n):
    for i in range(n):
        seed(conn, mailbox, addr(mailbox, i), NOW - timedelta(days=3, minutes=i), mode="live")


def bounce_of(address, mailbox):
    return reply("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", f"wasn't delivered to {address}",
                 mailbox=mailbox)


def paused(conn, now=NOW):
    return outreach_send.paused_mailboxes(conn, now, CFG)


def test_a_bounce_rate_over_the_limit_pauses_that_mailbox_for_7_days(conn, pushes):
    live_history(conn, MAILBOXES[0], 20)
    live_history(conn, MAILBOXES[1], 20)
    out = outreach_send.suppress_from_replies(conn, [bounce_of(addr(MAILBOXES[0], 0), MAILBOXES[0])], now=NOW, cfg=CFG)
    assert out["paused"] == [MAILBOXES[0]]                    # 1 of 20 recipients = 5% > 3%
    assert set(paused(conn)) == {MAILBOXES[0]} and paused(conn)[MAILBOXES[0]] == NOW + timedelta(days=7)
    assert MAILBOXES[0] not in paused(conn, NOW + timedelta(days=7, minutes=1))
    assert len(pushes) == 1 and "pause" in pushes[0][0].lower()
    outreach_send.suppress_from_replies(conn, [bounce_of(addr(MAILBOXES[0], 0), MAILBOXES[0])], now=NOW, cfg=CFG)
    assert conn.execute("SELECT count(*) FROM core.outreach_pause").fetchone() == (1,) and len(pushes) == 1


def test_a_bounce_rate_at_or_under_the_limit_pauses_nothing(conn, pushes):
    live_history(conn, MAILBOXES[0], 40)
    out = outreach_send.suppress_from_replies(conn, [bounce_of(addr(MAILBOXES[0], 0), MAILBOXES[0])], now=NOW, cfg=CFG)
    assert out["bounces"] == 1 and out["paused"] == [] and paused(conn) == {} and pushes == []   # 2.5%


def test_a_spam_complaint_pauses_the_mailbox_at_once(conn, pushes):
    live_history(conn, MAILBOXES[1], 100)
    complaint = reply("complaints@abuse.example", "Abuse report: spam complaint",
                      f"The recipient {addr(MAILBOXES[1], 3)} reported your message as spam.", mailbox=MAILBOXES[1])
    out = outreach_send.suppress_from_replies(conn, [complaint], now=NOW, cfg=CFG)
    assert out["paused"] == [MAILBOXES[1]] and set(paused(conn)) == {MAILBOXES[1]}
    assert conn.execute("SELECT reason FROM core.suppression WHERE address = %s", (addr(MAILBOXES[1], 3),)).fetchone() == ("complaint",)
    assert len(pushes) == 1


def test_a_paused_mailbox_sends_nothing_and_its_volume_is_not_moved_to_the_others(conn, tmp_path):
    many(tmp_path, 30)
    conn.execute("INSERT INTO core.outreach_pause (mailbox, reason, paused_at, until_at) VALUES (%s, 'bounce rate', %s, %s)",
                 (MAILBOXES[0], NOW - timedelta(hours=1), NOW + timedelta(days=7)))
    plan = outreach_send.eligible(conn, NOW, CFG)
    assert plan and MAILBOXES[0] not in {s.mailbox for s in plan}
    # b and c wrote 7 each today: the day's 20 minus the paused mailbox's 6 is 14, so nothing is added for the paused one's share
    for i in range(7):
        seed(conn, MAILBOXES[1], f"b{i}@x.example", NOW - timedelta(hours=3, minutes=i))
        seed(conn, MAILBOXES[2], f"c{i}@x.example", NOW - timedelta(hours=3, minutes=20 + i))
    assert outreach_send.eligible(conn, NOW, CFG) == []
    assert outreach_send.eligible(conn, NOW, {**CFG, "daily_total": 40}) != []   # only the shared total stopped it


def test_a_paused_mailbox_gets_no_follow_ups_moved_to_another_mailbox(conn, tmp_path):
    many(tmp_path, 1)
    seed(conn, MAILBOXES[0], "founder0@co0.example", NOW - timedelta(days=7, hours=1))
    conn.execute("INSERT INTO core.outreach_pause (mailbox, reason, paused_at, until_at) VALUES (%s, 'spam complaint', %s, %s)",
                 (MAILBOXES[0], NOW - timedelta(days=1), NOW + timedelta(days=6)))
    assert outreach_send.eligible(conn, NOW, CFG) == []


def test_a_mailbox_is_judged_again_on_its_new_sends_after_a_pause(conn):
    live_history(conn, MAILBOXES[0], 20)
    outreach_send.suppress_from_replies(conn, [bounce_of(addr(MAILBOXES[0], 0), MAILBOXES[0])], now=NOW, cfg=CFG)
    after = NOW + timedelta(days=8)
    assert outreach_send.pause_unhealthy(conn, after, CFG) == [] and paused(conn, after) == {}


# ---------------------------------------------------------------------------------------------------------------- the email

def test_every_email_ends_with_one_plain_text_opt_out_line(conn, tmp_path):
    many(tmp_path, 1)
    [send] = outreach_send.eligible(conn, NOW, CFG)
    assert send.body.rstrip().splitlines()[-1] == outreach_send.OPT_OUT and send.body.count(outreach_send.OPT_OUT) == 1
    assert "http" not in outreach_send.OPT_OUT and "\n" not in outreach_send.OPT_OUT and "reply" in outreach_send.OPT_OUT.lower()
    message = outreach_send.compose(send, "<id@mail.example>", NOW)
    assert message.get_content().rstrip().splitlines()[-1] == outreach_send.OPT_OUT


def test_a_draft_that_already_has_the_opt_out_line_still_ends_with_it_once(conn, tmp_path):
    many(tmp_path, 1, body=BODY + "\n\n" + outreach_send.OPT_OUT)
    [send] = outreach_send.eligible(conn, NOW, CFG)
    assert send.body.count(outreach_send.OPT_OUT) == 1 and send.body.rstrip().endswith(outreach_send.OPT_OUT)


def test_the_first_email_is_plain_text_with_no_attachment_and_no_tracking_links(conn, tmp_path):
    many(tmp_path, 1)
    [send] = outreach_send.eligible(conn, NOW, CFG)
    message = outreach_send.compose(send, "<id@mail.example>", NOW)
    assert not message.is_multipart() and message.get_content_type() == "text/plain"
    assert list(message.iter_attachments()) == []
    assert "http://" not in send.body and "https://" not in send.body and "utm_" not in send.body
    assert "List-Unsubscribe" not in message and "Disposition-Notification-To" not in message and "Return-Receipt-To" not in message


@pytest.mark.parametrize("extra", [
    "See https://co0.example/case-study?utm_source=cold for the details.",
    "My CV is at http://bit.ly/abc123.",
    "I attached my CV.",
    "Please find the attachment.",
])
def test_a_first_email_with_a_link_or_an_attachment_is_dropped(conn, tmp_path, extra):
    many(tmp_path, 1, body=BODY + "\n" + extra)
    assert outreach_send.eligible(conn, NOW, CFG) == []


def test_a_draft_that_names_a_gap_is_dropped(conn, tmp_path):
    many(tmp_path, 1, body=BODY.replace("Could we talk", "I have no experience with Snowflake yet. Could we talk"))
    assert outreach_send.eligible(conn, NOW, CFG) == []
    many(tmp_path, 1, body=BODY.replace("Could we talk", "I lack Kubernetes depth. Could we talk"))
    assert outreach_send.eligible(conn, NOW, CFG) == []


@pytest.mark.parametrize("sentence", [
    "Your data matters, and so does speed — so I move fast.",                 # an em dash
    "I hope this finds you well.",
    "Let's get on a call.",
    "Honestly, the pipeline is solid.",
    "This work serves as proof of delivery.",
    "I cut the load time, ensuring reports land on time.",                          # an -ing phrase tacked on
    "It was a pivotal move for the team.",
    "Tell me about “your stack”.",                                        # curly quotes
    "Let me know if you want to talk.",
])
def test_a_draft_that_fails_the_humanizer_check_is_dropped(conn, tmp_path, sentence):
    many(tmp_path, 1, body=BODY.replace("Could we talk", f"{sentence} Could we talk"))
    assert outreach_send.eligible(conn, NOW, CFG) == []


def test_a_first_email_may_not_pose_as_a_reply(conn, tmp_path):
    many(tmp_path, 1, subject="Re: your pipeline")
    assert outreach_send.eligible(conn, NOW, CFG) == []
    assert outreach_send.draft_problems("Re: your pipeline", BODY.format(first="Maya"), follow=True) == []   # a follow-up is one


def test_an_empty_or_overlong_draft_is_dropped(conn, tmp_path):
    many(tmp_path, 1, body=" ".join(["word"] * 140))
    assert outreach_send.eligible(conn, NOW, CFG) == []
    assert outreach_send.draft_problems("", "x") and outreach_send.draft_problems("s", "")


def test_the_clean_draft_passes_every_check():
    assert outreach_send.draft_problems("Late stock data at Co0", BODY.format(first="Maya")) == []


# --------------------------------------------------------------------------------------------------------- working hours

@pytest.mark.parametrize("location, now, sends_now", [
    ("Cairo, Egypt", datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc), True),   # 12:00 Wednesday
    ("Cairo, Egypt", datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc), False),  # 20:00
    ("Cairo, Egypt", datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc), False),   # 07:00
    ("Cairo, Egypt", datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc), False),  # Friday: Egypt's weekend
    ("Cairo, Egypt", datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc), True),   # Sunday: a working day there
    ("Berlin", datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc), True),         # 11:00
    ("Berlin", datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc), False),        # Saturday
    ("New York, USA", datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc), False),  # 05:00
    ("New York, USA", datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc), True),  # 11:00
    ("Atlantis", datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc), False),      # unknown place: no way to honour their hours
    ("", datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc), False),
])
def test_nothing_goes_outside_the_recipients_working_hours(conn, tmp_path, location, now, sends_now):
    add(tmp_path, [person(1, location=location)])
    plan = outreach_send.eligible(conn, now, CFG)
    assert bool(plan and plan[0].at == now) is sends_now


# --------------------------------------------------------------------------------------------------------------- kill switch

def test_disabled_sends_nothing(conn, tmp_path):
    many(tmp_path, 3)
    cfg = {**CFG, "enabled": False}
    assert outreach_send.eligible(conn, NOW, cfg) == [] and outreach_send.run(conn, NOW, cfg) == []
    assert sends(conn) == [] and not (outreach_send.DRAFTS / "outbox").exists()


def test_the_stop_file_sends_nothing_even_when_enabled_and_live(conn, tmp_path):
    many(tmp_path, 3)
    outreach_send.STOP_FILE.write_text("stop")
    cfg = {**CFG, "mode": "live"}
    assert outreach_send.eligible(conn, NOW, cfg) == [] and outreach_send.run(conn, NOW, cfg) == []
    assert sends(conn) == [] and not (outreach_send.DRAFTS / "outbox").exists()


def test_with_no_mailbox_configured_nothing_is_sent(conn, tmp_path, monkeypatch):
    many(tmp_path, 2)
    monkeypatch.setenv("MAILBOXES", "someone@example.org:pw")   # not a Gmail account: this sender writes from Gmail only
    assert outreach_send.eligible(conn, NOW, CFG) == []


def test_eligible_is_read_only(conn, tmp_path):
    many(tmp_path, 3)
    outreach_send.eligible(conn, NOW, CFG)
    assert sends(conn) == [] and not (outreach_send.DRAFTS / "outbox").exists()


# ---------------------------------------------------------------------------------------------------------------- dry run

def test_a_dry_run_writes_the_eml_and_one_dry_run_row_and_never_calls_smtp(conn, tmp_path):
    [p] = [person(1)]
    add(tmp_path, [p])
    [send] = outreach_send.run(conn, NOW, CFG)                  # the tripwire fails the test if SMTP is touched
    eml = outreach_send.DRAFTS / "outbox" / f"{p['email']}-1.eml"
    message = message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert message["To"].endswith(f"<{p['email']}>") and message["From"].endswith(f"<{send.mailbox}>") and send.mailbox in MAILBOXES
    assert message["Subject"] == send.subject and message["Message-ID"] and not message.is_multipart()
    assert message.get_content().rstrip().splitlines()[-1] == outreach_send.OPT_OUT
    (row,) = sends(conn)
    assert row[:2] == (send.mailbox, p["email"]) and row[2] == row[3] == message["Message-ID"]   # a first email starts its thread
    assert row[5] == "dry_run" and row[6] == 0
    assert row[4] == NOW


def test_a_dry_run_follow_up_is_the_second_numbered_eml(conn, tmp_path):
    many(tmp_path, 1)
    outreach_send.run(conn, NOW, CFG)
    outreach_send.run(conn, NOW + timedelta(days=7, hours=1), CFG)
    outbox = outreach_send.DRAFTS / "outbox"
    assert sorted(f.name for f in outbox.iterdir()) == ["founder0@co0.example-1.eml", "founder0@co0.example-2.eml"]
    follow = message_from_bytes((outbox / "founder0@co0.example-2.eml").read_bytes(), policy=policy.default)
    first = message_from_bytes((outbox / "founder0@co0.example-1.eml").read_bytes(), policy=policy.default)
    assert follow["In-Reply-To"] == first["Message-ID"] and follow["From"] == first["From"] and follow["Subject"].startswith("Re: ")


def test_a_rerun_at_the_same_time_changes_nothing(conn, tmp_path):
    many(tmp_path, 3)
    outreach_send.run(conn, NOW, CFG)
    before = sends(conn), sorted(p.name for p in (outreach_send.DRAFTS / "outbox").iterdir())
    outreach_send.run(conn, NOW, CFG)
    assert (sends(conn), sorted(p.name for p in (outreach_send.DRAFTS / "outbox").iterdir())) == before


def test_a_dry_run_never_counts_against_a_later_live_run(conn, tmp_path):
    many(tmp_path, 2)
    outreach_send.run(conn, NOW, CFG)
    live = outreach_send.eligible(conn, NOW + timedelta(hours=1), {**CFG, "mode": "live"})
    assert len(live) == 2                                       # the simulated first email is not a real one


@pytest.mark.parametrize("enabled, mode", [(False, "dry_run"), (False, "live"), (True, "dry_run"), (True, ""), (True, "Live "),
                                           (True, "yes"), (True, None), ("true", "live"), (1, "live"), (None, "live")])
def test_no_smtp_unless_mode_is_live_and_enabled_is_true(conn, tmp_path, enabled, mode):
    many(tmp_path, 2)
    cfg = {**CFG, "enabled": enabled, "mode": mode}
    outreach_send.run(conn, NOW, cfg)                           # the tripwire fails the test if SMTP is touched
    assert all(r[5] == "dry_run" for r in sends(conn))


@pytest.mark.parametrize("enabled, mode", [(False, "live"), ("true", "live"), (1, "live"), (None, "live"), (True, "dry_run"),
                                           (True, "Live"), (True, None)])
def test_deliver_itself_refuses_to_send_unless_mode_is_live_and_enabled_is_true(conn, tmp_path, enabled, mode):
    """The gate is in deliver() too, not only in run(): called directly with a cfg that is not live, it writes the dry-run file."""
    many(tmp_path, 1)
    [send] = outreach_send.eligible(conn, NOW, CFG)
    assert outreach_send.deliver(conn, send, NOW, {**CFG, "enabled": enabled, "mode": mode}) is True   # the tripwire guards SMTP
    assert [r[5] for r in sends(conn)] == ["dry_run"] and (outreach_send.DRAFTS / "outbox").exists()


def test_a_send_that_may_have_gone_is_recorded_and_never_retried(conn, tmp_path, monkeypatch, capsys):
    many(tmp_path, 1)

    class Dropped:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def send_message(self, message):
            raise smtplib.SMTPServerDisconnected("dropped after DATA")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Dropped)
    [done] = outreach_send.run(conn, NOW, {**CFG, "mode": "live"})
    assert [r[5] for r in sends(conn)] == ["live"]        # the row stays: a second send would be a duplicate
    assert outreach_send.eligible(conn, NOW + timedelta(hours=1), {**CFG, "mode": "live"}) == []
    assert done.recipient not in capsys.readouterr().out


def test_live_goes_through_smtp_only_with_mode_live_and_enabled_true(conn, tmp_path, monkeypatch):
    many(tmp_path, 1)
    log = []

    class FakeSMTP:
        def __init__(self, host, port, *a, **k):
            log.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, password):
            log.append(("login", user, password))

        def send_message(self, message):
            log.append(("send", message))

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    [send] = outreach_send.run(conn, NOW, {**CFG, "mode": "live"})
    assert log[0] == ("connect", "smtp.gmail.com", 465) and log[1] == ("login", send.mailbox, PASSWORDS[send.mailbox])
    sent = log[2][1]
    assert sent["To"].endswith("<founder0@co0.example>") and list(sent.iter_attachments()) == []
    (row,) = sends(conn)
    assert row[5] == "live" and row[3] == sent["Message-ID"]
    assert not (outreach_send.DRAFTS / "outbox").exists()      # a live send writes no dry-run file


def test_a_failed_live_send_leaves_no_row_so_it_is_tried_again(conn, tmp_path, monkeypatch, capsys):
    many(tmp_path, 1)

    class Broken:
        def __init__(self, *a, **k):
            raise smtplib.SMTPServerDisconnected("down")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Broken)
    assert outreach_send.run(conn, NOW, {**CFG, "mode": "live"}) == []
    assert sends(conn) == []
    assert "founder0@co0.example" not in capsys.readouterr().out


def test_a_refused_recipient_is_suppressed_and_not_mailed_again(conn, tmp_path, monkeypatch):
    many(tmp_path, 1)

    class Refusing:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def send_message(self, message):
            raise smtplib.SMTPRecipientsRefused({"founder0@co0.example": (550, b"no such user")})

    monkeypatch.setattr(smtplib, "SMTP_SSL", Refusing)
    assert outreach_send.run(conn, NOW, {**CFG, "mode": "live"}) == []
    assert conn.execute("SELECT reason FROM core.suppression WHERE address = 'founder0@co0.example'").fetchone() == ("bounce",)
    assert outreach_send.eligible(conn, NOW + timedelta(hours=1), {**CFG, "mode": "live"}) == []


def test_nothing_printed_names_an_address_or_a_password(conn, tmp_path, capsys):
    many(tmp_path, 2)
    mailed(conn)
    outreach_send.run(conn, NOW, CFG)
    outreach_send.suppress_from_replies(conn, [reply("maya@co1.example", text="No.")], now=NOW, cfg=CFG)
    out = capsys.readouterr().out
    assert out and "@" not in out and not any(p in out for p in PASSWORDS.values())


# ------------------------------------------------------------------------------------------------------------ the reply reader

class FakeIMAP:
    """Gmail over IMAP, as much as the reader uses: one list of raw messages per account, every command recorded."""
    mail: dict[str, list[bytes]] = {}
    broken: set[str] = set()
    log: list[tuple] = []

    def __init__(self, host, *a, **k):
        self.host, self.user = host, None
        FakeIMAP.log.append(("connect", host))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        FakeIMAP.log.append(("login", user, password))
        if user in FakeIMAP.broken:
            raise OSError("down")
        self.user = user
        return "OK", [b"ok"]

    def select(self, name, readonly=False):
        FakeIMAP.log.append(("select", name, readonly))
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        FakeIMAP.log.append((cmd, *args))
        box = FakeIMAP.mail.get(self.user, [])
        if cmd == "search":      # every query finds every message: the reader must not read one twice
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(box)))]
        raw, spec = box[int(args[0]) - 1], args[1]
        if "RFC822.SIZE" in spec:
            head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            return "OK", [(f"{args[0]} (RFC822.SIZE {len(raw) if b'HUGE' not in raw else 9_000_000} BODY[HEADER.FIELDS]".encode(), head), b")"]
        return "OK", [(f"{args[0]} (BODY[] {len(raw)}".encode(), raw), b")"]


@pytest.fixture
def imap(monkeypatch):
    FakeIMAP.mail, FakeIMAP.broken, FakeIMAP.log = {}, set(), []
    monkeypatch.setattr(outreach_send.imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


def raw_mail(sender, subject, text, to=MAILBOXES[0], message_id="<m1@x>", date="Wed, 11 Nov 2026 09:30:00 +0000", html=None):
    parts = [f"From: {sender}", f"To: {to}", f"Subject: {subject}", f"Message-ID: {message_id}", f"Date: {date}", "MIME-Version: 1.0"]
    if html:
        body = f"Content-Type: text/html; charset=utf-8\r\n\r\n{html}"
    else:
        body = f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}"
    return ("\r\n".join(parts) + "\r\n" + body).encode()


def commands(imap):
    return [entry[0] for entry in imap.log]


def test_the_reader_opens_all_mail_read_only_and_only_peeks(conn, imap):
    mailed(conn)
    imap.mail[MAILBOXES[0]] = [raw_mail("Maya <maya@co1.example>", "Re: Late stock data", "No thanks.")]
    outreach_send.read_replies(conn, NOW, CFG)
    assert ("connect", "imap.gmail.com") in imap.log and ("login", MAILBOXES[0], PASSWORDS[MAILBOXES[0]]) in imap.log
    assert ('select', '"[Gmail]/All Mail"', True) in imap.log
    assert set(commands(imap)) <= {"connect", "login", "select", "search", "fetch"}      # never store, copy, expunge or append
    for entry in imap.log:
        if entry[0] == "fetch":
            assert "PEEK" in entry[2]                                                    # nothing is marked read


def test_the_reader_searches_for_mail_from_the_people_we_mailed_and_for_bounces(conn, imap):
    for i in range(45):
        mailed(conn, f"p{i}@co{i}.example", when=NOW - timedelta(days=2, minutes=i))
    imap.mail[MAILBOXES[0]] = []
    outreach_send.read_replies(conn, NOW, CFG)
    queries = sorted({e[3] for e in imap.log if e[0] == "search"})   # the same searches in each of the three mailboxes
    from_queries = [q for q in queries if "mailer-daemon" not in q]
    assert len(from_queries) == 3 and all(q.count("@") <= 20 for q in from_queries)    # 45 addresses, 20 to a query
    assert {f"p{i}@co{i}.example" for i in range(45)} == {a for q in from_queries for a in re.findall(r"p\d+@co\d+\.example", q)}
    assert any("mailer-daemon" in q and "postmaster" in q for q in queries)
    assert all("newer_than:" in q for q in queries)


def test_the_reader_looks_back_to_the_first_email_sent_and_no_further_than_60_days(conn, imap):
    mailed(conn, when=NOW - timedelta(days=10))
    outreach_send.read_replies(conn, NOW, CFG)
    assert any("newer_than:12d" in e[3] for e in imap.log if e[0] == "search")
    imap.log.clear()
    mailed(conn, "old@co9.example", when=NOW - timedelta(days=400))
    outreach_send.read_replies(conn, NOW, CFG)
    assert any("newer_than:60d" in e[3] for e in imap.log if e[0] == "search")


def test_with_nobody_mailed_the_reader_does_not_connect(conn, imap):
    assert outreach_send.read_replies(conn, NOW, CFG) == [] and imap.log == []


def test_a_row_has_the_shape_suppress_from_replies_reads(conn, imap):
    mailed(conn)
    imap.mail[MAILBOXES[0]] = [raw_mail("Maya Founder <maya@co1.example>", "Re: Late stock data",
                                        "No thanks.\n\nOn Wed, 11 Nov 2026, Omar wrote:\n> reply \"no\" and I will not write again.")]
    [row] = outreach_send.read_replies(conn, NOW, CFG)
    assert row["sender"] == "Maya Founder <maya@co1.example>" and row["subject"] == "Re: Late stock data"
    assert row["message_id"] == "<m1@x>" and row["mailbox"] == MAILBOXES[0] and row["kind"] == "other"
    assert row["received_at"] == datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc)
    assert row["text"].startswith("No thanks.") and "\n> reply" in row["text"]            # the lines stay lines: quotes can be told apart


def test_an_html_only_reply_is_read_as_text(conn, imap):
    mailed(conn)
    imap.mail[MAILBOXES[0]] = [raw_mail("maya@co1.example", "Re: x", "", html="<html><head><style>p{}</style></head><body><p>Please unsubscribe me.</p></body></html>")]
    [row] = outreach_send.read_replies(conn, NOW, CFG)
    assert "Please unsubscribe me." in row["text"] and "<p>" not in row["text"] and "p{}" not in row["text"]


def test_a_bounce_report_names_the_failed_address_from_its_delivery_status_part(conn, imap):
    mailed(conn)
    dsn = ("From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\nTo: " + MAILBOXES[0] + "\r\n"
           "Subject: Delivery Status Notification (Failure)\r\nMessage-ID: <b1@x>\r\nDate: Wed, 11 Nov 2026 09:31:00 +0000\r\n"
           "MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=B\r\n\r\n"
           "--B\r\nContent-Type: text/plain\r\n\r\nYour message could not be delivered.\r\n"
           "--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; googlemail.com\r\n\r\n"
           "Final-Recipient: rfc822; maya@co1.example\r\nAction: failed\r\nStatus: 5.1.1\r\n--B--\r\n").encode()
    imap.mail[MAILBOXES[0]] = [dsn]
    [row] = outreach_send.read_replies(conn, NOW, CFG)
    assert "maya@co1.example" in row["text"]
    out = outreach_send.suppress_from_replies(conn, [row], now=NOW, cfg=CFG)
    assert out["bounces"] == 1


def test_a_message_found_by_several_queries_or_in_two_mailboxes_is_one_row(conn, imap):
    mailed(conn)
    same = raw_mail("maya@co1.example", "Re: x", "Hello")
    imap.mail[MAILBOXES[0]] = [same, same]
    imap.mail[MAILBOXES[1]] = [same]
    assert len(outreach_send.read_replies(conn, NOW, CFG)) == 1


def test_mail_from_our_own_mailboxes_and_our_own_messages_is_left_out(conn, imap):
    mailed(conn)
    own_id = "<own1@mail.example>"
    conn.execute("UPDATE core.outreach_send SET message_id = %s", (own_id,))
    imap.mail[MAILBOXES[0]] = [raw_mail(f"Omar <{MAILBOXES[1]}>", "Re: x", "note to self", message_id="<self@x>"),
                                           raw_mail("maya@co1.example", "x", "copy", message_id=own_id)]
    assert outreach_send.read_replies(conn, NOW, CFG) == []


def test_a_message_over_the_size_limit_is_not_downloaded(conn, imap):
    mailed(conn)
    imap.mail[MAILBOXES[0]] = [raw_mail("maya@co1.example", "Re: x", "HUGE attachment inside")]
    assert outreach_send.read_replies(conn, NOW, CFG) == []
    assert not any(e[0] == "fetch" and "BODY.PEEK[]" in e[2] for e in imap.log)


def test_a_mailbox_that_cannot_be_read_does_not_stop_the_others_and_names_no_address(conn, imap, capsys):
    mailed(conn)
    imap.broken.add(MAILBOXES[0])
    imap.mail[MAILBOXES[1]] = [raw_mail("maya@co1.example", "Re: x", "Hello", to=MAILBOXES[1])]
    rows = outreach_send.read_replies(conn, NOW, CFG)
    assert [r["mailbox"] for r in rows] == [MAILBOXES[1]]
    out = capsys.readouterr().out
    assert "OSError" in out and "@" not in out and not any(p in out for p in PASSWORDS.values())


def test_collecting_replies_feeds_suppression_and_the_mailbox_pause(conn, imap, pushes):
    live_history(conn, MAILBOXES[0], 20)
    mailed(conn)
    imap.mail[MAILBOXES[0]] = [
        raw_mail("Maya <maya@co1.example>", "Re: Late stock data", "No thanks, not interested.", message_id="<r1@x>"),
        raw_mail("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)",
                 f"Your message wasn't delivered to {addr(MAILBOXES[0], 0)} because the address couldn't be found.", message_id="<r2@x>")]
    out = outreach_send.collect_replies(conn, NOW, CFG)
    assert out["read"] == 2 and out["suppressed"] == 2 and out["bounces"] == 1
    assert dict(conn.execute("SELECT address, reason FROM core.suppression").fetchall()) == {"maya@co1.example": "no", addr(MAILBOXES[0], 0): "bounce"}
    assert out["paused"] == [MAILBOXES[0]] and len(pushes) == 1                       # 1 bounce of 21 recipients = 4.8% > 3%
    again = outreach_send.collect_replies(conn, NOW + timedelta(minutes=5), CFG)       # the next tick reads them again: nothing changes
    assert again["suppressed"] == 0 and conn.execute("SELECT count(*) FROM core.outreach_pause").fetchone() == (1,) and len(pushes) == 1


def test_collecting_replies_touches_no_mailbox_when_outreach_is_off(conn, imap):
    mailed(conn)
    assert outreach_send.collect_replies(conn, NOW, {**CFG, "enabled": False})["read"] == 0
    assert imap.log == []


def test_collecting_replies_still_reads_when_the_stop_file_is_there(conn, imap):
    """The stop file stops sending; a reply or an unsubscribe that arrives meanwhile must still be honoured."""
    mailed(conn)
    outreach_send.STOP_FILE.write_text("stop")
    imap.mail[MAILBOXES[0]] = [raw_mail("maya@co1.example", "Re: x", "Please unsubscribe me.")]
    assert outreach_send.collect_replies(conn, NOW, CFG)["suppressed"] == 1


def test_collecting_prints_counts_only(conn, imap, capsys):
    mailed(conn)
    imap.mail[MAILBOXES[0]] = [raw_mail("maya@co1.example", "Re: x", "No.")]
    outreach_send.collect_replies(conn, NOW, CFG)
    out = capsys.readouterr().out
    assert out and "@" not in out and not any(p in out for p in PASSWORDS.values())
