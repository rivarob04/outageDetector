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
def migrate() -> None:
    """Apply database migrations."""
    from pulse.db.migrate import upgrade

    upgrade(get_settings())
    typer.echo("migrations applied")


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
