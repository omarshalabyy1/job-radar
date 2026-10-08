"""The job sources. Each returns rows for raw.job_posting, kept to the roles and places in scope.

Every public page is read, whatever a site's robots.txt or terms say (your rule, 2026-10-05: you
take the risk), as a browser asks for it: a site that turns requests away is asked again with a
real Chrome's TLS handshake (request()). The code never logs in and never gets past a bot check: a
site that shows one is opened by you once (scripts/open_blocked.py) and read with your saved
session from then on. Every source stays inside its limits, so no board has a reason to block the
laptop's internet address: see fetch() (a host that answers 429 is left alone for as long as it
asks) and the README's limits. LinkedIn is never opened from here: its jobs come from your alert emails
and from an Apify Actor (linkedin()).

One module per group (docs/architecture.md, the mind map): base (the request door every source uses),
boards, egypt, gulf, remote, startups, companies, inbox."""

from .base import ERRORS, Challenge, Held, fetch, time_left
from .boards import bayt, freehire, indeed, jooble, linkedin, workable_jobs
from .egypt import tanqeeb, wuzzuf
from .gulf import dubizzle, gulftalent, naukrigulf
from .remote import (arbeitnow, dailyremote, himalayas, jobicy, relomote, remoteco, remoteok, remotive,
                     weworkremotely, workingnomads)
from .startups import builtin, startup_jobs, welcometothejungle, wellfound, ycombinator
from .companies import company_portals, company_sites
from .inbox import LINKEDIN_ALERTS, mailboxes

__all__ = ["ERRORS", "Challenge", "Held", "fetch", "time_left", "bayt", "freehire", "indeed", "jooble", "linkedin",
           "workable_jobs", "tanqeeb", "wuzzuf", "dubizzle", "gulftalent", "naukrigulf", "arbeitnow",
           "dailyremote", "himalayas", "jobicy", "relomote", "remoteco", "remoteok", "remotive",
           "weworkremotely", "workingnomads", "builtin", "startup_jobs", "welcometothejungle", "wellfound",
           "ycombinator", "company_portals", "company_sites", "LINKEDIN_ALERTS",
           "mailboxes"]
