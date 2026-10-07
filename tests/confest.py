import os

import psycopg
import pytest


@pytest.fixture
def conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    c = psycopg.connect(dsn)
    # Open the transaction up front, so replace_snapshot nests as a savepoint
    # instead of committing, and teardown rolls everything back.
    c.execute("SELECT 1")
    yield c
    c.rollback()
    c.close()