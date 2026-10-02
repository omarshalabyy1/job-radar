"""The warehouse connection: Postgres in docker-compose.yml, on localhost:5433 from the laptop."""

import os

import psycopg


def connect() -> psycopg.Connection:
    return psycopg.connect(host=os.environ.get("WAREHOUSE_HOST", "localhost"),
                           port=os.environ.get("WAREHOUSE_PORT", "5433"),
                           dbname="jobradar", user="jobradar", password=os.environ["WAREHOUSE_PASSWORD"])
