"""The warehouse connection: Postgres in docker-compose.yml, on 127.0.0.1:5433 from the laptop
(not "localhost": Windows tries IPv6 first and waits about 2 minutes, as Docker publishes the port on IPv4 only)."""

import os
import sys

import psycopg


def connect() -> psycopg.Connection:
    return psycopg.connect(host=os.environ.get("WAREHOUSE_HOST", "127.0.0.1"),
                           port=os.environ.get("WAREHOUSE_PORT", "5433"),
                           dbname=os.environ.get("WAREHOUSE_DB", "jobradar"),  # the tests set their own throwaway database
                           user="jobradar", password=os.environ["WAREHOUSE_PASSWORD"],
                           connect_timeout=15,  # a warehouse that is down is said in seconds, not minutes
                           # the step it serves, in the warehouse's log: a session that dies there names its step
                           application_name=("job_radar " + " ".join(sys.argv[1:]))[:60])
