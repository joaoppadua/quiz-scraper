# banco-questoes-pp — M2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add official OAB 2ª-fase *Direito Penal* material to the corpus by harvesting and parsing the *Padrão de respostas* PDFs published by the Conselho Federal da OAB — ~139 discursive questions and peças, each carrying the banca's own **Gabarito Comentado**. This is milestone M2 of `SPEC-questoes-pipeline.md`, re-scoped against what the live sources actually serve (see Spec Amendments).

**Architecture:** Two new stages slot into the existing pipeline between `harvest` and `classify`. A polite HTTP layer (`harvest/http.py`) fetches and caches with a provenance manifest; an index scraper (`harvest/oab_site.py`) turns one seed page into per-exam artifact lists; a PDF layer (`parse/pdf.py`) extracts text and judges text-layer health; a segmenter (`parse/padrao.py`) splits a padrão into `(stem, rationale)` sections. Ingestion writes the same `Question` rows the HF adapters already write, so `classify → vet → curate` need **no changes at all**.

**Tech Stack:** unchanged, plus `pdfplumber` (pulls `pdfminer.six` + `pypdfium2`).

**Scope decided with the professor (2026-08-08):**
- **2ª fase only.** The 1ª-fase objective provas are deferred (see "Deferred" below).
- **All 45 exams, 2010.2 → 46º.** Full archive depth; the vetting stage already flags what the law overtook.
- **T1.1 and T3.3 are not M2's problem.** They stay empty pending M3/Cebraspe; Task 7 adds a manual seed path so they can be unblocked by hand.

---

## Global Constraints

Everything in the M0/M1 plan's Global Constraints section still applies verbatim. The ones this milestone actually exercises:

- **Only public, official (or officially mirrored) sources.** M2 fetches exclusively from `examedeordem.oab.org.br` and `s.oab.org.br` — the Conselho Federal da OAB's own publication of its own exam. No commercial aggregator, no third-party mirror.
- **Harvest etiquette is a hard requirement** (spec §6): descriptive User-Agent from `[harvest] user_agent`, **≤ 1 request/second per host**, on-disk cache keyed by URL hash (never re-download an unchanged file), and a manifest recording URL + SHA-256 + fetch date + HTTP headers for every file.
- **Verbatim text, never paraphrased**; full attribution (banca, certame, year, URL) on every shortlist entry.
- **Idempotent and re-runnable.** Deterministic ids, existing rows skipped unless `--force`.
- **`data/` is gitignored.** Downloaded PDFs live in `data/raw/oab/`; only small fixtures are committed.
- Every CLI command supports `--dry-run` and `-v`.

---

## Spec Amendments (verified against live sources, 2026-08-08)

These correct §6 and §13 of the spec. **Implement the amended values, not the spec's.** Every row below was observed directly — a full 46-exam index sweep plus download and structural analysis of all 45 Penal padrões.

| # | Spec says | Reality | Consequence |
|---|---|---|---|
| **B1** | Source `fgv-oab`, `base_url: https://oab.fgv.br`, "index pages list per-exam PDF links" | `oab.fgv.br/home.aspx?key=N` returns an **identical 7119-byte ASP.NET shell** for every one of the 46 exams — a "Selecione a Seccional" `<select>`, `__VIEWSTATE`/`__EVENTVALIDATION`, no submit button, no inline JS, **zero PDF links**. The `key` parameter is ignored without a postback. The same PDFs are served by the OAB itself over plain GET. | Point the adapter at `examedeordem.oab.org.br`. Name the module `harvest/oab_site.py` (it describes the *source site*); `banca` stays `FGV` in config (it describes the *authoring board*). The FGV postback route is not implemented. |
| **B2** | (implicit) exam PDFs can be enumerated or constructed | Nothing is guessable: `s.oab.org.br` filenames are random UUIDs under `/arquivos/YYYY/MM/`, with no directory listing; `oab.fgv.br/arq/` returns 403. | Index-scraping is **mandatory**. Discovery is a two-level GET: seed page → 46 exam ids → per-exam page → labelled links. 47 requests for a full sweep. |
| **B3** | (implicit) links are fetchable as emitted | **1337 of 1998** links (67%) are emitted as `http://`, and `http://s.oab.org.br/...` returns **502 Bad Gateway**; the identical `https://` URL returns 200. Clean boundary: exams **XXXII and newer are all https**, **XXXI and older are http**. | Mandatory `^http://` → `https://` rewrite before any fetch. Without it two-thirds of the archive silently vanishes. Ship a unit test on this one line. |
| **B4** | (implicit) `exam_year` is derivable from the URL | The `/arquivos/YYYY/MM/` path segment is the **upload** date, not the exam date (pre-2019 files were rehomed under `2019/10`). But **all 83** Penal padrão links carry a `dd/mm/yyyy` prefix in their anchor text. | Take `exam_year` from the **anchor-text date prefix**, never from the URL path. This is the date the 2ª fase was applied — exactly what the law watchlist means, and consistent with the `exam_years` map already used for `oab-bench`. |
| **B5** | M2 delivers "gabarito justificado ingestion" | The *Padrão de respostas* is **self-contained**: `PADRÃO DE RESPOSTA – QUESTÃO N` → `Enunciado` (the question verbatim, with `A)`/`B)` sub-items and `(Valor: 0,65)` markers) → `Gabarito Comentado` (the banca's reasoning) → `DISTRIBUIÇÃO DOS PONTOS`. The 2ª-fase *caderno* adds nothing the padrão lacks. | **One document per exam, not a prova+gabarito pair.** No pairing logic, no join key, no second parser. This is the single biggest simplification in M2. |
| **B6** | (implicit) 1ª-fase gabaritos carry justifications | 1ª-fase gabaritos are **bare answer grids** (1–2 pages, pure letter tables). Banca-authored rationales exist **only** for the 2ª fase. | M2 must not promise rationale-rich 1ª-fase items. Reinforces the 2ª-fase-only scope. |
| **B7** | (§13) segment on `Questão N` / `N.` | Real anchors, verified across 15 years: `PADRÃO DE RESPOSTA` + en-dash **or** hyphen + (`PEÇA PROFISSIONAL` \| `QUESTÃO N`), with an optional trailing booklet code (`– B005`, `- B005250`) and zero-padded numbers (`QUESTÃO 01`). Sub-headers `Enunciado` / `Gabarito Comentado` are **Title Case in most exams and ALL CAPS in 12** — a case-sensitive regex silently loses two-thirds of them. | All anchors compile with `re.I`. Tolerate `[–\-—]`, `0*(\d+)`, and a trailing suffix. Getting this wrong is the difference between 175 sections and 60. |
| **B8** | (§13) `pdfplumber` for extraction | Confirmed correct, and **better than `pdftotext`** here: on XXXIII, `pdftotext -layout` finds 0 `Enunciado` headers where `pdfplumber.extract_text()` finds 5. | Use `pdfplumber`. Fixtures must be generated with pdfplumber, not pdftotext, or the tests encode the wrong text. |
| **B9** | (§13) "no text layer ⇒ `needs_ocr`" | **Zero of 45 padrões** show glyph corruption or an absent text layer. The mojibake failure mode reported for a 1ª-fase caderno does not occur in this document class. What *does* happen is narrower: **36 of 175 sections have a rasterised or oddly-ordered enunciado**, so the stem extracts empty while the rationale extracts fine. | No OCR dependency in M2. Implement a per-section quality gate: a section with a rationale but no stem is **skipped and logged**, never ingested as a question with an empty `stem`. |
| **B10** | every exam yields a Penal padrão | **45 of 46** do (only the 47º, mid-cycle, does not). 34 publish an explicit *definitivo*; the rest only a plain *Padrão de respostas*. Label has 10 spelling variants (`(Direito Penal)`, `- Direito Penal`, `Definitivo- Penal`, …). | Fallback ladder per exam: **definitivo → plain → skip**, recorded in provenance. Match the label with a tolerant regex, not string equality. |
| **B11** | one artifact set per exam | **Reaplicação variants exist** and share a certame: XXV has a `Porto Alegre/RS` variant, XX a `Reaplicação Porto Velho/RO`. Both appear as a second Penal padrão on the same index page. | Model them as distinct exams keyed on (exam, variant) — the variant suffix goes into `certame`. Do **not** collapse them as duplicates. |
| **B12** | (implicit) the whole archive parses | **35 of 45** exams yield sections; 10 do not (2010.2, 2010.3, IV, V, VI, XI, XII, XIII, XIV, 39º) because they predate the `PADRÃO DE RESPOSTA – …` header convention. Measured yield: **175 sections, 139 usable** (111 discursivas + 28 peças) with both stem and rationale. | Ship the 139. Log the 10 no-anchor exams by name at INFO. Do not build speculative parsers for them — see Deferred. |
| **B13** | (implicit) sources do not overlap | Exams 39º–44º are **already in the corpus** from `maritaca-ai/oab-bench`. Of those, 39º has no anchors and 40º–43º have unusable stems, so the real collision is the 44º alone (5 sections). | Dedupe on a normalised stem hash computed in Python at ingest time. The corpus is ~350 rows — no index, no schema migration, no `ALTER TABLE`. |

**Yield summary (measured, not estimated):** 139 new discursive/peça items with banca-authored reasoning, against the 30 the corpus holds today — a **4.6× increase in the format `[ranking] format_weights` scores highest** (`dissertativa = 3.0` vs `mcq* = 1.0`).

---

## File Structure

```
quiz-scraper/
├── config/
│   └── sources.yaml                 # T2  + the oab-2f-penal entry (URLs live here, not in code)
│   └── seed_questions.yaml          # T7  professor-authored questions (new, optional file)
├── src/bqpp/
│   ├── db.py                        # T1  + harvest_manifest table, + stem-hash read helper (T5)
│   ├── models.py                    # T7  + "manual" to the Kind literal
│   ├── harvest/
│   │   ├── http.py                  # T1  polite fetch + on-disk cache + provenance manifest
│   │   └── oab_site.py              # T2/T5  index scrape, artifact selection, ingestion
│   ├── parse/
│   │   ├── __init__.py              # T3
│   │   ├── pdf.py                   # T3  pdfplumber extraction + text-layer health
│   │   └── padrao.py                # T4  padrão de resposta segmenter
│   ├── seed.py                      # T7  manual question ingestion
│   └── cli.py                       # T6  adapter dispatch, `bqpp parse`, `bqpp seed`
└── tests/
    ├── fixtures/oab_site/
    │   ├── seed_page.html           # T2  trimmed real seed page
    │   ├── exam_index.html          # T2  trimmed real per-exam index (44º)
    │   ├── padrao_44.pdf            # T3  one real padrão, 134 kB (spec §13 acceptance fixture)
    │   ├── padrao_44.expected.json  # T4  hand-checked expected segmentation
    │   ├── padrao_xxxiii.txt        # T4  Title-Case anchors, pdfplumber-extracted
    │   ├── padrao_xxv.txt           # T4  booklet-code suffix (QUESTÃO 1 – B005250)
    │   └── padrao_vii.txt           # T4  no Enunciado/Gabarito sub-headers (degrades to skip)
    └── test_http.py test_oab_site.py test_parse_pdf.py test_padrao.py test_seed.py
```

Responsibility boundaries carried over and extended: **`db.py` is still the only module that emits SQL**; **`harvest/http.py` is the only module that opens a socket** (so etiquette is enforced in exactly one place and is unit-testable); **`parse/` is pure** — it takes bytes or text and returns dataclasses, touching neither the network nor the database.

---

## Task 1: Polite HTTP layer with cache and provenance manifest

**Files:** Create `src/bqpp/harvest/http.py`, `tests/test_http.py`. Modify `src/bqpp/db.py`.

**Interfaces:**
- Produces: `Fetcher(user_agent, cache_dir, db=None, min_interval=1.5)` with `.get(url) -> FetchResult`, where `FetchResult` has `.body: bytes`, `.from_cache: bool`, `.sha256: str`, `.url: str` (post-rewrite), `.headers: dict`.
- Produces: `normalise_url(url) -> str` — the `http://` → `https://` rewrite (B3), exported separately so it can be tested in isolation.
- Consumes: `Database.record_fetch(...)`.

- [x] **Step 1: Write the failing tests.** No real network: monkeypatch the module's opener.
  - `normalise_url` upgrades `http://s.oab.org.br/x.pdf` and leaves `https://` untouched (B3).
  - A second `.get()` of the same URL returns `from_cache=True` and does **not** call the opener again.
  - The cache file is keyed by a hash of the URL, and the returned `sha256` is the digest of the body.
  - Two back-to-back `.get()` calls to the same host sleep ≥ `min_interval` (inject a fake clock; assert on the recorded sleeps, never wall-clock).
  - Every fetch writes one `harvest_manifest` row carrying url, sha256, fetched_at, status and headers.
- [x] **Step 2:** Add `harvest_manifest` to `SCHEMA` in `db.py` (`CREATE TABLE IF NOT EXISTS`, so existing databases pick it up on the next `init_schema()` with no migration) plus `record_fetch(**f)`.
- [x] **Step 3:** Implement `http.py` with `urllib.request` — no new dependency for HTTP. The rate limiter is per-host and holds the timestamp of the last request in a dict.
- [x] **Step 4:** Run `uv run pytest tests/test_http.py -q` and `uv run ruff check .`.
- [x] **Step 5:** Commit — `feat: polite HTTP fetcher with on-disk cache and provenance manifest`.

**Requirements:** ≤ 1 req/s per host (default 1.5 for headroom); never re-download an unchanged file; the manifest is the provenance record spec §6 demands. `min_interval` is a constructor argument so tests do not sleep.

---

## Task 2: OAB index scraper and artifact selection

**Files:** Create `src/bqpp/harvest/oab_site.py`, `tests/test_oab_site.py`, `tests/fixtures/oab_site/{seed_page.html,exam_index.html}`. Modify `config/sources.yaml`.

**Interfaces:**
- Produces: `parse_exam_ids(html) -> list[Exam]` (`Exam.id`, `Exam.label`); `parse_exam_index(html) -> list[IndexEntry]` (`.href`, `.label`, `.date: date | None`, `.area: str | None`, `.variant: str | None`); `select_penal_padrao(entries) -> tuple[IndexEntry, str] | None` returning the entry and which rung of the ladder it came from (`"definitivo"` / `"plain"`).

- [x] **Step 1: Write the failing tests** against committed real HTML (trimmed to the relevant `<select>` / anchor block, never hand-invented).
  - `parse_exam_ids` finds 46 exams from the seed page and **drops the `value="0"` placeholder**.
  - `parse_exam_index` returns 41 PDF entries for the 44º, each with a parsed `date`.
  - `select_penal_padrao` prefers `Padrão de respostas definitivo (Direito Penal)` over `Padrão de respostas (Direito Penal)` and reports rung `"definitivo"` (B10).
  - It falls back to the plain padrão and reports `"plain"` when no definitivo exists.
  - It matches all 10 observed label spellings, including `Padrão de Respostas Definitivo- Penal` and `Padrão de respostas - Direito Penal` (B10).
  - It does **not** match `(Direito Civil)`, `(Direito Tributário)`, or a 1ª-fase `Caderno de Prova - Tipo 1`.
  - A `Porto Alegre/RS` / `Reaplicação Porto Velho/RO` label yields a populated `.variant` and is kept as a separate entry, not deduped (B11).
  - `exam_year` comes from the `dd/mm/yyyy` label prefix; a URL under `/arquivos/2019/10/` for an XXV-era exam must **not** produce year 2019 (B4).
- [x] **Step 2:** Implement with `re` + `html.unescape`, consistent with how the project already parses (no new HTML dependency for two fixed page shapes).
- [x] **Step 3:** Add the `oab-2f-penal` source entry to `config/sources.yaml` — `adapter: oab_site`, `seed_url`, `exam_url_template`, `area: "Direito Penal"`, `banca: FGV`, `carreira: oab`, and a `notes` block recording the B1 amendment.
- [x] **Step 4:** `uv run pytest tests/test_oab_site.py -q` and `uv run ruff check .`.
- [x] **Step 5:** Commit — `feat: OAB exam index scraper with definitivo-first artifact selection`.

**Requirements:** URLs live in `sources.yaml`, never as literals in code. Label matching is tolerant (`re.I`, optional article, en-dash or hyphen), because the vocabulary drifts across 15 years of exams.

---

## Task 3: PDF text extraction and text-layer health

**Files:** Create `src/bqpp/parse/__init__.py`, `src/bqpp/parse/pdf.py`, `tests/test_parse_pdf.py`, `tests/fixtures/oab_site/padrao_44.pdf`. Modify `pyproject.toml`.

**Interfaces:**
- Produces: `extract_text(path_or_bytes) -> str` (pages joined with `\n`); `text_health(text) -> Literal["ok", "no_text_layer", "glyph_unmapped"]`.

- [x] **Step 1: Write the failing tests.**
  - `extract_text` on the committed 44º padrão returns > 15 000 chars and contains `PADRÃO DE RESPOSTA` and `Gabarito Comentado`.
  - `text_health("")` and whitespace-only ⇒ `"no_text_layer"`.
  - `text_health` on a synthetic string with a high `(cid:N)` / non-Latin ratio ⇒ `"glyph_unmapped"`; on the real 44º text ⇒ `"ok"`.
- [x] **Step 2:** Add `pdfplumber>=0.11` to `[project].dependencies`; `uv sync --extra dev`.
- [x] **Step 3:** Implement. Import `pdfplumber` **inside** the function, matching how `hf_datasets.py` defers `huggingface_hub`/`pyarrow` — the CLI must stay fast to start and importable without the parsing extras loaded.
- [x] **Step 4:** `uv run pytest tests/test_parse_pdf.py -q`; `uv run ruff check .`.
- [x] **Step 5:** Commit — `feat: pdfplumber text extraction with text-layer health check`.

**Requirements:** No OCR (B9). The health check exists to make a bad document *loud*, not to repair it. `extract_text` must never raise on a malformed PDF — catch, log, return `""`, and let `text_health` classify it.

---

## Task 4: Padrão de resposta segmenter

**Files:** Create `src/bqpp/parse/padrao.py`, `tests/test_padrao.py`, fixtures `padrao_xxxiii.txt`, `padrao_xxv.txt`, `padrao_vii.txt`, `padrao_44.expected.json`.

**Interfaces:**
- Produces: `segment_padrao(text) -> list[Section]` where `Section` has `.number: str` (`"peca"`, `"1"`…`"4"`), `.format: Literal["peca","dissertativa"]`, `.stem: str`, `.rationale: str`, `.usable: bool`.

- [x] **Step 1: Write the failing tests.** Generate every `.txt` fixture with **pdfplumber** (B8) from the real PDFs, then hand-check.
  - The 44º (ALL-CAPS sub-headers) yields exactly 5 sections: 1 `peca` + 4 `dissertativa`, all `usable`, matching `padrao_44.expected.json` — the spec §13 acceptance fixture.
  - XXXIII (Title-Case `Enunciado`/`Gabarito Comentado`) yields 5 usable sections. **This is the B7 regression test**: a case-sensitive regex passes the 44º and fails here.
  - XXV yields 5 sections despite the `– B005250` booklet-code suffix, and `.number` is `"1"`, not `"1 – B005250"` (B7).
  - Zero-padded `QUESTÃO 01` normalises to `.number == "1"` (B7).
  - VII (no `Enunciado`/`Gabarito Comentado` sub-headers) yields sections with a rationale but an empty stem, all `usable=False` (B9).
  - Page furniture — `ORDEM DOS ADVOGADOS DO BRASIL`, `Página N de M`, `ÁREA: DIREITO PENAL`, `Aplicada em …`, the `gabarito preliminar` disclaimer — never appears in `.stem` or `.rationale`.
  - Sub-item markers `A)` / `B)` and `(Valor: 0,65)` survive **verbatim** in the stem (spec §15: verbatim, never paraphrased).
- [x] **Step 2:** Implement. Section anchors and sub-headers all compile with `re.I`; the footer stripper is a single line-level regex applied to both stem and rationale. `usable = len(stem) >= 80 and len(rationale) >= 80`.
- [x] **Step 3:** `uv run pytest tests/test_padrao.py -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: padrão de resposta segmenter with per-section quality gate`.

**Requirements:** Pure function, no I/O. Measured target: 175 sections across 35 exams, 139 usable. A change that lowers either number is a regression.

---

## Task 5: Ingestion with deduplication

**Files:** Modify `src/bqpp/harvest/oab_site.py`, `src/bqpp/db.py`, `tests/test_oab_site.py`.

**Interfaces:**
- Produces: `ingest_padrao(text, *, exam, entry, rung, db, force) -> int`; `harvest_source(entry, db, settings, *, dry_run, force) -> int` (same signature as the `hf_datasets` one, so the CLI dispatch is uniform).
- Consumes: `Database.stem_hashes() -> dict[str, str]` (normalised stem hash → question id).

- [x] **Step 1: Write the failing tests.**
  - One `SourceDocument` per exam: `kind="gabarito_justificado"`, `banca="FGV"`, `carreira="oab"`, `certame="OAB 44º Exame (2ª fase)"`, `exam_year` from the label date (B4), `id` = sha256 of the PDF bytes.
  - Questions get `format` `dissertativa`/`peca`, `answer_rationale` populated, `answer_key=None`, and `question_number` `"peca"`/`"1"`…`"4"`.
  - `usable=False` sections are **skipped, not stored with an empty stem** (B9), and the skip is logged.
  - Re-running ingests 0 new questions (idempotence), and `--force` re-writes them.
  - A question whose normalised stem already exists from `hf-oab-bench` is skipped as a duplicate (B13).
  - Stem normalisation collapses whitespace and case-folds before hashing, so a re-flowed line break is not a new question.
- [x] **Step 2:** Implement. `_exam_document()` mirrors the one in `hf_datasets.py` — one `source_documents` row per exam, because **`exam_year` is what makes the law watchlist fire** (the lesson already recorded in that module).
- [x] **Step 3:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: ingest OAB 2ª-fase padrões with stem-hash deduplication`.

**Requirements:** No `ALTER TABLE`. The professor's live `data/corpus.sqlite` holds the usage log and must survive this milestone untouched — dedup reads existing stems into memory (the corpus is ~350 rows).

---

## Task 6: CLI wiring — adapter dispatch and `bqpp parse`

**Files:** Modify `src/bqpp/cli.py`, `tests/test_cli.py`.

- [x] **Step 1: Write the failing tests.** `bqpp parse --help` exits 0; `harvest --source oab-2f-penal --dry-run` neither writes rows nor opens a socket; an unknown adapter still prints the "lands in M3" notice rather than crashing.
- [x] **Step 2:** Replace the hardcoded `if entry.adapter != "hf_datasets"` check with a dispatch table `{"hf_datasets": …, "oab_site": …}`, and add `bqpp parse [--source ID]` for re-segmenting already-cached PDFs without re-fetching (spec §12 lists `parse` as a first-class command; it has had no implementation until now).
- [x] **Step 3:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: adapter dispatch and the bqpp parse command`.

**Requirements:** `cli.py` still holds **no business logic**. `parse` re-reads from the fetch cache, so it is safe to iterate on the segmenter without touching the network.

---

## Task 7: Manual seed path for uncovered subtopics

**Files:** Create `src/bqpp/seed.py`, `config/seed_questions.yaml`, `tests/test_seed.py`. Modify `src/bqpp/models.py`, `src/bqpp/cli.py`, `.gitignore` if the professor wants his seeds private.

This exists because T1.1 and T3.3 have zero candidates and the OAB exam is unlikely ever to supply them (they are doctrinal topics objective exams avoid). The durable fix is M3/Cebraspe; this unblocks the course now.

- [x] **Step 1: Write the failing tests.**
  - A seed entry with explicit `subtopic_ids` is ingested **pre-classified** (`classify_model="manual"`, `classified_at` set) so `bqpp classify` skips it — the professor's own classification is not second-guessed by an LLM.
  - It still flows through `bqpp vet` normally.
  - Invalid subtopic ids are rejected loudly against `taxonomy.yaml` rather than stored.
  - Re-seeding the same file is idempotent (id = sha256 of the seed file + entry key).
  - A seed entry lacking `stem` or `subtopic_ids` raises with the offending entry named.
- [x] **Step 2:** Add `"manual"` to the `Kind` literal in `models.py` (TEXT column, no migration). Implement `seed.py` and `bqpp seed [--file PATH]`.
- [x] **Step 3:** Ship `config/seed_questions.yaml` as a **commented empty template** documenting every field, with T1.1 and T3.3 named as the reason it exists.
- [x] **Step 4:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 5:** Commit — `feat: manual seed path for subtopics no exam covers`.

---

## Task 8: End-to-end test, README, and a real run

**Files:** Modify `README.md`, `CLAUDE.md`, `tests/test_end_to_end.py`.

- [x] **Step 1:** Extend the end-to-end test: fixture padrão → segment → ingest → classify (fake LLM) → vet → curate, asserting a discursive item with a rationale reaches a shortlist and its `<details>` block carries the **Gabarito comentado da banca**. Still no network, still no real LLM.
- [x] **Step 2:** Update `README.md` — M2 in scope, the OAB source and why it is not FGV (B1), `bqpp parse` and `bqpp seed`, and a note that the first `harvest` downloads ~45 PDFs at ~1.5 s apart (roughly two minutes).
- [x] **Step 3:** Update the milestone table and empty-subtopic line in `CLAUDE.md`.
- [x] **Step 4:** Full verification: `uv run pytest -q` (all green) and `uv run ruff check .`.
- [x] **Step 5:** **Real run** against the live site and a real LLM:
  ```bash
  uv run bqpp harvest --source oab-2f-penal -v
  uv run bqpp classify -v && uv run bqpp vet -v
  uv run bqpp curate --semester 2026.2 && uv run bqpp stats
  ```
- [x] **Step 6:** Commit — `feat: M2 end-to-end — OAB 2ª-fase padrões in the shortlists`.

---

## Result (real run, 2026-08-08)

All eight tasks landed; 171 tests green, ruff clean. Measured against the definition of done below:

| | Target | Actual |
|---|---|---|
| Padrões fetched | 45 | **45** (92 manifest rows = 47 index pages + 45 PDFs, all `https`) |
| Exams segmented | 35 | **35** — the 10 pre-convention exams logged by name and skipped |
| Sections / usable | 175 / 139 | **175 / 139** |
| Questions ingested | ~139 | **134** — the other 5 were the 44º, correctly deduped against `oab-bench` |
| Corpus total | ~345 | **341** (was 207) |
| `dissertativa` | ~135 | **131** (was 24); `peca` 33 (was 6) |
| Questions with a rationale | — | **164** (was 30) |
| Re-run ingests nothing | yes | **yes**, and with no network traffic — every URL a cache hit |
| `usage_log` preserved | yes | **yes** (backed up to `data/corpus.sqlite.pre-m2.*.bak` first) |

Subtopic coverage after curating: every subtopic grew, several sharply — T2.4 25→63, T4.2 19→69,
T2.7 10→26, T2.2 11→23, T1.4 8→17, T3.5 5→15.

**T1.1 went from 0 to 3 candidates**, which this plan did not predict: the OAB 2ª fase does set
discursive questions on the *marco regulatório* of medidas cautelares, even though the objective exam
avoids the topic. **T3.3 (standards probatórios) is the only subtopic still empty**, as expected —
it remains M3/seed work.

Two things the run surfaced that are *not* M2 defects:

1. One pre-existing M1 `mcq4` fails both classification and vetting because Gemini returns an empty
   body for it (almost certainly a safety filter on the fact pattern). It never reaches the OpenAI
   fallback, because `llm/fallback.py` deliberately falls through only on `LLMTransientError` and an
   empty body surfaces as a validation error. Defensible as written, but an empty response is more
   plausibly a provider refusal than a schema problem. Worth revisiting on its own.
2. The `DISTRIBUIÇÃO DOS PONTOS` scoring table is carried into `answer_rationale` verbatim, and its
   two-column layout re-flows awkwardly in markdown. The content is useful — it is how the banca
   weighted each element — so it is kept. Trimming it is a one-line change in `padrao._split_body`
   if the professor prefers a cleaner block.

---

## M2 Definition of Done

- `bqpp harvest --source oab-2f-penal` fetches 45 padrões, obeying ≤ 1 req/s, writing a `harvest_manifest` row per file, and re-running downloads nothing.
- The corpus gains **~139 discursive/peça questions with `answer_rationale` populated**, taking the total from 207 to ~345.
- `bqpp stats` shows `dissertativa` rising from 24 to ~135, and no subtopic that had candidates loses them.
- Shortlists contain OAB 2ª-fase items whose `<details>` block shows the banca's own **Gabarito Comentado**.
- The full suite passes with no network and no LLM; ruff is clean.
- `data/corpus.sqlite` retains every `usage_log` row it had before the milestone.

---

## Deferred (explicitly not M2)

| Item | Why it is deferred |
|---|---|
| **1ª-fase objective provas** | The professor scoped M2 to the 2ª fase. Needs two-column coordinate reflow, bold-numeral segmentation, per-exam alternative markers (`A)` vs `(A)`) and a glyph-health gate — the 42º Tipo 1 is confirmed mojibake. Triples the parser work for the format ranked lowest. Revisit as M2.5. |
| **The 10 no-anchor exams** (2010.2, 2010.3, IV, V, VI, XI–XIV, 39º) | Pre-convention layouts. XII–XIV do carry `Enunciado`/`Gabarito Comentado` under a `DISCIPLINA: DIREITO PENAL` header and are the cheapest future win. 39º is already covered by `oab-bench`. |
| **The 36 stem-less sections** | Rasterised enunciados (XXXVIII, 40º–43º) and pre-2013 layouts. Recoverable by pairing the 2ª-fase *caderno*, which reintroduces the join logic B5 deleted. 40º–43º are already covered by `oab-bench`, so the real loss is small. |
| **The FGV ASP.NET postback client** | `oab.fgv.br` serves byte-identical files behind three requests per exam instead of one. Write it only if the OAB host goes away. |
| **Cebraspe / generic MP-TJ PDFs** | M3. Needs `item_type` and `stem_context` schema fields, because certo/errado items share a common *comando* paragraph — a real schema change. |
| **`russ7/oab_exams_2011_2025_combined`** | MIT-tagged HF mirror, ~966 post-2018 MCQ rows. Rejected as a corpus source: no banca rationale, incomplete editions (66–80 of 80), demonstrably mislabelled `question_type`, and a third party's licence tag confers no rights over OAB text. Worth keeping only as a **cross-validation oracle** — an item whose answer key two independent sources dispute should not open a class. |
