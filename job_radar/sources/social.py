"""Social hiring posts: X and Reddit through Apify (sources/apify.py runs the Actor, this module reads what it gives back).

An Actor's dataset item is a *post*, not a job. A post becomes a posting only when ALL of these hold, checked in this order,
and each post that fails is counted under the first reason it fails (the run log says how many of each):

  not a post       a comment, a "no results" placeholder, an item with no text or no link
  retweet          X: isRetweet, or text starting "RT @"
  removed          Reddit: a moderator's or Reddit's removal, or a body that is "[removed]" or "[deleted]"
  stale / no date  older than the board's fresh_days (3), or no date to judge by
  job seeker       "looking for a job", "open to work", "[FOR HIRE]", "أبحث عن عمل" ... unless the post also speaks as an
                   employer ("we're hiring", "join our team", "apply here"): a recruiter's hook "Looking for a new job?" is not one
  not hiring       nothing in it says someone is hiring (hiring, vacancy, we are looking for, [HIRING], مطلوب, تعلن ...)
  no role          none of your roles (settings.yaml roles) is named: the title is that role, with Senior/Junior in front
  no way to apply  no company, no apply link and no contact (an email, a phone number, "DM")
  duplicate        the same text again, here or in an earlier run, under another link: the earliest post is kept

A kept post is a wide posting (steps.load, wide=True) whose source is 'apify:x' or 'apify:reddit', whose job_url (its natural
key) is the post's own link, whose title is the role's English name, and whose payload gives apply_link (the link in the post;
steps.apply_url hands it on), contact, posted_at and is_remote. Where the post says remote the posting is searched_for
"Remote"; where it names a place of the home country, "Egypt": transform then makes the same role, place and reach checks as for
any board, and the existing Egypt-eligibility rule is untouched.

Post text is data. Nothing here sends it to a model, opens a link in it or reads a word in it as a command: it is matched by
regular expressions, and what a match yields (a role from your settings, a place, a link that must be plain http or https) is
the only thing it can change. A post saying "ignore your rules" is the same posting as without that sentence; it only stays in
the description, as text. A post that makes this code raise is dropped as unreadable, never the run."""

from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

from dateutil.parser import isoparse

from ..config import HOME_AREAS, NOT_REMOTE, PLACE_PATTERNS, REMOTE, SETTINGS, role_of
from .base import row

DESCRIPTION_CHARS = 4000  # what the radar keeps of a post's text
ANALYSED_CHARS = 6000     # how much of a post is read at all


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---- the roles: what a post says -> the title it becomes ---------------------------------------------------------------

NOUN = r"(?:engineer|developer|analyst|scientist|specialist|consultant)"
ACRONYMS = {"ai": "AI", "ml": "ML", "bi": "BI", "etl": "ETL", "elt": "ELT", "sql": "SQL", "nlp": "NLP", "llm": "LLM",
            "genai": "GenAI", "mis": "MIS", "dwh": "DWH", "ssis": "SSIS", "ssrs": "SSRS", "mlops": "MLOps"}
# variants posts use that the search words do not list; each is kept only if role_of takes it as a title
EXTRA_PHRASES = ["ML Engineer", "AI/ML Engineer", "BI Engineer", "BI Analyst", "Data Platform Engineer",
                 "Machine Learning Scientist", "AI Specialist"]
SENIORITY = {"senior": "Senior", "sr": "Senior", "junior": "Junior", "jr": "Junior", "lead": "Lead", "principal": "Principal",
             "staff": "Staff"}
# Arabic role names -> the English title: the radar's own role patterns, emails and tracker are English
ARABIC_ROLES = [(r"مهندس[ةه]?\s+(?:ال)?بيانات", "Data Engineer"),
                (r"(?:محلل[ةه]?|(?:أخصائي|اخصائي|متخصص)\s+تحليل)\s+(?:ال)?بيانات", "Data Analyst"),
                (r"عالم[ةه]?\s+(?:ال)?بيانات", "Data Scientist"),
                (r"مهندس[ةه]?\s+(?:ال)?ذكاء\s+(?:ال)?اصطناعي", "AI Engineer"),
                (r"مطور[ةه]?\s+(?:ال)?ذكاء\s+(?:ال)?اصطناعي", "AI Developer"),
                (r"مهندس[ةه]?\s+تعلم\s+(?:ال)?آلة", "Machine Learning Engineer"),
                (r"(?:محلل[ةه]?|مطور[ةه]?|مهندس[ةه]?)\s+ذكاء\s+(?:ال)?[أا]عمال", "BI Developer")]


def pretty(phrase: str) -> str:
    return phrase if not phrase.islower() else " ".join(ACRONYMS.get(w, w.capitalize()) for w in phrase.split())


def role_patterns() -> list[tuple[re.Pattern, str, bool]]:
    """(pattern, title, takes_a_noun) for each role phrase of settings.yaml roles (their search words and the multi-word
    title words), longest first. A phrase that is already a title ends in a role noun ('Data Engineer'); one that is not
    ('Power BI', 'Big Data') is a role only with a noun after it ('Power BI Developer'). Phrases role_of would not take
    as a title are left out, so a post is never given a role the rest of the pipeline would not read as one."""
    phrases: dict[str, str] = {}
    for role in SETTINGS["roles"]:
        for term in role["search"]:
            phrases.setdefault(term.lower(), term)
        for group in role["title_needs"]:
            for word in group:
                word = str(word).rstrip("*").strip()
                if " " in word and word.isascii():
                    phrases.setdefault(word.lower(), pretty(word))
    for term in EXTRA_PHRASES:
        phrases.setdefault(term.lower(), term)
    out = []
    for phrase in sorted(phrases.values(), key=len, reverse=True):
        if not phrase.isascii():
            continue
        complete = re.search(rf"{NOUN}$", phrase, re.I) is not None
        if role_of(phrase if complete else f"{phrase} Engineer") is None:
            continue
        pattern = re.escape(phrase).replace(r"\ ", r"\s+").replace("/", r"\s*/\s*")
        out.append((re.compile(rf"(?<![\w-])(?:(?P<level>senior|sr\.?|junior|jr\.?|lead|principal|staff)\s+)?(?P<role>{pattern})(?:s)?"
                               rf"(?P<noun>\s+{NOUN})?(?![\w])", re.I), phrase, not complete))
    return out


ROLE_PATTERNS = role_patterns()
ARABIC_PATTERNS = [(re.compile(pattern), title) for pattern, title in ARABIC_ROLES if role_of(title) is not None]


def role_in(text: str) -> tuple[str, int] | None:
    """The title the first role named in the text gives, and where it stands; None when no role of yours is named."""
    found: list[tuple[int, int, str]] = []  # (position, -length, title)
    for pattern, phrase, needs_noun in ROLE_PATTERNS:
        for m in pattern.finditer(text):
            noun = (m.group("noun") or "").strip()
            if needs_noun and not noun:
                continue
            title = phrase if not needs_noun else f"{phrase} {noun.capitalize()}"
            level = SENIORITY.get((m.group("level") or "").lower().rstrip("."))
            title = f"{level} {title}" if level else title
            if role_of(title) is not None:
                found.append((m.start(), -len(m.group(0)), title))
    for pattern, title in ARABIC_PATTERNS:
        if m := pattern.search(text):
            found.append((m.start(), -len(m.group(0)), title))
    if not found:
        return None
    position, _, title = min(found)
    return title, position


# ---- what a post says about itself --------------------------------------------------------------------------------------

EMPLOYER_VOICE = re.compile(
    r"\bwe(?:'re|’re| are)\b|\bour (?:team|company|client|startup|office)\b|\bjoin (?:our|us)\b|\b(?:is|are) (?:now |currently |actively )?hiring\b"
    r"|\bwe(?:'re|’re| are)? hiring\b|\bnow hiring\b|\bapply (?:now|here|today|at|via|through|below)\b|\bvacanc(?:y|ies)\b"
    r"|\bsend (?:your |us your )?(?:cv|resume)\b|مطلوب|تعلن\b|نبحث عن", re.I)
SEEKER = re.compile(
    r"\bopen to work\b|#opentowork\b|\bfor hire\b|\bhire me\b|\bavailable for (?:hire|work|freelance|new|projects)\b"
    r"|\b(?:looking|searching|seeking|hunting|applying)\s+(?:for\s+)?(?:a\s+|an\s+|my\s+)?"
    r"(?:(?:new|next|full[- ]time|remote|part[- ]time)\s+)*(?:jobs?|work|roles?|positions?|opportunit\w+|gigs?|internships?|employment)\b"
    r"|\bjob[- ]?seeker\b|\bin search of (?:a )?(?:job|work|role|position|opportunit\w+)\b"
    r"|[أا]بحث\s+عن\s+(?:عمل|وظيف\w*|فرص\w*|شغل)|باحث[ةه]?\s+عن\s+(?:عمل|وظيف\w*|فرص\w*)|متاح\s+للعمل"
    r"|محتاج\s+(?:شغل|وظيف\w*)|عايز\s+(?:شغل|وظيف\w*)", re.I)
HIRING = re.compile(
    r"\bhiring\b|\bnow recruiting\b|\bvacanc(?:y|ies)\b|\bjob openings?\b|\bopen (?:role|position)s?\b|\bjoin (?:our|my|the) [^.\n]{0,30}team\b"
    r"|\bjoin us\b|\b(?:we|our team|company|client)\b[^.\n]{0,25}\b(?:looking for|seeking|recruiting|searching for)\b"
    r"|\b(?:is|are) looking for\b|\bwe need (?:a|an)\b|\bapply (?:now|here|today|via|at|through|on|below)\b"
    r"|\bsend (?:your |us your )?(?:cv|resume)\b|\[hiring\]|"
    r"مطلوب|تعلن\b|نبحث عن|فرصة عمل|فرص عمل|وظيفة شاغرة|وظائف شاغرة|وظائف متاحة|يعلن\b|بحاجة (?:إلى|الى)|تطلب\b|للتقديم|التقديم على", re.I)
WE_VOICE = re.compile(r"\b(?:we|we're|we’re|our|join us|join our)\b", re.I)


def seeker(signal: str) -> bool:
    return SEEKER.search(signal) is not None and EMPLOYER_VOICE.search(signal) is None


def hiring(signal: str, flair: str = "") -> bool:
    return HIRING.search(signal) is not None or flair.strip().lower() == "hiring"


# ---- links and contacts --------------------------------------------------------------------------------------------------

URL = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
MARKDOWN_LINK = re.compile(r"\]\((https?://[^\s)]+)\)", re.I)
TRACKING = {"fbclid", "gclid", "ref", "ref_src", "ref_url", "src", "source", "igshid", "mc_cid", "mc_eid", "s", "t", "si"}
SOCIAL_HOSTS = ("x.com", "twitter.com", "t.co", "reddit.com", "redd.it", "imgur.com", "giphy.com", "youtube.com", "youtu.be")
APPLY_HINT = re.compile(r"apply|career|job|vacanc|recruit|hiring|forms?\.|typeform|workable|lever\.co|greenhouse|ashbyhq|"
                        r"smartrecruiters|recruitee|bamboohr|linkedin\.com/jobs", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?20[- ]?|0)1[0125][- ]?\d{4}[- ]?\d{4}(?!\d)")
DM = re.compile(r"\bdm\b|direct message|message (?:me|us)\b|whats?app|wa\.me|واتساب|واتس اب|راسلنا|ابعتلنا", re.I)


def safe_url(url) -> str | None:
    """A link that is plain http or https with a real host, without a password or user name in it, a tracking query or a
    fragment, at most 500 characters; None for anything else (javascript:, file:, data:, a bare word)."""
    if not isinstance(url, str):
        return None
    url = url.strip().rstrip(".,;:!?\"'>)]")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or "." not in host or len(url) > 500 or re.search(r"\s", url):
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    netloc = host + (f":{port}" if port else "")
    return parts._replace(netloc=netloc, query=urlencode(query), fragment="").geturl()


def is_social(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


def apply_link(links: list[str]) -> str | None:
    """The link in a post that most likely applies: one that looks like a careers, job or form page, else the first link out of
    the platform (a link back to X or Reddit is the post itself, not a way to apply)."""
    out = [u for u in dict.fromkeys(safe_url(u) for u in links) if u and not is_social(u)]
    return next((u for u in out if APPLY_HINT.search(u)), out[0] if out else None)


def contact_in(text: str) -> str | None:
    if m := EMAIL.search(text):
        return m.group(0)
    if m := PHONE.search(text):
        return m.group(0)
    return "dm" if DM.search(text) else None


# ---- company and place ---------------------------------------------------------------------------------------------------

TOKEN = r"[A-Z][\w'’&-]*(?:\.[\w'’&-]+)*"
NAME = rf"{TOKEN}(?:\s+(?:{TOKEN}|\d+)){{0,4}}"
ARABIC_NAME = r"[؀-ۿ0-9A-Za-z&'’.-]+(?:\s+[؀-ۿ0-9A-Za-z&'’.-]+){0,3}?"
COMPANY_PATTERNS = [
    re.compile(rf"(?P<n>{NAME})\s+(?i:is|are)\s+(?i:(?:now|currently|actively|also)\s+)?(?i:hiring|looking\s+for|seeking|recruiting)"),
    re.compile(rf"\b(?i:at)\s+@?(?P<n>{NAME})"),
    re.compile(rf"\b(?i:join)\s+@?(?P<n>{NAME})(?=\s+(?i:as|and|today|now)\b|[.,!:\-–—\n]|$)"),
    re.compile(r"(?i:company|employer|organi[sz]ation)\s*[:\-–]\s*(?P<n>[^\n.|,;]{2,60})"),
    re.compile(rf"شركة\s+(?P<n>{ARABIC_NAME})(?=\s*[-–—.,،:|!؟?\n]|\s+(?:تعلن|تطلب|تبحث|مطلوب|في|بـ|للعمل|عن)\b|\s*$)"),
    re.compile(r"(?:الشركة|المؤسسة)\s*[:\-–]\s*(?P<n>[^\n.|،,;]{2,60})"),
]
NOT_A_COMPANY = {"we", "i", "our", "you", "the", "a", "an", "this", "it", "they", "hiring", "remote", "egypt", "cairo", "giza",
                 "alexandria", "linkedin", "twitter", "x", "reddit", "dm", "apply", "job", "jobs", "team", "senior", "junior",
                 "hybrid", "onsite", "full", "part", "immediate", "urgent", "apply now", "mena", "emea", "gcc", "usa", "uk",
                 "مصر", "القاهرة", "الجيزة"}
CITY = {"القاهرة": "Cairo", "الجيزة": "Giza", "الإسكندرية": "Alexandria"}


def valid_company(name: str) -> str | None:
    name = name.strip().strip(".,;:!-–—'’\"()[]").strip()
    low = name.lower()
    if not 2 <= len(name) <= 60 or low in NOT_A_COMPANY or re.search(r"https?:|@|#", name):
        return None
    if role_of(name) is not None or re.search(rf"\b{NOUN}s?\b", name, re.I) or role_in(name) or HOME_AREAS.fullmatch(name):
        return None  # a role, or a place, is not who is hiring
    if any(p.fullmatch(name) for p in PLACE_PATTERNS.values()):
        return None
    return name


def company_in(text: str, author_name: str = "", kind: str = "") -> tuple[str, str]:
    """(company, where it came from): named in the text, else, for an X post in the company's own voice ('we', 'our'), the
    account's display name; else ('', ''). A Reddit user name is never a company."""
    for pattern in COMPANY_PATTERNS:
        for m in pattern.finditer(text):
            if name := valid_company(m.group("n")):
                return name, "text"
    if kind == "x" and author_name and WE_VOICE.search(text) and (name := valid_company(author_name)):
        return name, "author"
    return "", ""


def where(text: str) -> tuple[str | None, str]:
    """(searched_for, location) as the post says: Remote (worldwide when it says so) unless it also says hybrid or onsite; else
    a city of the home country with ', Egypt'; else Egypt alone; else the first other place it names, else nothing."""
    if re.search(REMOTE, text, re.I) and not re.search(NOT_REMOTE, text, re.I):
        return "Remote", "Remote, Worldwide" if re.search(r"worldwide|anywhere|global", text, re.I) else "Remote"
    if m := HOME_AREAS.search(text):
        city = m.group(0).strip()
        return "Egypt", f"{CITY.get(city, city.title() if city.isascii() else city)}, Egypt"
    if re.search(r"\begypt\b|مصر", text, re.I):
        return "Egypt", "Egypt"
    for pattern in PLACE_PATTERNS.values():
        if m := pattern.search(text):
            return None, m.group(0).strip().title()
    return None, ""


# ---- one post ---------------------------------------------------------------------------------------------------------------

class Post:
    """What the two Actors' items have in common."""

    def __init__(self, kind: str, item: dict):
        self.kind, self.item = kind, item
        self.ok, self.retweet, self.removed = True, False, False
        self.id = str(item.get("id") or item.get("parsedId") or "")
        self.flair = str(item.get("flair") or "")
        links: list[str] = []
        if kind == "x":
            text = item.get("fullText") or item.get("text")
            self.ok = isinstance(text, str) and bool(text.strip()) and item.get("type", "tweet") == "tweet"
            text = text if isinstance(text, str) else ""
            for u in (item.get("entities") or {}).get("urls") or []:
                if isinstance(u, dict):
                    expanded = u.get("expanded_url") if isinstance(u.get("expanded_url"), str) else ""
                    if isinstance(u.get("url"), str) and u["url"]:
                        text = text.replace(u["url"], expanded if safe_url(expanded) else "")
                    links.append(expanded)
            text = re.sub(r"https?://t\.co/\w+", "", text)
            links += URL.findall(text)
            self.retweet = item.get("isRetweet") is True or bool(re.match(r"\s*RT @", text)) or bool(item.get("retweeted_tweet"))
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            self.handle, self.author_name = str(author.get("userName") or ""), str(author.get("name") or "")
            self.url = safe_url(item.get("url") or item.get("twitterUrl"))
            self.created = parse_time(item.get("createdAt"))
        else:
            self.ok = item.get("dataType", "post") == "post" and isinstance(item.get("title"), str) and bool(item["title"].strip())
            title = str(item.get("title") or "").strip()
            body = item.get("body") if isinstance(item.get("body"), str) else ""
            self.removed = (item.get("removalStatus") not in (None, "", "active")
                            or body.strip().lower() in ("[removed]", "[deleted]"))
            text = f"{title}\n\n{body}".strip()
            if isinstance(item.get("contentUrl"), str):
                links.append(item["contentUrl"])
            links += MARKDOWN_LINK.findall(body) + URL.findall(body)
            self.handle, self.author_name = str(item.get("authorName") or ""), ""
            self.url = safe_url(item.get("postUrl") or item.get("url"))
            self.created = parse_time(item.get("createdAt"))
        self.ok = self.ok and self.url is not None
        self.text = re.sub(r"\n{3,}", "\n\n", text.replace("\x00", "")).strip()[:ANALYSED_CHARS]
        self.links = links


def parse_time(value) -> datetime | None:
    """X's 'Thu Oct 29 14:10:49 +0000 2026' or an ISO time, in UTC; None when there is none."""
    if not isinstance(value, str) or not value.strip():
        return None
    for read in (lambda v: datetime.strptime(v, "%a %b %d %H:%M:%S %z %Y"), isoparse):
        try:
            t = read(value.strip())
            return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            continue
    return None


def text_hash(text: str) -> str:
    """The same words however they are dressed: without links, @mentions, #tags, RT, punctuation, case or spacing."""
    t = URL.sub(" ", text.lower())
    t = re.sub(r"[@#]\w+", " ", t)
    t = re.sub(r"\brt\b", " ", t)
    t = re.sub(r"[^\w\s]|_", " ", t)
    return hashlib.sha1(" ".join(t.split()).encode("utf-8")).hexdigest()[:16]


def reason_to_drop(post: Post, fresh_days: int, now: datetime) -> str | None:
    """The first rule the post fails (the docstring above, in order), or None."""
    if not post.ok:
        return "not a post"
    if post.retweet:
        return "retweet"
    if post.removed:
        return "removed"
    if post.created is None:
        return "no date"
    if post.created < now - timedelta(days=fresh_days):
        return "stale"
    signal = f"{post.flair}\n{post.text}"
    if seeker(signal):
        return "job seeker"
    if not hiring(post.text, post.flair):
        return "not hiring"
    if role_in(post.text) is None:
        return "no role"
    return None


def posting(post: Post) -> dict | None:
    """The wide posting of a post that passed every rule, or None when it has no way to apply."""
    title, _ = role_in(post.text)
    company, company_from = company_in(post.text, post.author_name, post.kind)
    link, contact = apply_link(post.links), contact_in(post.text)
    if not (company or link or contact):
        return None
    searched_for, location = where(post.text)
    description = post.text[:DESCRIPTION_CHARS]
    payload = {"social": post.kind, "post_id": post.id, "author": post.handle, "author_name": post.author_name,
               "posted_at": post.created.isoformat(), "apply_link": link, "contact": contact, "company_from": company_from or None,
               "text_hash": text_hash(post.text), "is_remote": searched_for == "Remote"}
    if post.kind == "reddit":
        payload["community"] = post.item.get("parsedCommunityName") or post.item.get("communityName")
        payload["flair"] = post.flair or None
    return row(f"apify:{post.kind}", searched_for, title[:200], company, location, post.url, post.created.date().isoformat(),
               description, payload)


def posts(kind: str, items: list[dict], *, fresh_days: int = 3, now: datetime | None = None,
          seen: dict[str, set[str]] | None = None) -> tuple[list[dict], Counter]:
    """(the postings of the hiring posts among an Actor's items, how many posts were dropped for each reason).
    `seen` maps the text hash of posts stored by earlier runs to their links: a post whose text is there under another link
    is a repost; one under its own link is only read again, and kept so its last_seen moves."""
    now, seen = now or utcnow(), seen or {}
    dropped: Counter = Counter()
    candidates: list[tuple[int, datetime, dict]] = []
    for n, item in enumerate(items):
        try:
            post = Post(kind, item) if isinstance(item, dict) else None
            if post is None:
                dropped["not a post"] += 1
                continue
            if reason := reason_to_drop(post, fresh_days, now):
                dropped[reason] += 1
                continue
            if (found := posting(post)) is None:
                dropped["no way to apply"] += 1
                continue
            candidates.append((n, post.created, found))
        except Exception as e:  # hostile or odd data must cost one post, never the paid run
            dropped["unreadable"] += 1
            print(f"WARNING social {kind}: item {n} unreadable ({type(e).__name__}), dropped"[:200])
    groups: dict[str, list[tuple[int, datetime, dict]]] = {}
    for candidate in candidates:
        groups.setdefault(candidate[2]["payload"]["text_hash"], []).append(candidate)
    kept: list[tuple[int, dict]] = []
    for digest, group in groups.items():
        if digest in seen:
            survivors = [c for c in group if c[2]["job_url"] in seen[digest]]
        else:
            survivors = [min(group, key=lambda c: (c[1], c[0]))]
        if len(group) > len(survivors):  # not `+= 0`: a Counter would keep a zero entry and the log would say "duplicate 0"
            dropped["duplicate"] += len(group) - len(survivors)
        kept += [(c[0], c[2]) for c in survivors]
    return [found for _, found in sorted(kept, key=lambda k: k[0])], dropped


# ---- what the Actor is asked ---------------------------------------------------------------------------------------------

def prepare_input(kind: str, payload: dict, fresh_days: int, today: date) -> dict:
    """The Actor's input with the freshness window put in as of today (the settings hold none, so it never goes stale):
    X's search terms get ' since:YYYY-MM-DD' (a term that names its own start keeps it); Reddit gets postedAfter '<n> days',
    newest first, posts only. The settings' own input is not changed."""
    sent = copy.deepcopy(payload)
    if kind == "x":
        since = (today - timedelta(days=fresh_days)).isoformat()
        sent["searchTerms"] = [t if "since:" in t else f"{t} since:{since}" for t in sent.get("searchTerms") or []]
    else:
        sent.setdefault("postedAfter", f"{fresh_days} days")
        sent.setdefault("searchSort", "new")
        sent.setdefault("searchPosts", True)
    return sent
