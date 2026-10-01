"""Select a prepared release without advancing an old ledger's schema."""

import os
from pathlib import Path
import sqlite3

def activate_release(temporary: Path, selector: Path, runtime: Path, *, supported_schema_version: int) -> None:
    connection = None
    try:
        database = runtime / "state.sqlite3"
        if database.exists():
            connection = sqlite3.connect(database, isolation_level=None, timeout=10)
            connection.execute("BEGIN EXCLUSIVE")
            version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if version > supported_schema_version:
                raise RuntimeError("runtime schema is newer than this release; refusing downgrade")
            if version < supported_schema_version:
                pending = connection.execute("SELECT id FROM runs WHERE state NOT IN ('succeeded','failed','cancelled')").fetchall()
                if pending:
                    raise RuntimeError(
                        "schema upgrade deferred: active/recoverable runs " + ", ".join(row[0] for row in pending)
                        + "; finish or cancel with the previous release, then retry"
                    )
        os.replace(temporary, selector)
        if connection is not None:
            connection.execute("COMMIT")
    finally:
        if connection is not None:
            connection.close()
