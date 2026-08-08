# CLAUDE.md — banco-questoes-pp

Corpus builder + semester curation pipeline for **Processo Penal 2** (Faculdade de Direito, UFF).
It harvests Brazilian public-exam questions, classifies them against the course taxonomy, vets them
for outdated law, and emits per-subtopic markdown shortlists. **It never picks the question used in
class** — it shortlists, the professor chooses.

`SPEC-questoes-pipeline.md` is the contract. Read it before changing pipeline behaviour.

## Commands

```bash
uv sync --extra dev
uv run pytest                     # 171 tests, ~8s, no network and no LLM
uv run ruff check .
uv run bqpp stats                 # corpus counts + per-subtopic coverage
uv run bqpp harvest | parse | classify | vet | curate --semester 2026.2
uv run bqpp seed | use | history | stats | export
```

`harvest`/`classify`/`vet` hit the network and (for the last two) a real LLM. Prefer `--dry-run`
and `--limit N` when exercising them by hand.

## Milestone status

| | | |
|---|---|---|
| **M0** skeleton | ✅ | models, db, settings, LLM layer, CLI |
| **M1** HF bootstrap | ✅ | `eduagarcia/oab_exams` + `maritaca-ai/oab-bench` → 207 questions, 19/21 subtopics |
| **M2** OAB 2ª-fase padrões | ✅ | `oab_site` adapter + padrão segmenter → 139 discursivas/peças with banca rationale |
| **M2.5** 1ª-fase provas | ⬜ | two-column reflow; deferred — MCQ ranks lowest for this method |
| **M3** Cebraspe + generic PDFs | ⬜ | certo/errado pipeline; needs `item_type` + `stem_context` schema fields |
| **M4** polish | ⬜ | carreira-diversity ranking, per-semester stats, JSONL round-trip test |

**T1.1** and **T3.3** stay empty by design — doctrinal topics objective exams avoid. The professor
seeds them by hand via `config/seed_questions.yaml`; M3/Cebraspe is the durable fix. Do not widen
scope to chase them.

The M2 plan and its spec-amendment table, all verified against live sources, are in
`docs/superpowers/plans/2026-08-08-banco-questoes-pp-m2.md`.

## Architecture invariants

Violating one of these is a bug even if the tests pass.

- **`db.py` is the only module that emits SQL.** Every other module goes through `Database`.
- **`harvest/http.py` is the only module that opens a socket**, so harvest etiquette is enforced
  in one testable place. **`parse/` is pure** — bytes or text in, dataclasses out; no network, no DB.
- **`llm/client.py` is the only place retries, schema validation and call logging live.** Backends
  (`llm/gemini_client.py`, `llm/openai_client.py`) are dumb transports: no retries, no validation,
  no logging, ~40 lines each. Adding a provider must stay that cheap.
- **`cli.py` holds no business logic.** It parses args and calls a stage function.
- **Never store unvalidated LLM output.** `complete_json` either returns a dict that passed
  `jsonschema.validate`, or raises. Client-side validation is authoritative regardless of backend —
  Gemini and OpenAI accept overlapping-but-different schema subsets.
- **Model ids are config values, never literals in code.** They live in `config/settings.toml` and
  drift fast. Same for ranking weights and shortlist size.
- **Every stage is idempotent.** Work is keyed by deterministic ids (`sha256`), existing rows are
  skipped unless `--force`, and stages communicate only through SQLite + the file cache, so any
  stage can be re-run in isolation.
- **One bad row must not abandon the run.** Stage loops catch `LLMError` per question, log, continue.

## Domain rules that are easy to get wrong

- **Language split:** code, identifiers, comments, docs in **English**; question content, shortlist
  prose and `pedagogy_note` in **pt-BR**. Question text is reproduced **verbatim, never paraphrased**.
- **Attribution is mandatory** on every shortlist entry: banca, certame, year, source URL. This is
  what makes classroom reproduction defensible (spec §15).
- **Only public, official (or officially mirrored) sources.** Commercial aggregators (QConcursos,
  TEC Concursos, Estratégia, Gran Cursos…) are out of bounds — terms bar it and it isn't needed.
- **Harvest etiquette is a hard requirement** (spec §6): descriptive User-Agent from
  `[harvest] user_agent`, ≤ 1 request/second per host, on-disk cache keyed by URL hash, and every
  download recorded with URL + SHA-256 + fetch date for provenance.
- **`flagged` is not `rejected`.** A question whose answer changed with the *Pacote Anticrime* is
  classroom gold. `resposta_mudou_mas_util` deliberately maps to `flagged` and surfaces with a
  warning banner (spec §10.2, enforced in `vet._merge_verdict`).
- **`exam_year` is load-bearing.** The law watchlist only fires when a question's `exam_year`
  predates a watchlist entry's `effective` date. A source that registers questions without a year
  silently disables vetting for them — hence `_exam_document()` in the HF adapter, which splits one
  dataset bundle into one `source_documents` row per exam.
- **Renaming a taxonomy id orphans `usage_log` rows and existing shortlists.** Adding is safe.
- **`curate` excludes only *prior* semesters' picks.** Re-curating mid-selection must not swap out
  the professor's own pick; it stays, marked `✅ Já escolhida`.

## Conventions

- **TDD.** Every task in the plan docs starts with a failing test. Tests never touch the network or
  a real LLM: provider SDKs are stubbed, LLM calls go through `llm/fake_client.FakeBackend`, and
  harvest adapters are fed committed fixtures in `tests/fixtures/` (trimmed **real** rows, not
  invented ones). **No exam PDFs are committed** — this repo is public, so padrão fixtures are stored
  as pdfplumber-extracted text and `.gitignore` blocks `tests/fixtures/**/*.pdf`. The one test that
  genuinely needs a PDF builds a minimal synthetic one (`tests/test_parse_pdf.py::_one_page_pdf`).
- `load_settings()` and `load_taxonomy()` are `@cache`d. Tests that need different paths use
  `load_settings().model_copy(deep=True)` and override fields.
- Ruff with `E,F,W,I,UP,B,C4,DTZ,ISC,RUF`, line length 100. `DTZ` means datetimes need a timezone —
  `datetime.now(UTC)` everywhere, with one deliberate `# noqa: DTZ011` in `vet.build_prompt` because
  "is this still correct today?" means the professor's wall clock, not UTC's.
- One commit per plan task, `feat:`/`fix:`/`chore:` prefix, subject describing the behaviour change.

## Layout notes

- `docs/superpowers/plans/` holds the implementation plans. `2026-08-05-banco-questoes-pp-m0-m1.md`
  and `2026-08-08-banco-questoes-pp-m2.md` are the model to follow: global constraints, a
  **spec-amendments table verified against live sources**, then TDD tasks with explicit file lists
  and interfaces. Write the next milestone's plan the same way.
- `data/` is gitignored (raw PDFs, `corpus.sqlite`, JSONL export). **`data/corpus.sqlite` holds the
  usage log — the only record of what was actually taught. Never delete it casually; it is not
  reconstructible from the sources.**
- `shortlists` is gitignored **with no trailing slash** — on the professor's machine it is a symlink
  into the course folder on Google Drive, and git treats a symlink as a file, so `shortlists/` would
  fail to match and commit the absolute path.
- `config/taxonomy.yaml` and `config/law_watchlist.yaml` are maintained by the professor. Treat edits
  to them as domain decisions, not refactors.
