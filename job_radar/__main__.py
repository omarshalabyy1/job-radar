"""python -m job_radar <step> [<step> ...] | all

Steps, in order: schema extract_boards extract_portals extract_email transform describe score email
Settings come from the environment, or from .env in the repo root (see .env.example).
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

from . import steps
from .db import connect

STEPS = ["schema", "extract_boards", "extract_portals", "extract_email", "transform", "describe", "score", "email"]


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    names = STEPS if sys.argv[1:] == ["all"] else sys.argv[1:]
    if not names or any(name not in STEPS for name in names):
        raise SystemExit(__doc__)
    with connect() as conn:
        for name in names:
            print(f"== {name}")
            getattr(steps, name)(conn)


if __name__ == "__main__":
    main()
