"""`pulse` command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from pulse.config import get_settings

app = typer.Typer(help="Pulse: self-hosted outage map", no_args_is_help=True)


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Run the hub (API, UI, scheduler)."""
    s = get_settings()
    uvicorn.run(
        "pulse.app:create_app",
        factory=True,
        host=host or s.host,
        port=port or s.port,
        reload=reload,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


@app.command()
def agent() -> None:
    """Run as a remote vantage agent (needs PULSE_HUB_URL and PULSE_AGENT_TOKEN)."""
    import asyncio

    from pulse.agent.runner import run_agent
    from pulse.logging import setup_logging

    s = get_settings()
    setup_logging(s.log_level, s.log_json)
    asyncio.run(run_agent(s))


@app.command()
def migrate(
    downgrade_to: str = typer.Option(
        None, "--downgrade", help="Revision to downgrade to, e.g. 0002"
    ),
) -> None:
    """Apply database migrations (they also run automatically on start)."""
    from pulse.db.migrate import downgrade, upgrade

    if downgrade_to:
        downgrade(get_settings(), downgrade_to)
        typer.echo(f"downgraded to {downgrade_to}")
    else:
        upgrade(get_settings())
        typer.echo("migrations applied")


@app.command()
def backup(
    path: Path = typer.Argument(..., help="Output file, e.g. pulse-backup.tar.gz"),
    skip_raw: bool = typer.Option(False, help="Leave out raw check results (much smaller)"),
) -> None:
    """Write a portable backup (works for SQLite and Postgres, and across them)."""
    import asyncio

    from pulse.backup import backup as do_backup
    from pulse.db.engine import make_engine
    from pulse.db.store import Store

    async def run() -> dict[str, int]:
        engine = make_engine(get_settings())
        try:
            return await do_backup(Store(engine), path, include_raw=not skip_raw)
        finally:
            await engine.dispose()

    counts = asyncio.run(run())
    typer.echo(f"wrote {path} ({sum(counts.values()):,} rows)")


@app.command()
def restore(path: Path = typer.Argument(..., help="Backup file from `pulse backup`")) -> None:
    """Restore a backup into an empty data directory / database (stop the hub first)."""
    import asyncio

    from pulse.backup import restore as do_restore
    from pulse.db.engine import make_engine
    from pulse.db.migrate import upgrade
    from pulse.db.store import Store

    s = get_settings()
    upgrade(s)

    async def run() -> dict[str, int]:
        engine = make_engine(s)
        try:
            return await do_restore(Store(engine), path)
        finally:
            await engine.dispose()

    counts = asyncio.run(run())
    typer.echo(f"restored {sum(counts.values()):,} rows from {path}")


catalog_app = typer.Typer(help="Catalog tools", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("verify")
def catalog_verify(
    only: list[str] = typer.Option(None, "--only", help="Service ids to check"),
    record: Path | None = typer.Option(None, help="Save raw feed bodies to this dir"),
    write: bool = typer.Option(False, help="Write catalog/verification.json"),
    report: Path | None = typer.Option(None, help="Write a Markdown report here"),
) -> None:
    """Re-check every catalog endpoint live and print a table."""
    import asyncio

    from pulse.catalog.loader import load_catalog
    from pulse.catalog.verify import render_markdown, verify_catalog, write_results

    s = get_settings()
    cat_dir = s.resolved_catalog_dir()
    catalog = load_catalog(cat_dir)
    results = asyncio.run(verify_catalog(catalog, s.ua, only, record))
    bad = 0
    for r in sorted(results, key=lambda r: r.service_id):
        bad += 0 if r.verified else 1
        mark = "OK  " if r.verified else "FAIL"
        typer.echo(
            f"{mark} {r.service_id:<16} feed={r.feed_status} parsed={r.feed_parsed} "
            f"probes={[p.get('status', p.get('error')) for p in r.probes]} "
            f"{r.feed_error or ''}"
        )
    if write:
        write_results(cat_dir, results, merge=bool(only))
    if report:
        from pulse.catalog.loader import load_catalog as _reload

        full = _reload(cat_dir)
        report.write_text(render_markdown(full, list(full.verification.values()) or results))
    typer.echo(f"{len(results) - bad}/{len(results)} verified")


@app.command()
def version() -> None:
    from pulse import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
