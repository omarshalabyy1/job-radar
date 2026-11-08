"""Check monthly usage across up to eight Apify accounts and start an Actor on one chosen account.

The local threshold (limit - safety margin) is a pre-check only, not a billing cap: usage can
change between the check and the launch, and the Actor keeps incurring usage while it runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

SLOTS = range(1, 9)
# Field of GET /v2/users/me/usage/monthly (apify-client 2.x: UserClient.monthly_usage()).
USAGE_FIELD = "totalUsageCreditsUsdAfterVolumeDiscount"

log = logging.getLogger("apify_rotation")


def load_accounts() -> dict[int, ApifyClient]:
    """One client per configured APIFY_TOKEN_n; missing slots are logged, never their values."""
    accounts = {}
    for n in SLOTS:
        token = os.getenv(f"APIFY_TOKEN_{n}", "").strip()
        if token:
            accounts[n] = ApifyClient(token)
        else:
            log.info("account %d: not configured", n)
    return accounts


def limit_for(n: int) -> float:
    return float(os.getenv(f"APIFY_ACCOUNT_LIMIT_USD_{n}") or os.getenv("APIFY_ACCOUNT_LIMIT_USD", "5.00"))


def threshold_for(n: int) -> float:
    return limit_for(n) - float(os.getenv("APIFY_SAFETY_MARGIN_USD", "0.50"))


def get_monthly_usage(account: ApifyClient) -> float:
    data = account.user().monthly_usage()
    if not data or USAGE_FIELD not in data:
        raise ValueError("usage response missing " + USAGE_FIELD)
    return float(data[USAGE_FIELD])


def check(n: int, account: ApifyClient) -> dict:
    """Usage row for one account; any error makes usage unknown and the account ineligible."""
    try:
        usage = get_monthly_usage(account)
    except Exception as exc:  # fail closed on anything
        return {"account": n, "usage": None, "remaining": None, "eligible": False, "error": type(exc).__name__}
    return {"account": n, "usage": usage, "remaining": limit_for(n) - usage,
            "eligible": usage < threshold_for(n), "error": None}


def get_usage_report(accounts: dict[int, ApifyClient]) -> list[dict]:
    return [check(n, a) for n, a in sorted(accounts.items())]


def print_report(results: list[dict]) -> None:
    print(f"{'acct':<5}{'usage $':>10}{'est. left $':>13}  eligible  error")
    for r in results:
        usage = "unknown" if r["usage"] is None else f"{r['usage']:.2f}"
        left = "unknown" if r["remaining"] is None else f"{r['remaining']:.2f}"
        print(f"{r['account']:<5}{usage:>10}{left:>13}  {'yes' if r['eligible'] else 'no':<8}  {r['error'] or ''}")
    print("Remaining amounts are estimates, not hard billing caps.")


def initialize_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_number INTEGER NOT NULL,
        actor_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        status TEXT,
        created_at TEXT NOT NULL)""")
    return db


def save_run(db: sqlite3.Connection, account_number: int, actor_id: str, run_id: str, status: str | None) -> None:
    db.execute("INSERT INTO runs (account_number, actor_id, run_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
               (account_number, actor_id, run_id, status, datetime.now(timezone.utc).isoformat()))
    db.commit()


def load_input(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid input file {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"invalid input file {path}: must be a JSON object")
    return data


def run_actor(n: int, account: ApifyClient, actor_id: str, run_input: dict | None, db: sqlite3.Connection) -> int:
    status = check(n, account)
    if not status["eligible"]:
        log.error("account %d not eligible (usage=%s, threshold=%.2f, error=%s); not starting",
                  n, status["usage"], threshold_for(n), status["error"])
        return 2
    try:
        run = account.actor(actor_id).start(run_input=run_input)
    except Exception as exc:
        # The run may have started anyway; never retry here or on another account.
        log.error("launch failed or was ambiguous (%s); check the Apify Console before retrying", type(exc).__name__)
        return 3
    run_id = (run or {}).get("id")
    if not run_id:
        log.error("no run ID returned; check the Apify Console before retrying")
        return 3
    save_run(db, n, actor_id, run_id, run.get("status"))
    log.info("account %d started run %s (%s)", n, run_id, run.get("status"))
    log.info("usage keeps growing while the run is active; the threshold is not a hard cap")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("report", help="show monthly usage for every configured account")
    run = sub.add_parser("run", help="start an Actor on one explicitly chosen account")
    run.add_argument("account", type=int, choices=list(SLOTS))
    run.add_argument("actor_id", nargs="?", default=os.getenv("APIFY_ACTOR_ID"), help="e.g. username/actor-name")
    run.add_argument("--input", type=Path, help="JSON file holding the Actor input object")
    args = parser.parse_args(argv)

    accounts = load_accounts()
    if args.command == "report":
        print_report(get_usage_report(accounts))
        return 0
    if not args.actor_id:
        parser.error("actor_id is required (argument or APIFY_ACTOR_ID)")
    if args.account not in accounts:
        log.error("account %d is not configured", args.account)
        return 2
    run_input = load_input(args.input)
    db = initialize_db(Path(os.getenv("APIFY_STATE_DB", "apify_rotation.sqlite3")))
    try:
        return run_actor(args.account, accounts[args.account], args.actor_id, run_input, db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
