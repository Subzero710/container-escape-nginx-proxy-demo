import logging
import os
import signal
import sys
from datetime import datetime, timezone
from threading import Event

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
SCHEMA_LOCK = 861157001
CREATE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""
logger = logging.getLogger("worker")


def connect_database():
    return psycopg.connect(
        DATABASE_URL, connect_timeout=3, options="-c statement_timeout=5000"
    )


def initialize_database():
    with connect_database() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
        connection.execute(CREATE_ITEMS_TABLE)


def insert_tick():
    message = f"Worker tick — {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    with connect_database() as connection:
        row = connection.execute(
            "INSERT INTO items (message) VALUES (%s) RETURNING id", (message,)
        ).fetchone()
    logger.info("Inserted item %s: %s", row[0], message)
    return row[0]


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(message)s")
    stopped = Event()

    def stop(signum, frame):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        initialize_database()
        logger.info("Worker started; insert interval: 30 seconds")
        while not stopped.is_set():
            insert_tick()
            stopped.wait(30)
    except psycopg.Error:
        logger.exception("PostgreSQL unavailable; worker stopped")
        return 1
    logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
