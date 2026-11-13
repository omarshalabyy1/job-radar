"""Copy four warehouse tables into the local SQL Server database jobradar_copy, to practise SSMS, SSIS and SSAS on real data.

    python scripts/copy_to_sqlserver.py

Postgres is only read (a read-only session; nothing is written there). On SQL Server (localhost, Windows auth) the script
creates jobradar_copy if it is missing, then drops and reloads each copied table, so a rerun is a full refresh.
It prints each table's row count in Postgres and in SQL Server, and exits 1 if any differ.

Needs pyodbc and "ODBC Driver 18 for SQL Server" (Windows only). pyodbc is not in requirements.txt because the Docker image
installs that file and has no use for SQL Server. timestamptz columns land as DATETIME2 in UTC; text[] columns as JSON text."""

import json
import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from psycopg import IsolationLevel  # noqa: E402

from job_radar import db  # noqa: E402

TABLES = ["mart.job_status", "core.application", "core.application_email", "core.job"]
TARGET_DB = "jobradar_copy"
SERVER = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;Trusted_Connection=yes;TrustServerCertificate=yes"

TYPES = {"bigint": "BIGINT", "integer": "INT", "smallint": "SMALLINT", "boolean": "BIT", "date": "DATE",
         "real": "REAL", "double precision": "FLOAT", "text": "NVARCHAR(MAX)", "character varying": "NVARCHAR(MAX)",
         "timestamp with time zone": "DATETIME2", "ARRAY": "NVARCHAR(MAX)"}


def sql_type(data_type: str, precision: int | None, scale: int | None) -> str:
    """The SQL Server type for a Postgres column; numeric without a precision becomes FLOAT."""
    if data_type == "numeric":
        return f"DECIMAL({precision},{scale})" if precision else "FLOAT"
    return TYPES[data_type]


def to_sql(value, data_type: str, target: str):
    """A Postgres value as SQL Server takes it."""
    if value is None:
        return None
    if data_type == "ARRAY":
        return json.dumps(value, ensure_ascii=False)
    if data_type == "timestamp with time zone":
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if target == "FLOAT":
        return float(value)
    return value


def read(pg, table: str) -> tuple[list, list, int]:
    """Columns as (name, data_type, sql_type), the rows, and count(*) for one table."""
    schema, name = table.split(".")
    cols = [(c, t, sql_type(t, p, s)) for c, t, p, s in pg.execute(
        "SELECT column_name, data_type, numeric_precision, numeric_scale FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (schema, name))]
    names = ", ".join('"' + c + '"' for c, _, _ in cols)
    rows = pg.execute(f"SELECT {names} FROM {table}").fetchall()
    count = pg.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return cols, [tuple(to_sql(v, t, st) for v, (_, t, st) in zip(r, cols)) for r in rows], count


def load(cur, table: str, cols: list, rows: list) -> int:
    """Drop, recreate and fill one table in jobradar_copy; return its row count there."""
    schema, name = table.split(".")
    target = f"[{schema}].[{name}]"
    cur.execute(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA [{schema}]')")
    cur.execute(f"DROP TABLE IF EXISTS {target}")
    cur.execute(f"CREATE TABLE {target} ({', '.join(f'[{c}] {st}' for c, _, st in cols)})")
    if rows:
        cur.executemany(f"INSERT INTO {target} VALUES ({', '.join('?' for _ in cols)})", rows)
    return cur.execute(f"SELECT count(*) FROM {target}").fetchone()[0]


def main() -> int:
    import pyodbc  # Windows-only dependency, see the docstring

    with db.connect() as pg:
        pg.isolation_level = IsolationLevel.REPEATABLE_READ  # one snapshot: the hourly DAG can't shift counts mid-copy
        pg.read_only = True
        data = {t: read(pg, t) for t in TABLES}

    server = pyodbc.connect(SERVER, autocommit=True)  # CREATE DATABASE can't run inside a transaction
    server.execute(f"IF DB_ID('{TARGET_DB}') IS NULL CREATE DATABASE [{TARGET_DB}]")
    server.close()

    copy = pyodbc.connect(f"{SERVER};DATABASE={TARGET_DB}")
    cur = copy.cursor()
    cur.fast_executemany = True
    ok = True
    for table, (cols, rows, pg_count) in data.items():
        ms_count = load(cur, table, cols, rows)
        copy.commit()
        ok &= ms_count == pg_count
        print(f"{table}: postgres {pg_count}, sql server {ms_count}{'' if ms_count == pg_count else '  MISMATCH'}")
    copy.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
