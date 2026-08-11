# CLAUDE.md — banco-questoes-pp

Corpus builder + semester curation pipeline for **Processo Penal 2** (Faculdade de Direito, UFF).
It harvests Brazilian public-exam questions, classifies them against the course taxonomy, vets them
for outdated law, and emits per-subtopic markdown shortlists. **It never picks the question used in
class** — it shortlists, the professor chooses.

`SPEC-questoes-pipeline.md` is the contract. Read it before changing pipeline behaviour.

## Commands

```bash
uv sync --extra dev
uv run pytest                     # 431 tests, ~5s, no network and no LLM
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
| **M3** Cebraspe + curated provas | ✅ | `stem_context` migration, column extraction, 4 bancas, 829 questions |
| **M2.5** 1ª-fase provas | ✅ | OAB 1ª fase 2019–2026, Tipo 1: banded grid reader, per-source item anchor + furniture, keyword gate → **405 `mcq4` items, corpus 1222** |
| **Source widening** | ⬜ | more official sources; needs its own recon |
| **M4** polish | ⬜ | carreira-diversity ranking, per-semester stats, JSONL round-trip test |

**T3.3** is opened from doctrine, not from a question. It is marked `opens_with: doutrina` in
`config/taxonomy.yaml`, and neither `stats` nor `curate` treats it as a gap — that is the design
decision, and it stands whatever the corpus holds. It is no longer *empty*: M2.5 brought it to 3
candidates (2 Cebraspe `certo_errado` + 1 OAB `mcq4`) out of 1222 questions searched, so
`render_shortlist` now prints the normal candidate list rather than the "opens by doutrina"
message. Objective exams still barely touch standards of proof; three hits in 1222 is why the
subtopic is not sourced from them.

M2.5 shipped despite `[ranking]` putting `mcq4` at the floor weight of 1.0 — which was the stated
reason for deferring it. The payoff is depth in the thinnest subtopics, not headline count, and the
professor confirmed the trade before Task 1. Whether the weight should now change is a domain call,
untouched here.

The M2 plan and its spec-amendment table, all verified against live sources, are in
`docs/superpowers/plans/2026-08-08-banco-questoes-pp-m2.md`. The M2.5 plan
(`…/2026-08-09-banco-questoes-pp-m2.5.md`) carries the same for the 1ª fase, plus a **§ Defect
history** the shipped code and tests cite by name: read it before loosening any guard in
`parse/objetiva.py` or `harvest/oab_1f.py`.

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
- **Never construct a source URL by pattern.** MPRS ordinal substitution 404s, MPF's 401s. Literal
  verified URLs live in `config/provas_manifest.yaml` with the date they were confirmed.
- **Dedup keys fold in the alternatives.** A stem is not always where the content lives — MPF uses
  "Assinale a opção correta:" as the stem of five different questions.
- **Search and classify over comando + stem + rationale**, never the stem alone: for certo/errado
  items the topic is frequently declared only in the comando.
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
- **Schema changes go through `Database.MIGRATIONS`** — a guarded `ADD COLUMN`, which is O(1) and
  never rewrites the table. Ship it with a test that opens a database from the previous milestone.
- One commit per plan task, `feat:`/`fix:`/`chore:` prefix, subject describing the behaviour change.

## Layout notes

- `docs/superpowers/plans/` holds the implementation plans. `2026-08-05-banco-questoes-pp-m0-m1.md`,
  `2026-08-08-banco-questoes-pp-m2.md` and `2026-08-09-banco-questoes-pp-m2.5.md` are the model to
  follow: global constraints, a **spec-amendments table verified against live sources**, then TDD
  tasks with explicit file lists and interfaces. The M2.5 one adds the two sections that made it
  survive its own review — a **Result** section measured against the definition of done, and a
  **Defect history** naming what each fix round found. Write the next milestone's plan the same way,
  and keep the durable record in the plan: `.superpowers/` is git-ignored and does not survive a
  clone.
- `data/` is gitignored (raw PDFs, `corpus.sqlite`, JSONL export). **`data/corpus.sqlite` holds the
  usage log — the only record of what was actually taught. Never delete it casually; it is not
  reconstructible from the sources.**
- `shortlists` is gitignored **with no trailing slash** — on the professor's machine it is a symlink
  into the course folder on Google Drive, and git treats a symlink as a file, so `shortlists/` would
  fail to match and commit the absolute path.
- `config/taxonomy.yaml` and `config/law_watchlist.yaml` are maintained by the professor. Treat edits
  to them as domain decisions, not refactors.
