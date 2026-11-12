"""A NaukriGulf recommended-jobs email puts the location two lines under the title ("Company", "1 - 6 Years", "Cairo - Egypt"). It was read
as no location, so 55 of 85 email postings became "Unknown location" and transform dropped them as not in reach (audit 2026-11-10)."""

from job_radar.sources.inbox import jobs_in_email
from tests.test_steps import conn  # noqa: F401  (the rolled-back warehouse connection)

CARD = ('<a href="https://www.naukrigulf.com/job/{n}"><div>{title}</div><div>{company}</div><div>1 - 6 Years</div>'
        '<div>{location}</div><div>5 Oct</div></a>')  # the whole card is one link, as in the real email (checked 2026-11-10)


def test_the_location_below_the_experience_line_is_read():
    page = "<html><body>" + CARD.format(n=1, title="Data Engineer", company="Nawy", location="Cairo - Egypt") + \
        CARD.format(n=2, title="Data Analyst", company="Salt", location="Riyadh - Saudi Arabia") + "</body></html>"
    jobs = jobs_in_email(page)
    assert [(t, c, loc) for t, c, loc, _ in jobs] == [("Data Engineer", "Nawy", "Cairo - Egypt"),
                                                      ("Data Analyst", "Salt", "Riyadh - Saudi Arabia")]


def test_a_card_with_no_place_keeps_an_empty_location_and_never_reads_the_next_card():
    page = "<html><body>" + '<a href="https://www.naukrigulf.com/job/3"><div>Data Engineer</div><div>Nawy</div><div>1 - 6 Years</div></a>' + \
        CARD.format(n=4, title="Data Analyst", company="Salt", location="Cairo - Egypt") + "</body></html>"
    jobs = jobs_in_email(page)
    assert [(t, loc) for t, _, loc, _ in jobs] == [("Data Engineer", ""), ("Data Analyst", "Cairo - Egypt")]


def test_a_posting_stored_with_no_location_gets_it_when_the_same_link_is_read_again(conn):
    from job_radar import steps
    row = {"source": "email", "searched_for": None, "title": "Data Engineer", "company": "Nawy", "location": "", "job_url": "https://t.test/loc-1",
           "date_posted": None, "description": None, "payload": {}}
    steps.load(conn, "email", [row], record=False)
    steps.load(conn, "email", [{**row, "location": "Cairo - Egypt"}], record=False)
    assert conn.execute("SELECT location FROM raw.job_posting WHERE job_url = 'https://t.test/loc-1'").fetchone()[0] == "Cairo - Egypt"
    steps.load(conn, "email", [{**row, "location": "Dubai"}], record=False)  # a location already there is kept
    assert conn.execute("SELECT location FROM raw.job_posting WHERE job_url = 'https://t.test/loc-1'").fetchone()[0] == "Cairo - Egypt"
