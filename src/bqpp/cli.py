"""Typer CLI. Contains no business logic — it parses args and calls stage functions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

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

DbOpt = Annotated[Path | None, typer.Option("--db", help="Override the configured SQLite path")]
Verbose = Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging")]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Do everything except write")]
Force = Annotated[bool, typer.Option("--force", help="Redo work that is already done")]


def _open_db(db_path: Path | None) -> Database:
    settings = load_settings()
    settings.ensure_dirs()
    db = Database.connect(db_path or settings.db_path)
    db.init_schema()
    return db


def _build_client_or_exit(database: Database):
    """A missing API key is a config mistake, not a crash — report it and stop."""
    from bqpp.llm.base import LLMError
    from bqpp.llm.factory import build_client

    try:
        return build_client(load_settings(), db=database)
    except (LLMError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        database.close()
        raise typer.Exit(code=1) from None


def _setup_logging(verbose: bool) -> None:
    """-v raises verbosity for our own loggers only.

    Root DEBUG would drown the run in httpcore/filelock/urllib3 chatter during a
    harvest, which is exactly when someone reaches for -v.
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("bqpp").setLevel(logging.DEBUG if verbose else logging.INFO)


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
    source: Annotated[str | None, typer.Option("--source", help="Only this source id")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """Download / refresh sources into the corpus."""
    _setup_logging(verbose)
    _run_adapters(source, db, dry_run=dry_run, force=force)


@app.command()
def parse(
    source: Annotated[str | None, typer.Option("--source", help="Only this source id")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """Re-segment already-downloaded documents, without touching the network.

    Everything harvest does except fetching: it reads the on-disk cache, so the
    segmenters can be iterated on without asking the OAB for the same 45 PDFs again.
    """
    _setup_logging(verbose)
    _run_adapters(source, db, dry_run=dry_run, force=force, offline=True)


_OFFLINE_CAPABLE = {"oab_site", "cebraspe", "generic_pdf"}


def _adapters() -> dict:
    """Adapter id -> harvest_source callable. Imported lazily: pdfplumber and
    huggingface_hub are heavy, and `bqpp stats` needs neither."""
    from bqpp.harvest import cebraspe, generic_pdf, hf_datasets, oab_site

    return {
        "hf_datasets": hf_datasets.harvest_source,
        "oab_site": oab_site.harvest_source,
        "cebraspe": cebraspe.harvest_source,
        "generic_pdf": generic_pdf.harvest_source,
    }


def _run_adapters(
    source: str | None, db: Path | None, *, dry_run: bool, force: bool, offline: bool = False
) -> None:
    from bqpp.harvest.registry import load_sources

    settings = load_settings()
    settings.ensure_dirs()
    database = _open_db(db)
    adapters = _adapters()
    total = 0
    for entry in load_sources():
        if source and entry.id != source:
            continue
        run = adapters.get(entry.adapter)
        if run is None:
            console.print(
                f"[yellow]skipping {entry.id}: adapter {entry.adapter!r} lands in M3[/yellow]"
            )
            continue
        if offline and entry.adapter not in _OFFLINE_CAPABLE:
            # Otherwise `bqpp parse` reaches the network anyway, and with --force it
            # re-ingests every HF row from scratch, wiping its classification and vetting.
            console.print(
                f"[yellow]skipping {entry.id}: adapter {entry.adapter!r} has no offline mode[/yellow]"
            )
            continue
        kwargs = {"offline": True} if offline else {}
        n = run(entry, database, settings, dry_run=dry_run, force=force, **kwargs)
        console.print(f"{entry.id}: {n} new questions")
        total += n
    console.print(f"{total} new questions {'parsed' if offline else 'harvested'}")
    database.close()


@app.command()
def seed(
    file: Annotated[Path | None, typer.Option("--file", help="Seed YAML; default config/")] = None,
    db: DbOpt = None,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """Ingest your own questions for subtopics no public exam covers."""
    _setup_logging(verbose)
    from bqpp.seed import SeedError, ingest_seeds, load_seeds

    database = _open_db(db)
    try:
        n = ingest_seeds(
            load_seeds(file), db=database, taxonomy=load_taxonomy(), force=force
        )
    except SeedError as exc:
        console.print(f"[red]{exc}[/red]")
        database.close()
        raise typer.Exit(code=1) from None
    console.print(f"seeded {n} questions")
    database.close()


@app.command()
def classify(
    only_unclassified: Annotated[bool, typer.Option("--only-unclassified")] = True,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """LLM classification against the taxonomy (tier: fast)."""
    _setup_logging(verbose)
    from bqpp.classify import run_classify

    database = _open_db(db)
    client = _build_client_or_exit(database)
    n = run_classify(
        database, client, load_taxonomy(),
        only_unclassified=only_unclassified, limit=limit, dry_run=dry_run, force=force,
    )
    console.print(f"classified {n} questions")
    database.close()


@app.command()
def vet(
    only_unvetted: Annotated[bool, typer.Option("--only-unvetted")] = True,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    db: DbOpt = None,
    dry_run: DryRun = False,
    force: Force = False,
    verbose: Verbose = False,
) -> None:
    """Rule + LLM vetting (tier: strong)."""
    _setup_logging(verbose)
    from bqpp.vet import load_watchlist, run_vet

    database = _open_db(db)
    client = _build_client_or_exit(database)
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
        str | None, typer.Option("--subtopics", help="Comma-separated ids; default all")
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
    note: Annotated[str | None, typer.Option("--note")] = None,
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
def history(
    semester: Annotated[
        str | None, typer.Option("--semester", help="Only this semester; default all")
    ] = None,
    db: DbOpt = None,
) -> None:
    """What was used in class, when, and the note you left on it."""
    database = _open_db(db)
    taxonomy = load_taxonomy()
    entries = database.usage_entries(semester)
    if not entries:
        scope = f" para {semester}" if semester else ""
        console.print(f"[yellow]nada registrado no usage log{scope}[/yellow]")
        database.close()
        raise typer.Exit()

    table = Table(title="Usage log", show_header=True)
    for col in ("semestre", "subtópico", "questão", "em", "nota"):
        table.add_column(col, no_wrap=col in ("semestre", "em"))
    for e in entries:
        q = database.get_question(e.question_id)
        # a question can vanish if the corpus was rebuilt; the log still stands
        stem = " ".join(q.stem.split())[:60] + "…" if q else f"[red]{e.question_id[:12]}…[/red]"
        table.add_row(
            e.semester,
            f"{e.subtopic_id} {taxonomy.labels.get(e.subtopic_id, '')}"[:34],
            stem,
            (e.used_at or "")[:10],
            e.note or "—",
        )
    console.print(table)
    n = len(entries)
    console.print(f"{n} {'questão registrada' if n == 1 else 'questões registradas'}")
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
