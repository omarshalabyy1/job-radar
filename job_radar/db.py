"""The warehouse connection: Postgres in docker-compose.yml, on 127.0.0.1:5433 from the laptop
(not "localhost": Windows tries IPv6 first and waits about 2 minutes, as Docker publishes the port on IPv4 only)."""

import os

import psycopg


def connect() -> psycopg.Connection:
    return psycopg.connect(host=os.environ.get("WAREHOUSE_HOST", "127.0.0.1"),
                           port=os.environ.get("WAREHOUSE_PORT", "5433"),
                           dbname="jobradar", user="jobradar", password=os.environ["WAREHOUSE_PASSWORD"])
