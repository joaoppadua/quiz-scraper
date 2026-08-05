"""Typer CLI. Contains no business logic — it parses args and calls stage functions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from bqpp.config import load_settings, load_taxonomy
from bqpp.db import Database

app = typer.Typer(
    add_completion=False,
    help="banco-questoes-pp — exam-question corpus builder and semester curation",
)
console = Console()

DbOpt = Annotated[Optional[Path], typer.Option("--db", help="Override the configured SQLite path")]
Verbose = Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging")]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Do everything except write")]
Force = Annotated[bool, typer.Option("--force", help="Redo work that is already done")]


def _open_db(db_path: Optional[Path]) -> Database:
    settings = load_settings()
    settings.ensure_dirs()
    db = Database.connect(db_path or settings.db_path)
    db.init_schema()
    return db


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def stats(db: DbOpt = None) -> None:
    """Corpus counts by subtopic / format / vet status."""
    database = _open_db(db)
    s = database.stats()
    taxonomy = load_taxonomy()

    console.print(f"Total questions: {s['total']}")
    for title, data in (
        ("By format", s["by_format"]),
        ("By discipline", s["by_discipline"]),
        ("By vet status", s["by_vet_status"]),
        ("Sources", s["sources"]),
    ):
        if not data:
            continue
        table = Table(title=title, show_header=True)
        table.add_column("key")
        table.add_column("count", justify="right")
        for k, v in sorted(data.items()):
            table.add_row(k, str(v))
        console.print(table)

    # Every subtopic is listed, including empty ones — a zero here is the M2 work
    # queue, and it is invisible if we only print what the corpus happens to cover.
    coverage = Table(title="Subtopic coverage", show_header=True)
    coverage.add_column("id", no_wrap=True)
    coverage.add_column("label")
    coverage.add_column("n", justify="right")
    for sid, label in taxonomy.labels.items():
        n = s["by_subtopic"].get(sid, 0)
        coverage.add_row(sid, label, f"[red]{n}[/red]" if n == 0 else str(n))
    console.print(coverage)
    database.close()


@app.command()
def harvest(
    source: Annotated[Optional[str], typer.Option("--source", help="Only this source id")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """Download / refresh sources into the corpus."""
    _setup_logging(verbose)
    from bqpp.harvest.hf_datasets import harvest_source
    from bqpp.harvest.registry import load_sources

    settings = load_settings()
    settings.ensure_dirs()
    database = _open_db(db)
    total = 0
    for entry in load_sources():
        if source and entry.id != source:
            continue
        if entry.adapter != "hf_datasets":
            console.print(
                f"[yellow]skipping {entry.id}: adapter {entry.adapter!r} lands in M2/M3[/yellow]"
            )
            continue
        n = harvest_source(entry, database, settings, dry_run=dry_run, force=force)
        console.print(f"{entry.id}: {n} new questions")
        total += n
    console.print(f"{total} new questions harvested")
    database.close()


@app.command()
def classify(
    only_unclassified: Annotated[bool, typer.Option("--only-unclassified")] = True,
    limit: Annotated[Optional[int], typer.Option("--limit")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """LLM classification against the taxonomy (tier: fast)."""
    _setup_logging(verbose)
    from bqpp.classify import run_classify
    from bqpp.llm.factory import build_client

    database = _open_db(db)
    client = build_client(load_settings(), db=database)
    n = run_classify(
        database, client, load_taxonomy(),
        only_unclassified=only_unclassified, limit=limit, dry_run=dry_run, force=force,
    )
    console.print(f"classified {n} questions")
    database.close()


@app.command()
def vet(
    only_unvetted: Annotated[bool, typer.Option("--only-unvetted")] = True,
    limit: Annotated[Optional[int], typer.Option("--limit")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """Rule + LLM vetting (tier: strong)."""
    _setup_logging(verbose)
    from bqpp.llm.factory import build_client
    from bqpp.vet import load_watchlist, run_vet

    database = _open_db(db)
    client = build_client(load_settings(), db=database)
    n = run_vet(
        database, client, load_watchlist(),
        only_unvetted=only_unvetted, limit=limit, dry_run=dry_run, force=force,
    )
    console.print(f"vetted {n} questions")
    database.close()


@app.command()
def curate(
    semester: Annotated[str, typer.Option("--semester", help='e.g. "2026.2"')],
    subtopics: Annotated[
        Optional[str], typer.Option("--subtopics", help="Comma-separated ids; default all")
    ] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    verbose: Verbose = False,
) -> None:
    """Write per-subtopic markdown shortlists."""
    _setup_logging(verbose)
    from bqpp.curate import run_curate

    settings = load_settings()
    settings.ensure_dirs()
    database = _open_db(db)
    ids = [s.strip() for s in subtopics.split(",")] if subtopics else None
    written = run_curate(
        database, load_taxonomy(), settings,
        semester=semester, subtopic_ids=ids, dry_run=dry_run,
    )
    empty = [k for k, v in written.items() if v == 0]
    out_dir = settings.shortlist_dir / semester.replace(".", "-")
    console.print(f"wrote {len(written)} shortlists to {out_dir}")
    if empty:
        console.print(
            f"[yellow]{len(empty)} subtopics with no candidates: {', '.join(empty)}[/yellow]"
        )
    database.close()


@app.command()
def use(
    question_id: str,
    semester: Annotated[str, typer.Option("--semester")],
    subtopic: Annotated[str, typer.Option("--subtopic")],
    note: Annotated[Optional[str], typer.Option("--note")] = None,
    db: DbOpt = None,
) -> None:
    """Record the professor's pick in the usage log."""
    from bqpp.curate import record_use

    database = _open_db(db)
    if database.get_question(question_id) is None:
        console.print(f"[red]no such question: {question_id}[/red]")
        database.close()
        raise typer.Exit(code=1)
    record_use(database, question_id, semester, subtopic, note)
    console.print(f"recorded {question_id[:12]}… for {semester} / {subtopic}")
    database.close()


@app.command()
def export(db: DbOpt = None) -> None:
    """Write data/export/questions.jsonl."""
    from bqpp.export import export_jsonl

    settings = load_settings()
    settings.ensure_dirs()
    database = _open_db(db)
    out = settings.export_dir / "questions.jsonl"
    console.print(f"exported {export_jsonl(database, out)} questions to {out}")
    database.close()


if __name__ == "__main__":
    app()
