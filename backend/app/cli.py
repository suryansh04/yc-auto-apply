"""Operator CLI for the pipeline (spec §9). Each command wires real adapters
and repos into an application-layer use case and runs it against the
configured database.

`domain/` and `application/` stay adapter-free (D8); this module is the one
place allowed to import both a concrete adapter and a concrete repo and glue
them together.
"""

import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings

app = typer.Typer(add_completion=False)


@app.callback()
def _callback() -> None:
    """YC Auto Apply operator CLI. Run a subcommand, e.g. `sync`."""
    # Present even though empty: without a callback, Typer collapses a
    # single-command app so its one command runs with no subcommand name at
    # all, breaking the documented invocation `python -m app.cli sync`. A
    # (no-op) callback keeps subcommand dispatch active regardless of how
    # many commands are registered.


def _session() -> Session:
    engine = create_engine(get_settings().database_url)
    return Session(bind=engine)


@app.command()
def sync() -> None:
    """Sync the target company list from the yc-oss dataset (Task 10)."""
    from app.adapters.db.repositories import CompanyRepo
    from app.adapters.yc_oss import YCOSSDirectory
    from app.application.sync_companies import SyncCompanies

    with _session() as session:
        result = SyncCompanies(YCOSSDirectory(), CompanyRepo(session)).run()
        session.commit()
    typer.echo(
        f"fetched {result.fetched}, matched {result.matched}, upserted {result.upserted}"
    )


if __name__ == "__main__":
    app()
