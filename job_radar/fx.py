"""Salaries as one comparable number: a year's pay in US dollars and euros, at the European Central Bank's rate of the
day the job was first seen. match_skills calls it.

The ECB reader and the Gulf pegs are adapted from Radarak (MIT, Copyright (c) 2026 Moamen Moussa; see
LICENSE-THIRD-PARTY). The ECB publishes its euro reference rates on working days, free and without a key; its last-90-days
file is read every run (it is small, and fills the days the laptop was off). Rates are units of a currency per 1 euro."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .sources.base import ERRORS, Held, get

ECB_90_DAYS = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
# Gulf currencies are pegged to the US dollar and the ECB does not publish them: units per 1 US dollar
PEGS = {"AED": 3.6725, "SAR": 3.75, "QAR": 3.64, "BHD": 0.376, "OMR": 0.3845, "JOD": 0.709}
# how many of a period make a year; a salary with no period is taken as yearly
PERIODS = {"year": 1, "yearly": 1, "annual": 1, "annually": 1, "month": 12, "monthly": 12, "week": 52, "weekly": 52,
           "day": 260, "daily": 260, "hour": 2080, "hourly": 2080}
PLAUSIBLE_USD = (5_000, 1_500_000)  # a year's pay outside this is kept but flagged: usually a monthly figure called yearly


def parse_ecb(xml_text: str) -> list[tuple[str, str, float]]:
    """(rate_date, currency, units_per_eur) for every day in the ECB file, the euro itself included at 1."""
    rows = []
    for day in ET.fromstring(xml_text).iter():
        if not (rate_date := day.attrib.get("time")):
            continue
        rows.append((rate_date, "EUR", 1.0))
        rows += [(rate_date, cube.attrib["currency"].upper(), float(cube.attrib["rate"]))
                 for cube in day if cube.attrib.get("currency") and cube.attrib.get("rate")]
    return rows


def load_rates(conn) -> int:
    """The ECB's last 90 days into raw.fx_rate; an ECB that does not answer is a warning (the rates stored before stay)."""
    try:
        rows = parse_ecb(get(ECB_90_DAYS).text)
    except (*ERRORS, Held, ET.ParseError) as e:
        print(f"WARNING fx: the ECB rates were not read: {e!r}"[:300])
        return 0
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO raw.fx_rate (rate_date, currency, units_per_eur) VALUES (%s, %s, %s)"
                        " ON CONFLICT (rate_date, currency) DO UPDATE SET units_per_eur = EXCLUDED.units_per_eur", rows)
    return len(rows)


def per_eur(rates: dict, day, currency: str) -> tuple[float, float] | None:
    """(units of `currency`, units of USD) per 1 euro on the last ECB day up to `day` (else the first after it), or None
    when the currency has no rate. rates: {rate_date: {currency: units_per_eur}}."""
    days = sorted(rates)
    if not days:
        return None
    on = next((d for d in reversed(days) if d <= day), days[0])
    usd = rates[on].get("USD")
    cur = rates[on].get(currency) or (PEGS[currency] * usd if currency in PEGS and usd else None)
    return (cur, usd) if cur and usd else None


def yearly(low, high, currency: str, period: str | None, day, rates: dict) -> dict | None:
    """A salary range as a year's pay: {usd_min, usd_max, eur_min, eur_max, plausible}, or None when it cannot be
    converted (no currency rate, an unknown period, no amount)."""
    times = PERIODS.get((period or "year").strip().lower())
    rate = per_eur(rates, day, (currency or "").strip().upper())
    low, high = low or high, high or low
    if not (times and rate and low):
        return None
    cur, usd = rate
    eur = (float(low) * times / cur, float(high) * times / cur)
    out = {"eur_min": round(eur[0]), "eur_max": round(eur[1]), "usd_min": round(eur[0] * usd), "usd_max": round(eur[1] * usd)}
    out["plausible"] = PLAUSIBLE_USD[0] <= (out["usd_min"] + out["usd_max"]) / 2 <= PLAUSIBLE_USD[1]
    return out


def convert(conn, since_days: int) -> int:
    """Set the yearly USD and EUR pay of the jobs first seen in the last `since_days` days that give a salary and have
    none converted yet. Returns how many were converted."""
    rates: dict = {}
    for rate_date, currency, units in conn.execute("SELECT rate_date, currency, units_per_eur FROM raw.fx_rate"):
        rates.setdefault(rate_date, {})[currency] = float(units)
    jobs = conn.execute(
        "SELECT job_id, salary_min, salary_max, salary_currency, salary_period, first_seen FROM core.job"
        " WHERE first_seen >= current_date - %s AND coalesce(salary_min, salary_max) IS NOT NULL"
        " AND salary_min_usd IS NULL", (since_days,)).fetchall()
    found = [(pay, job_id) for job_id, low, high, currency, period, day in jobs
             if (pay := yearly(low, high, currency, period, day, rates))]
    with conn.cursor() as cur:
        cur.executemany("UPDATE core.job SET salary_min_usd = %(usd_min)s, salary_max_usd = %(usd_max)s,"
                        " salary_min_eur = %(eur_min)s, salary_max_eur = %(eur_max)s, salary_plausible = %(plausible)s"
                        " WHERE job_id = %(job_id)s", [{**pay, "job_id": job_id} for pay, job_id in found])
    return len(found)
