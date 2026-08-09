# banco-questoes-pp — M3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break the corpus out of a single banca. Every one of its 361 questions is FGV/OAB; M3 adds **delegado, defensoria and Ministério Público** material from four verified self-contained documents, and — more durably — ships the *architecture* that makes any future concurso a one-line YAML addition rather than an engineering task.

**Architecture:** Two new harvest adapters reuse M2's machinery unchanged. `harvest/cebraspe.py` discovers artifacts through Cebraspe's public JSON API; `harvest/generic_pdf.py` walks a hand-curated URL manifest. Both feed a shared column-aware extraction layer (`parse/columns.py`) and one of two readers: `parse/caderno.py` for Cebraspe's combined *prova + gabarito + justificativa* documents, and `parse/objetiva.py` for A–E provas with an answer grid. Everything lands in the same `Question` rows `classify → vet → curate` already consume.

**Tech Stack:** unchanged. No new dependencies — `pdfplumber` (M2) does the column work.

**Scope decided with the professor (2026-08-09):**
- **Self-contained artifacts only.** Every document carries its own answer key. No prova↔gabarito join across files, no preliminar/definitivo ladder, no 84-certame justificativa tier.
- **Certo/errado accepted, but only short-comando items.** A length heuristic at ingest rejects items hanging off multi-paragraph fact patterns, which read as fragments when lifted.
- **T3.3 opens from doctrine, not from an exam question.** Harvesting demonstrably will not close it (see C9), so the taxonomy must say so and stop reporting it as a gap.
- Widening to other web sources is deliberately **deferred to a later milestone**, not attempted here.

---

## Global Constraints

The M0/M1 and M2 Global Constraints apply verbatim. The ones M3 actually exercises:

- **Only public, official sources.** M3 fetches from `apis.cebraspe.org.br`, `cdn.cebraspe.org.br`, `www.mprs.mp.br` and `www.mpf.mp.br` — bancas and public bodies publishing their own exams. No commercial aggregator.
- **Harvest etiquette** (spec §6): `[harvest] user_agent`, ≤ 1 request/second per host, URL-hash cache, manifest row per download. All of this already exists in `harvest/http.py` and M3 must go through it — it remains **the only module that opens a socket**.
- **`parse/` stays pure**: bytes or text in, dataclasses out. No network, no database.
- **Verbatim text, never paraphrased**; full attribution on every shortlist entry.
- **Idempotent**, deterministic ids, `--force` redoes work without destroying LLM results (M2's `_carry_stage_fields`).
- **No exam PDFs committed.** `.gitignore` blocks `tests/fixtures/**/*.pdf`; fixtures are pdfplumber-extracted text.

---

## Spec Amendments (verified against live sources, 2026-08-08/09)

These correct §6, §8 and §13. **Implement the amended values.** Every row was observed directly — a 490-event API sweep, 97 cached certame manifests, and download-plus-parse of all four seed documents.

| # | Spec says | Reality | Consequence |
|---|---|---|---|
| **C1** | M3 is a "certo/errado pipeline" over delegado + magistratura + MP | Format does not track career. Verified C/E: PC/DF 26 Delegado, DP/DF 19 Defensor, PF 2018. Verified A–E: MPRS 50º Promotor, MPF 31º Procurador. Reported A–E for TJDFT, TJ/PA, MP/CE, MP/BA and the PC/PE and PC/CE **delegado** exams. | Format must be **detected from the artifact**, never inferred from the career. M3 ships two readers, and the source registry declares which one a source uses. |
| **C2** | §13: Cebraspe publishes no per-item banca rationale | Refuted. Two certames publish a **combined caderno** — item text, answer and the banca's justificativa in one file. Verified: `PC_DF_26_DELEGADO` (120 items, 120 justificativas, 65 CERTO / 55 ERRADO) and `DP_DF_19_DEFENSOR` (198 justificativas, 94 CERTO / 104 ERRADO). | Cebraspe is a rationale source and this genre is M3's headline artifact — the same self-contained shape that made M2 cheap. |
| **C3** | §13: justificativas print item text without numbers | Inverted. Standalone justificativa files print `ITEM \| GABARITO PRELIMINAR \| GABARITO DEFINITIVO \| SITUAÇÃO` and **no item text**. Irrelevant to M3 under the chosen scope, but it is why the 84-certame tier was excluded: it needs a cross-file join that is wrong at the source in at least one verified case (PF 2018's justificativa cites item 19 for text in item 20, where the grid's X sits). | Recorded so a later milestone does not rediscover it. |
| **C4** | Cebraspe needs HTML scraping / hand-curated links | Three unauthenticated GETs, no auth, no cookies, no JS: seed `apis.cebraspe.org.br/cebraspe/eventos/tipo/concursos/` → per-slug `…/eventos/{slug}` → `cdn.cebraspe.org.br/concursos/{slug}/arquivos/{nomeArquivo}`. `robots.txt` on `www` is `User-agent: * / Disallow:` (nothing disallowed); no terms-of-use page exists on either host (404). | Enumerate Cebraspe; curate everything else. |
| **C5** | (unstated) the seed is a list of events | It is a list of **4 fase groups**, each carrying an `eventos` array — 490 events total. Reading it as a flat list yields 4. | Flatten `[e for g in seed for e in g["eventos"]]`. This cost a wasted survey run. |
| **C6** | (unstated) the manifest tells you the file extension | `tipoExtensaoArquivo` is `_.pdf` **while `nomeArquivo` already ends in `.pdf`**. Joining them produces `.pdf.pdf` → 404. | Ignore `tipoExtensaoArquivo` entirely; use `nomeArquivo` verbatim, percent-encoded, case preserved. |
| **C7** | (unstated) a file's description identifies it | Neither field alone works. `PC_DF_26`'s description is `PROVA OBJETIVA (P1) E GABARITO PRELIMINAR COM JUSTIFICATIVAS`; `DP_DF_19`'s is merely `PROVA OBJETIVA` and only its filename `…_C_JUST.PDF` betrays the genre. Matching on description alone finds 1 of 2; matching loosely on both finds false positives (`TJ_PR_16_JUIZ` and `DPE_RS_21_DEFENSOR` matched a naive regex and have **zero** justificativas). | Classify on description **and** filename, accent- and case-normalised, then **assert on content** after download: a combined caderno must yield ≥ 20 `JUSTIFICATIVA - (CERTO\|ERRADO)` matches or it is not the genre. |
| **C8** | (unstated) `<FimJust>` delimits items | `PC_DF_26` (2026) prints 105 `<FimJust>` sentinels; `DP_DF_19` (2019) prints **zero**, in the same genre. | Do **not** depend on the sentinel. Segment on the item number and take the verdict from `JUSTIFICATIVA\s*[-–]\s*(CERTO\|ERRADO)`. Strip the sentinel if present. |
| **C9** | (implicit) M3 fills the remaining subtopic | ~770 questions were searched across seven certames for *standard probatório*, *além da dúvida razoável*, *persuasão racional*, *íntima convicção*, *prova tarifada*: **zero hits anywhere**. What exists is art. 155 CPP *valoração* — the MPF 31º prova has exactly 1 such hit in 120 questions. | **T3.3 will not be closed by harvesting.** The professor opens it from doctrine and case law. The taxonomy must record that, and `stats`/`curate` must stop reporting it as a coverage gap. |
| **C10** | §8 data model is sufficient | Cebraspe items hang off a shared *comando* paragraph — 36 comandos govern 120 items in PF 2018, and items 99/100 begin in lowercase and only parse when glued to their comando. **Measured:** a real comando is ~483 chars, and `models.stem_hash` keys on the first 300 — so prefixing the comando into each stem makes every item in a block hash **identically**, and M2's dedup silently drops all but the first. | The comando needs its own column. `ALTER TABLE questions ADD COLUMN stem_context TEXT` is O(1) in SQLite, rewrites nothing, defaults existing rows to NULL and never touches `usage_log`. This is the project's first migration and needs a guarded, tested path. |
| **C11** | (unstated) two-column PDFs extract linearly | `pdftotext -layout` interleaves columns on almost everything: MPF recovers 84 of 120 items, and the DP/DF caderno's justificativas splice across columns mid-sentence. Cropping each page at its midpoint and reading left-then-right fixes all four seed documents — verified: DP/DF 198 justificativas, PC/DF 120/120. MPRS is single-column and needs no crop. | A column-aware extractor is a **shared prerequisite**. Build it first, and let the source registry declare `columns: 1` or `2`. |
| **C12** | §13 annulment is `X` | Four conventions across the seed set: `X` in Cebraspe C/E grids (legend `Obs.: ( X ) item anulado.` printed in-file; PF 2018 verified at items 20/32/45/76); `ANULADA` spelled out in MPRS's grid (items 1, 18, 20, 35, 36, 45, 79); `*N` in MPF; `Deferido com anulação` in justificativa tables. | `nullified` detection is a per-source table, never a constant, and ingestion must **refuse** a grid whose convention was not positively identified rather than defaulting to "not annulled". |
| **C13** | (unstated) answer grids parse line by line | They do not. Cebraspe grids are **paired rows** — an `Item` line of numbers and a `Gabarito` line of letters directly beneath, aligned by column; a per-line regex returns **zero** entries. MPRS's grid is **interleaved four-column** (`1 ANULADA │ 26 A │ 51 C │ 76 E` on one physical line). | Two distinct grid readers. Cebraspe: pair `Item`/`Gabarito` rows and zip. MPRS: split each line into 4 `(number, answer)` cells. Both verified to recover 120/120 and 100/100. |
| **C14** | (unstated) URLs can be constructed by pattern | Actively unsafe. MPRS ordinal substitution (49º, 48º, 51º) returns **404**; the MPRS `provas` index page 404s; MPF ordinal substitution reportedly returns **401**. The MPF path is `/o-mpf/concursos/concursos-de-procuradores/…`, **not** the `/pgr/concursos/…` an earlier report claimed — that report's byte counts were right and its URL was wrong. | Never construct a non-Cebraspe URL. The manifest holds literal, verified URLs, and each entry records the date it was confirmed. |
| **C16** | (unstated) a question's topic is in its text | For Cebraspe C/E items it frequently is **not**. Re-running the T3.3 search over the two cadernos: **0 hits in item text** — which is all the original search covered, hence its zero — versus 6 items once the comando and justificativa are included. PC/DF item 87's topic (*valoração da prova*) appears only in its comando; the item itself is about prova pericial. | Search **and classify** over comando + item + justificativa, never item text alone. Already handled: `stem_context` is in `to_prompt_payload`, so `classify` sees the comando. Any future widening milestone must do the same, or it will systematically miss the questions it is looking for. Note the corrected yield is still only ~2–3 genuine T3.3 items across the whole seed set, so C9 stands. |
| **C15** | (unstated) MPF PDFs are plain | The MPF prova carries **344 invisible Unicode `Cf` characters**, which make keyword matching silently fail. Its Plone links end in `/view`; the file is at `{path-without-/view}/@@download/file`. | Add an unconditional `Cf`-strip to extraction. Record the download rule in the manifest, not in code. |

**Measured seed corpus** — all four downloaded, parsed and counted:

| Source | Format | Items | Notes |
|---|---|---|---|
| `PC_DF_26_DELEGADO` (Cebraspe) | certo/errado + justificativa | 120 / 120 | ~26 with processo-penal signal; 2 columns |
| `DP_DF_19_DEFENSOR` (Cebraspe) | certo/errado + justificativa | 198 justificativas | 2 columns; no `<FimJust>` |
| MPRS 50º | mcq5 + grid | 100 | single column; 7 `ANULADA` |
| MPF 31º | mcq4 + grid | 120 | 2 columns; 344 `Cf` chars; 1 valoração item |

Expect **60–100 questions** to survive classification as *processo penal*, across **four new carreiras**. That is modest against 361, and it is not the point: M3's payoff is banca diversity — it activates the `carreira` tie-break in `curate.rank_candidates`, which today does nothing because every question is `oab`.

---

## File Structure

```
config/
  sources.yaml            # T4/T7  + cebraspe-cadernos and manual-provas entries
  provas_manifest.yaml    # T7  curated literal URLs, each with a verified-on date
  taxonomy.yaml           # T8  + opens_with: doutrina on T3.3
src/bqpp/
  models.py               # T1  Question.stem_context
  db.py                   # T1  guarded ADD COLUMN migration
  curate.py               # T1/T8  render context above the stem; doctrine-subtopic message
  parse/
    columns.py            # T2  column-aware extraction + Cf-strip  (shared prerequisite)
    caderno.py            # T3  Cebraspe combined caderno -> items + comando + rationale
    objetiva.py           # T6  A-E prova + the two grid readers
  harvest/
    cebraspe.py           # T4/T5  API discovery, genre selection, ingestion
    generic_pdf.py        # T7  curated manifest -> ingestion
tests/
  fixtures/cebraspe/      # extracted TEXT only, never PDFs
  test_columns.py test_caderno.py test_objetiva.py test_cebraspe.py test_generic_pdf.py
  test_migration.py
```

---

## Task 1: `stem_context`, the first schema migration

**Files:** Modify `src/bqpp/models.py`, `src/bqpp/db.py`, `src/bqpp/curate.py`, `src/bqpp/export.py`. Test: `tests/test_migration.py`, extend `tests/test_db.py`, `tests/test_curate.py`.

- [x] **Step 1: Write the failing tests.**
  - Opening a database created by the **M2 schema** (build one by executing the previous `SCHEMA` string) and calling `init_schema()` adds `stem_context` and preserves every existing row, including `usage_log`.
  - Running `init_schema()` twice is a no-op — no duplicate-column error.
  - A `Question` round-trips `stem_context` through `upsert_question` / `get_question`.
  - `stem_context` survives a `--force` re-ingest exactly as the other content columns do.
  - `curate.render_shortlist` prints the context **above** the stem, visually distinguished, and omits the block entirely when it is NULL.
  - `export_jsonl` includes it.
  - **Regression for C10:** two items sharing one 480-char comando but with different item text produce **different** `stem_hash` values when the comando is in `stem_context` rather than prefixed to `stem`.
- [x] **Step 2:** Implement. The migration is a guarded `ALTER TABLE`, run inside `init_schema()` after `executescript(SCHEMA)`:
  ```python
  cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(questions)")}
  if "stem_context" not in cols:
      self.conn.execute("ALTER TABLE questions ADD COLUMN stem_context TEXT")
  ```
  Add the column to `SCHEMA` too, so fresh databases get it directly.
- [x] **Step 3:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 4:** **Back up the live database before running anything against it** (`cp data/corpus.sqlite data/corpus.sqlite.pre-m3.bak`), then `uv run bqpp stats` to confirm the migration applied cleanly to the real 361-row corpus.
- [x] **Step 5:** Commit — `feat: stem_context column and the first guarded schema migration`.

**Requirements:** `ADD COLUMN` is O(1) and non-destructive, but this is the first time the project migrates a database whose `usage_log` is the only record of what was taught. The test that opens an M2-era database is the point of this task.

---

## Task 2: Column-aware extraction

**Files:** Create `src/bqpp/parse/columns.py`, `tests/test_columns.py`, `tests/fixtures/cebraspe/*.txt`.

**Interfaces:** `extract_columns(source, *, columns: int = 2) -> str`; `strip_format_chars(text) -> str`.

- [x] **Step 1: Write the failing tests.**
  - `strip_format_chars` removes Unicode `Cf` characters and leaves ordinary pt-BR text untouched (C15).
  - With `columns=1` the function is equivalent to M2's `extract_text` plus the `Cf` strip.
  - Against a committed two-column fixture, reading order is left-column-then-right: a sentence that spans lines in the left column is contiguous, and no right-column text is interleaved into it.
  - A page whose crop yields nothing does not raise.
- [x] **Step 2:** Implement by cropping each page at its midpoint with pdfplumber and concatenating left then right. Verified sufficient for all four seed documents; a word-clustering splitter is **not** needed and must not be built speculatively.
- [x] **Step 3:** `uv run pytest tests/test_columns.py -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: column-aware PDF extraction with invisible-character stripping`.

---

## Task 3: Cebraspe combined-caderno reader

**Files:** Create `src/bqpp/parse/caderno.py`, `tests/test_caderno.py`, fixtures `pc_df_26.txt`, `dp_df_19.txt` (extracted text, two eras).

**Interfaces:** `segment_caderno(text) -> list[CadernoItem]` with `.number`, `.comando`, `.stem`, `.answer_key` (`"C"`/`"E"`), `.rationale`, `.usable`.

- [x] **Step 1: Write the failing tests.**
  - `PC_DF_26` yields **120 items**, each with a rationale and an answer key; the CERTO/ERRADO split is 65/55.
  - `DP_DF_19` yields its items **despite having no `<FimJust>` sentinel** — the C8 regression test.
  - A `<FimJust>` sentinel, where present, never leaks into `stem` or `rationale`.
  - Each item carries the comando of the block it belongs to, and a new comando starts a new block.
  - **The short-comando gate (the professor's scope decision):** an item whose comando is a topic sentence is `usable`; one hanging off a multi-paragraph fact pattern is not. Test both against real fixture blocks, and pin the threshold as a named constant.
  - An item whose text is a lowercase fragment is still stored with its comando intact, so it remains intelligible.
  - Page furniture (`-- PROVA OBJETIVA --`, page numbers, the caderno's instruction boilerplate) never reaches `stem` or `rationale`.
- [x] **Step 2:** Implement. Segment on `^\s*(\d{1,3})\s+(?=\S)`; verdict from `JUSTIFICATIVA\s*[-–]\s*(CERTO|ERRADO)`; comando is the prose preceding the first item of a block, typically ending `julgue os itens...`.
- [x] **Step 3:** `uv run pytest tests/test_caderno.py -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: Cebraspe combined-caderno reader with short-comando gate`.

---

## Task 4: Cebraspe API client and genre selection

**Files:** Create `src/bqpp/harvest/cebraspe.py`, `tests/test_cebraspe.py`, fixtures `seed_page.json`, `event_pc_df_26.json` (trimmed real API responses). Modify `config/sources.yaml`.

**Interfaces:** `parse_seed(json_bytes) -> list[Certame]`; `parse_manifest(json_bytes) -> list[Artifact]`; `select_combined_caderno(artifacts) -> Artifact | None`; `cdn_url(slug, artifact) -> str`.

- [x] **Step 1: Write the failing tests.**
  - `parse_seed` flattens the **4 fase groups** into 490 events (C5) — a naive read returns 4 and must fail this test.
  - `cdn_url` ignores `tipoExtensaoArquivo` and never produces `.pdf.pdf` (C6).
  - `cdn_url` percent-encodes names with spaces and accents, preserving case.
  - `select_combined_caderno` finds `PC_DF_26` by its description **and** `DP_DF_19` by its `_C_JUST.PDF` filename (C7).
  - It returns `None` for `TJ_PR_16_JUIZ`-style manifests whose description merely contains both words — the false-positive regression.
  - An empty body (HTTP 204 for an unknown slug) is treated as not-found, not a JSON parse error (C11).
- [x] **Step 2:** Implement. Add the `cebraspe-cadernos` source entry with an **explicit certame allow-list** — `PC_DF_26_DELEGADO`, `DP_DF_19_DEFENSOR` — plus the seed/event URL templates and `columns: 2`.
- [x] **Step 3:** `uv run pytest tests/test_cebraspe.py -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: Cebraspe API client with content-asserted genre selection`.

**Requirements:** The allow-list is explicit config, per spec §6. Discovery over all 490 events is **not** wired into `harvest` — it was used once to build the allow-list, and re-running it belongs in the deferred milestone.

---

## Task 5: Cebraspe ingestion

**Files:** Modify `src/bqpp/harvest/cebraspe.py`, `src/bqpp/cli.py`. Test: extend `tests/test_cebraspe.py`.

- [x] **Step 1: Write the failing tests.**
  - One `SourceDocument` per certame: `kind="gabarito_justificado"`, `banca="CEBRASPE"`, `carreira` from config (`delegado`, `defensoria`), `exam_year` from the certame, `certame` human-readable.
  - Questions get `format="certo_errado"`, `answer_key` `"C"`/`"E"`, `answer_rationale` populated, and `stem_context` holding the comando.
  - Items failing the short-comando gate are **skipped and logged**, never stored.
  - **Content assertion (C7):** a downloaded file yielding fewer than 20 justificativas is rejected with a loud error rather than ingested as an empty certame.
  - Idempotent; `--force` re-ingests without destroying classification or vetting.
  - Dedup by `stem_hash` still works — and the C10 regression holds on real data: items sharing a comando produce distinct hashes.
- [x] **Step 2:** Implement; register the adapter in `cli._adapters()` and add it to `_OFFLINE_CAPABLE` so `bqpp parse` covers it.
- [x] **Step 3:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: ingest Cebraspe combined cadernos`.

---

## Task 6: A–E prova and answer-grid readers

**Files:** Create `src/bqpp/parse/objetiva.py`, `tests/test_objetiva.py`, fixtures `mprs_50.txt`, `mpf_31.txt`, `mpf_31_gab.txt`.

**Interfaces:** `segment_objetiva(text) -> list[ObjetivaItem]` (`.number`, `.stem`, `.choices`, `.usable`); `read_grid(text, *, style) -> dict[int, str]` where `style` is `"paired_rows"` (Cebraspe) or `"interleaved"` (MPRS/MPF); `ANNULMENT_TOKENS: dict[str, set[str]]`.

- [x] **Step 1: Write the failing tests.**
  - MPRS yields **100 questions**, each with 5 choices labelled `A`–`E`.
  - Its grid reads as 100 entries with `ANULADA` at **1, 18, 20, 35, 36, 45, 79** (C12/C13) — the interleaved four-column regression.
  - The Cebraspe paired-row reader recovers **120 entries, C=59 / E=57 / X=4, annulled at 20/32/45/76**; a per-line regex returns zero and must fail this test (C13).
  - MPF yields items with **4** choices, and `*N` is read as annulment.
  - `read_grid` **raises** when no annulment legend or convention is identified, rather than defaulting to "none annulled" (C12).
  - Choice labels are never confused with the enumerated sub-items (`I-`, `II-`) that appear inside MPRS stems.
- [x] **Step 2:** Implement both grid styles and the shared item segmenter.
- [x] **Step 3:** `uv run pytest tests/test_objetiva.py -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: A-E prova reader and the two answer-grid conventions`.

---

## Task 7: Curated manifest adapter

**Files:** Create `src/bqpp/harvest/generic_pdf.py`, `config/provas_manifest.yaml`, `tests/test_generic_pdf.py`. Modify `config/sources.yaml`, `src/bqpp/cli.py`.

- [x] **Step 1: Write the failing tests.**
  - The manifest loads and validates: every entry needs `id`, `url`, `banca`, `carreira`, `certame`, `exam_year`, `format`, `columns`, `grid_style`, `verified_on`. A missing field names the offending entry.
  - A single-file entry (MPRS: prova and grid in one document) and a two-file entry (MPF: prova + separate gabarito) both ingest.
  - Ingestion is idempotent and `--force`-safe.
  - An unreachable URL is logged and **skipped**, and the run continues — the manifest is hand-maintained and will rot.
  - The shipped manifest parses and every entry validates.
- [x] **Step 2:** Implement. Ship the manifest with the four verified entries — MPRS 50º, MPF 31º prova + gabarito — each recording its `verified_on` date and, for MPF, the `/view` → `/@@download/file` rule as a comment (C14/C15).
- [x] **Step 3:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: curated-manifest adapter for non-enumerable official sources`.

**Requirements:** **Never construct a URL by pattern** (C14). The manifest is the source of truth and adding a concurso is a YAML edit.

---

## Task 8: Subtopics that open from doctrine

**Files:** Modify `config/taxonomy.yaml`, `src/bqpp/config.py`, `src/bqpp/curate.py`, `src/bqpp/cli.py`. Test: extend `tests/test_config.py`, `tests/test_curate.py`, `tests/test_cli.py`.

- [x] **Step 1: Write the failing tests.**
  - `Taxonomy` parses an optional `opens_with` on a subtopic and defaults it to `None`.
  - `curate` writes a shortlist for a `opens_with: doutrina` subtopic that says so, instead of "Amplie o corpus (M2/M3) ou revise a taxonomia".
  - `run_curate` does **not** count such a subtopic among those "with no candidates".
  - `bqpp stats` renders its count without the red-zero gap styling.
  - A subtopic with no marker behaves exactly as before.
- [x] **Step 2:** Implement, and mark `T3.3` in `taxonomy.yaml` with a comment recording *why* — ~770 questions searched, zero standards-of-proof hits (C9).
- [x] **Step 3:** `uv run pytest -q`; `uv run ruff check .`.
- [x] **Step 4:** Commit — `feat: subtopics that open from doctrine rather than an exam question`.

**Requirements:** This is a domain decision recorded in config, not a special case in code. Any subtopic may later be marked the same way.

---

## Task 9: End-to-end, docs, and the real run

**Files:** Modify `tests/test_end_to_end.py`, `README.md`, `CLAUDE.md`.

- [x] **Step 1:** Extend the end-to-end test: Cebraspe fixture → segment → ingest → classify (fake LLM) → vet → curate, asserting a certo/errado item reaches a shortlist with its comando rendered above the stem and the banca's justificativa in the `<details>` block. Add the MPRS path likewise. No network, no real LLM.
- [x] **Step 2:** Update `README.md`: the corpus now spans four bancas; what the manifest is and how to add a concurso; why T3.3 opens from doctrine.
- [x] **Step 3:** Update `CLAUDE.md`: milestone table, the migration precedent, and the rule that URLs are never constructed by pattern.
- [x] **Step 4:** Full verification — `uv run pytest -q`, `uv run ruff check .`.
- [x] **Step 5:** **Real run**, after backing up `data/corpus.sqlite`:
  ```bash
  uv run bqpp harvest --source cebraspe-cadernos -v
  uv run bqpp harvest --source manual-provas -v
  uv run bqpp classify -v && uv run bqpp vet -v
  uv run bqpp curate --semester 2026.2 && uv run bqpp stats
  ```
- [x] **Step 6:** Commit — `feat: M3 end-to-end — delegado, defensoria and MP material in the shortlists`.

---

## Result and post-review corrections (2026-08-09)

All nine tasks landed; 297 tests green, ruff clean. Corpus **361 → 817** across four bancas —
FGV 361, CEBRASPE 236, MPF 120, MPRS 100 — covering oab, delegado, defensoria and mp.

An adversarial review of the diff raised 20 findings; **11 survived independent refutation and none
were refuted**. Four were corrupting the corpus, and the worst was already live:

| | Defect | Consequence |
|---|---|---|
| **D1** | `read_grid` was handed the whole 43-page MPRS document (that source ships prova and gabarito in one file) and its cell regex matched ordinary Portuguese — "As questões 1 a 14", "(C) Apenas 3 e 4", margin line numbers — with `setdefault` letting prose beat the real grid on the last page. | **Six wrong answer keys, and item 1 — officially ANULADA — stored with a fabricated key `A`**, so `vet`'s nullified check never fired and it was shortlist-eligible. A grid row is now a line of nothing but cells, matching is case-sensitive, `ANULADA` precedes `A`, and a non-contiguous recovery raises. |
| **D2** | The final question's last alternative ran to end-of-text. | It absorbed the entire 100-answer grid and rendered it **above** the spoiler block. The body is now bounded at the first grid row. |
| **D3** | `objetiva` had no furniture stripping at all. | Running heads and page numbers reached 80 stems and choices. Added, with a per-source `furniture:` list in the manifest. |
| **D4** | An item's rationale ran to the next item mark. | It swallowed the next block's fact pattern and comando — **17 of 58 items in PC/DF** — printed under "Gabarito comentado da banca" with a CEBRASPE attribution the banca never wrote. Now cut at the `<FimJust>` sentinel, or at the next comando's sentence start where the banca prints none. |

Two more, both real: the MPF prova extracted with words glued together (`"Assinaleaopçãocorreta:"`), fixed
with `x_tolerance=1.5` — verified to leave MPRS and both Cebraspe fixtures byte-identical; and
`parse/columns.py`, the milestone's declared shared prerequisite, had **zero behavioural coverage** —
forcing `columns=1` left all 282 tests passing. Replaced with synthetic two-column PDFs that kill both
mutants.

The corrupted rows do not self-heal, so the 468 M3 questions were deleted and re-harvested from the
fixed parsers, then re-classified and re-vetted. Verified afterwards: **0 wrong MPRS keys, 0
annulled-but-keyed, 0 furniture leaks, 0 rationale bleed.**

One process note worth carrying forward: my first attempt at D4 used an abbreviation-tolerant regex,
which matches *leftmost* and so started comandos inside the previous rationale (after "Min." in a
citation), dropping DP/DF from 22 usable items to 3. Sentence splitting replaced it. The green suite
did not catch that — re-measuring against the real fixtures did.

**Known residual:** the first block's comando in each caderno is truncated (2 items per document),
because cover boilerplate merges into it before the first item mark. Logged, not fixed.

---

## M3 Definition of Done

- The live database migrates cleanly: `stem_context` present, all 361 pre-existing questions intact, `usage_log` untouched.
- Four documents harvested; the corpus gains **60–100 processo penal questions** across **CEBRASPE, MPRS and MPF** bancas and the `delegado`, `defensoria`, `mp` carreiras.
- Certo/errado items appear in shortlists with their comando rendered above the stem, and the Cebraspe justificativa in the gabarito block.
- `bqpp stats` reports **zero** subtopics as coverage gaps — T3.3 shows as opening from doctrine.
- `curate`'s carreira tie-break demonstrably fires: a semester's shortlists draw on more than one carreira.
- Full suite green with no network and no LLM; ruff clean.

---

## Deferred (explicitly not M3)

| Item | Why |
|---|---|
| **Widening to other web sources** | The professor's call: a later milestone with its own recon. The manifest adapter is the seam it will plug into. |
| **The 84-certame justificativa tier** | Needs a cross-file item-number join that is demonstrably wrong at the source (C3), plus preliminar/definitivo handling. High volume, real error surface. |
| **Full 490-event Cebraspe discovery in the pipeline** | Used once, offline, to build the allow-list. Re-running it belongs with the widening milestone. |
| **Word-clustering column splitter** | Midpoint cropping handles all four seed documents. Do not build the general case speculatively. |
| **FGV multi-tipo provas (MPRJ/TJRJ)** | Four shuffled tipos with one combined grid: needs tipo binding and cross-tipo dedup, for casuistic material that will not serve T3.3. |
| **OCR** | Nothing in the seed set needs it. |
