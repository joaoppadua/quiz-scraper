# banco-questoes-pp — M2.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the milestone M2 deferred. Ingest the **OAB 1ª-fase objective provas** for 2019–2026 — Tipo 1 only — from index pages the corpus already caches, adding roughly 100–170 criminal and criminal-procedure `mcq4` items with official answer keys.

**Architecture:** No new machinery. M3's `parse/columns.py` and `parse/objetiva.py` already do the two-column reflow and A–E segmentation M2 deferred this milestone for; they need one selectable item anchor and one additional grid style. A new adapter `harvest/oab_1f.py` imports `oab_site.py`'s existing pure discovery helpers, selects the Tipo 1 caderno and the best available gabarito per exam, gates non-criminal items with a keyword filter, and writes the same `Question` rows `classify → vet → curate` already consume.

**Tech Stack:** unchanged. No new dependencies.

**Design doc:** `docs/superpowers/specs/2026-08-09-m2.5-provas-objetivas-1a-fase-design.md`

**Scope decided with the professor (2026-08-09):**
- **2019–2026 only** (19 exams). `eduagarcia/oab_exams` already covers 2010–2018, so this is the genuinely new material and cross-source overlap is side-stepped entirely rather than deduplicated.
- **Tipo 1 only.** Tipos 2–4 are shuffled reshuffles of the same items. Taking one dissolves the tipo-binding and cross-tipo dedup problem M3 deferred for MPRJ/TJRJ.
- **Preliminary gabaritos are ingested, not dropped** — but auto-flagged, because the *recursos* phase is exactly what overturns them.
- **A keyword pre-gate runs at ingest.** Each caderno is 80 questions across seven disciplines with no headings to bind on; without a gate ~1,350 irrelevant items enter both the corpus and the classify bill.
- **`[ranking]` is not touched.** New items are `mcq4`, already at the floor weight.

---

## Global Constraints

The M0/M1, M2 and M3 Global Constraints apply verbatim. The ones M2.5 actually exercises:

- **Only public, official sources.** M2.5 fetches from `examedeordem.oab.org.br` and `s.oab.org.br` — the Conselho Federal publishing its own exams. FGV remains the recorded *banca*; the OAB is only the host. No commercial aggregator.
- **Harvest etiquette** (spec §6): `[harvest] user_agent`, ≤ 1 request/second per host, URL-hash cache, manifest row per download. `harvest/http.py` remains **the only module that opens a socket**.
- **`parse/` stays pure**: text in, dataclasses out. No network, no database. The new grid reader and anchor selector go in `parse/objetiva.py`; nothing source-specific does.
- **`db.py` is the only module that emits SQL.** The new column goes through `Database.MIGRATIONS`.
- **Never construct a source URL by pattern.** Every PDF URL comes from an anchor parsed out of a fetched index page, never assembled.
- **Verbatim text, never paraphrased**; full attribution on every shortlist entry.
- **Idempotent**, deterministic ids, `--force` redoes work without destroying LLM results.
- **No exam PDFs committed.** `.gitignore` blocks `tests/fixtures/**/*.pdf`; fixtures are pdfplumber-extracted text.
- **One bad row must not abandon the run.** Every failure below is per-exam or per-item.

---

## Spec Amendments (verified against live sources, 2026-08-09)

Amendments are lettered **E** — M2 used `B`, M3 used `C` and its post-review used `D`.

**E1–E6 are measured and final.** They come from the complete offline census of the 47 cached index pages plus a download-and-parse of the 43º Exame Tipo 1 caderno and its *Gabaritos Definitivos*, run through the repository's own code. **E7–E9 are single-exam observations that Task 1 must extend to all 19 exams before Task 2 begins.**

| # | Prior claim | Reality | Consequence |
|---|---|---|---|
| **E1** | M2: 1ª fase "needs two-column coordinate reflow" | Retired by M3. `extract_columns(body, columns=2)` recovers **80/80 questions and 320/320 `(A)`–`(D)` markers** in reading order from the 43º caderno. Column-crop damage across 104,690 chars is 4 stray fragments and one bisected footer line. | Build nothing. Declare `columns: 2` in the source entry and reuse the extractor. |
| **E2** | M2: 1ª fase "needs a glyph-health gate" | Already built. `text_health` returns `ok` on both 43º PDFs and already gates `generic_pdf`. | Reuse it. It is where a mojibake exam exits. |
| **E3** | M2: "per-exam alternative markers (`A)` vs `(A)`)" | Does not apply to this document class. The OAB uses `(A)`–`(D)`; the existing `_CHOICE` regex matches all 320 unchanged. | No per-exam marker table. Delete the requirement. |
| **E4** | M2: "bold-numeral segmentation" | Real, and the only true parser gap — but not about boldness. The OAB anchors questions on a **bare numeral alone on its own line**. `Questão N` scores **0** hits; `N.`/`N)` scores **0**; so `segment_objetiva` returns **0 items**. Patching the anchor alone yields **80 items, all usable, contiguous 1–80**. | A selectable `item_style`, never a widened `_ITEM` — a bare-numeral pattern is far looser and would destabilise MPRS and MPF, which parse correctly today. |
| **E5** | §13: two grid conventions suffice | A third exists. The OAB gabarito is a **banded transpose** — a row of up to 20 item numbers, then a row of the same many letters directly beneath. Both existing styles raise `GridError`. A prototype banded reader recovers **80/80 entries, annulled at 1 and 74** from the `*` token, which is already in `ANNULMENT_TOKENS`. | Add `style="banded"`. |
| **E6** | (unstated) one gabarito file is one answer key | **One file carries all four tipos**, as `43º EXAME DE ORDEM - PROVA TIPO 1..4`. An unscoped banded read lets Tipo 4 overwrite Tipo 1 and ships four-fifths wrong keys — the worst outcome this corpus can have. | `style="banded"` **requires** a `section`, and raises `GridError` when it is absent or unmatched. Never optional. |
| **E7** | (unstated) M2.5 needs source reconnaissance | It does not. The OAB index pages are **already cached** in `data/raw/oab` from M2; `select_penal_padroes` merely filtered the 1ª-fase artifacts out. Census of the 47 cached pages: **29 exams publish a `Caderno de Prova - Tipo N`; 19 are 2019 or later; all 19 also publish a 1ª-fase gabarito.** | M2.5 is decoupled from the unbuilt "Source widening" milestone. Only 38 new PDFs are fetched; discovery costs no network. |
| **E8** | (unstated) a 1ª-fase gabarito is definitive | **10 of the 19 (2023–2026) publish a *definitivo*; 9 (2019–2022) publish only a *preliminar*.** A preliminary key is precisely what the recursos phase overturns. | Prefer definitivo, fall back to preliminar, and mark the fallback so vetting can flag it. Never silently treat a preliminar as final. |
| **E9** | (unstated) a caderno is one discipline | It is **80 questions across seven disciplines**, and carries **no discipline headings** — every `DIREITO X` string in the 43º sits inside question prose. On that exam, ~5 items are clearly processo penal and ~8 more are penal material. | A keyword gate at ingest, over stem **and** alternatives. Without it, 19 × 80 = 1,520 items reach `classify` to surface ~150. |

### The 19 in-scope exams (from the cached index pages)

| Year | Exam ids | Gabarito |
|---|---|---|
| 2019 | 11561, 11562, 11563 | preliminar only |
| 2020 | 11694 | preliminar only |
| 2021 | 12443, 12895 | preliminar only |
| 2022 | 13096, 13415, 13817 | preliminar only |
| 2023 | 14340, 14709, 15122 | **definitivo** |
| 2024 | 15812, 16135, 16483 | **definitivo** |
| 2025 | 16773, 17000, 17431 | **definitivo** |
| 2026 | 17734 | **definitivo** |

2020 has a single exam; the other years have three.

### Expected yield

Extrapolating the 43º's ~5 clearly-procedural and ~8 penal-material items across 19 exams gives **roughly 100–170 items** surviving the keyword gate, before `classify` and `vet` cut further. Treat this as an estimate until Task 1 measures it. Every one is `mcq4` at the floor ranking weight of 1.0 — the payoff is depth in the thinnest subtopics, not headline count.

---

## File Structure

```
config/
  sources.yaml            # T6  + oab-1f-penal entry, incl. keep_keywords
src/bqpp/
  models.py               # T4  Question.answer_key_provisional
  db.py                   # T4  guarded ADD COLUMN migration
  vet.py                  # T4  gabarito_preliminar reason + escalation set
  cli.py                  # T6  register oab_1f in _adapters() and _OFFLINE_CAPABLE
  parse/
    objetiva.py           # T2  selectable item anchor
                          # T3  banded grid reader, section-scoped
  harvest/
    oab_1f.py             # T5  selection + keyword gate
                          # T6  ingestion + harvest_source
tests/
  fixtures/oab_1f/        # extracted TEXT only, never PDFs
    exame_43_tipo1.txt      trimmed caderno: a criminal item, a non-criminal
                            item, an annulled item, and the questionário tail
    exame_43_gabarito.txt   ALL FOUR tipo blocks, so scoping is really tested
  test_objetiva.py        # T2, T3  extended
  test_oab_1f.py          # T5, T6  new
  test_migration.py       # T4  extended
  test_vet.py             # T4  extended
```

`harvest/oab_site.py` is **not modified.** It keeps `extract_text` for padrões: `extract_columns(columns=1)` is not equivalent — it applies `x_tolerance=1.5` and strips Unicode `Cf` characters — so switching it would silently re-cut all 139 shipped M2 questions for no gain. `curate.py` is **not modified** either: `curate.py:112-117` already renders both the reason codes and each reason's detail under the ⚠ **Sinalizada** banner.

---

## Task 1: Recon sweep over all 19 exams

Everything measured so far is **n = 1** (the 43º Exame, 2025). The M2 plan records that *"the 42º Tipo 1 is confirmed mojibake"* — an adjacent exam that already fails. Older exams may differ in anchor style, column geometry and alternative count. This task replaces extrapolation with measurement **before any parser is finalised**.

**Files:** Create `scripts/recon_1f.py` (a one-shot survey, not pipeline code). Modify this plan document.

**Interfaces:** Produces no importable interface. Its output is the amended E7–E9 rows and a populated `keep_keywords` list, both consumed by Tasks 2, 3 and 6.

- [ ] **Step 1: Write the sweep script.**

It must go through `harvest/http.py`'s `Fetcher` so etiquette is enforced in the one place it lives — 1.5 s spacing, configured user agent, on-disk cache. Read the index pages from cache (`offline=True` succeeds; they are already there), and fetch only the 38 PDFs.

For each of the 19 exams report one row: `exam_id`, `year`, whether a Tipo 1 caderno and a gabarito were found, `definitivo` or `preliminar`, `text_health`, item count under the `bare` anchor, whether numbers are contiguous from 1, grid entry count, item↔grid match rate, and count of annulled items.

Also report two cross-source measurements Task 2 needs in order to assert a concrete value rather than a vague one:

- what the **MPRS and MPF** fixtures segment into under the `bare` anchor (item count, and whether the numbers are contiguous). Task 2 pins whatever this measures — that is why the measurement happens here.
- what the **OAB** caderno segments into under the `punctuated` anchor. Expected `0`; record the actual.

- [ ] **Step 2: Run it and read every row.**

Run: `uv run python scripts/recon_1f.py`

- [ ] **Step 3: Amend this plan from the results.**

Rewrite E7–E9 with measured values. Add a new lettered row for every convention that differs from the 43º — a different anchor, five alternatives, an off-centre gutter, a differently-worded tipo heading. If an exam fails, record it **by name with its reason** and exclude it; the precedent is M2 shipping 35 of 45 exams rather than building speculative parsers for the other 10. Update the expected-yield paragraph with the real number.

- [ ] **Step 4: Populate and tune `keep_keywords`.**

Hand-check one exam: list every item a domain reader would call criminal or criminal-procedure, then run the seed list from the design doc §5.5 against it. **Report recall explicitly** — how many hand-picked items the gate keeps. Extend the list until recall is complete on that exam, and prefer a false positive over a miss; `classify` is the real discipline filter and can discard, but a dropped item never gets a second chance.

- [ ] **Step 5: Extract the two fixtures Tasks 2 and 3 depend on.**

No other task can create these — Task 1 is the only one holding the PDFs. Write **extracted text, never PDFs** (`.gitignore` blocks `tests/fixtures/**/*.pdf`).

`tests/fixtures/oab_1f/exame_43_tipo1.txt` — from `extract_columns(caderno, columns=2)`, trimmed to keep: at least one clearly criminal-procedure item, one clearly non-criminal item (a tributário or civil one, for the gate's negative case), **the annulled item 1**, and enough of the trailing *questionário de percepção* that its `1.`–`10.` restart is present. Renumber nothing — the fixture must keep the real numbering so the longest-run arbiter is genuinely exercised.

`tests/fixtures/oab_1f/exame_43_gabarito.txt` — from `extract_columns(gabarito, columns=1)`, retaining **all four tipo blocks**. Trimming to one block would let a broken implementation pass, which is exactly the E6 failure mode.

Record in the report the item numbers kept and which is which, so Tasks 2, 3 and 6 can assert exact counts.

- [ ] **Step 6: Commit.**

```bash
git add scripts/recon_1f.py tests/fixtures/oab_1f/ docs/superpowers/plans/2026-08-09-banco-questoes-pp-m2.5.md
git commit -m "docs: M2.5 recon sweep — 19 OAB 1a-fase exams measured"
```

**Requirements:** No `src/bqpp/` file changes in this task. If the sweep shows fewer than 10 exams parse cleanly, **stop and re-scope with the professor** rather than proceeding — the milestone's value assumption would no longer hold.

---

## Task 2: Selectable item anchor

**Files:** Modify `src/bqpp/parse/objetiva.py`. Test: `tests/test_objetiva.py`, fixture `tests/fixtures/oab_1f/exame_43_tipo1.txt`.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `segment_objetiva(text, *, furniture: list[str] | None = None, item_style: str = "punctuated") -> list[ObjetivaItem]` and `_ITEM_STYLES: dict[str, re.Pattern]` with keys `"punctuated"` and `"bare"`. Tasks 5 and 6 pass `item_style="bare"`.

- [ ] **Step 1: Write the failing tests.**
  - The OAB fixture under `item_style="bare"` yields items numbered **contiguously from 1**, each with **exactly 4** choices labelled `A`–`D`, and every item `.usable`.
  - The trailing *questionário de percepção* is **excluded**: the fixture carries the questionnaire's own `1.`–`10.` restart, and the longest-sequential-run arbiter must keep the real prova run (E4).
  - **Regression, the important one:** the existing MPRS and MPF fixtures parsed with the default `item_style` produce output **identical** to today — same item count, same numbers, same stems, same choices. Assert on the parsed structures, not on a count alone.
  - The OAB fixture under the **default** style, and the MPRS fixture under `"bare"`, both assert the **exact values Task 1 measured** and recorded in its report — item counts and contiguity, not "some" or "few". Do not guess these; read them from the Task 1 report. Pinning the cross-style behaviour is what stops a future widening of either pattern from passing silently.
  - An unknown `item_style` raises rather than falling back to a default.

- [ ] **Step 2: Run the tests and watch them fail.**

Run: `uv run pytest tests/test_objetiva.py -q`
Expected: FAIL — `segment_objetiva() got an unexpected keyword argument 'item_style'`.

- [ ] **Step 3: Implement.**

```python
_ITEM_STYLES: dict[str, re.Pattern] = {
    # "12. Enunciado" / "12) Enunciado" — MPRS, MPF, and every M3 source.
    "punctuated": re.compile(r"^[ \t]*(\d{1,3})[ \t]*[\.\)][ \t]+(?=\S)", re.M),
    # A bare numeral alone on its own line — how FGV numbers the OAB 1ª fase.
    # Deliberately a separate style: this pattern is far looser than the
    # punctuated one, and widening _ITEM to accept both would destabilise the
    # sources that parse correctly today (E4).
    "bare": re.compile(r"^[ \t]*(\d{1,3})[ \t]*$", re.M),
}
```

`_question_marks` takes the compiled pattern as an argument instead of closing over the module constant. Nothing else changes — the longest-sequential-run arbiter and `_RESYNC_WINDOW` already resolve the OAB caderno to exactly 1–80.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `uv run pytest tests/test_objetiva.py -q && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit.**

```bash
git add src/bqpp/parse/objetiva.py tests/test_objetiva.py tests/fixtures/oab_1f/
git commit -m "feat: selectable item anchor so the OAB's bare-numeral numbering parses"
```

---

## Task 3: The banded grid reader

**Files:** Modify `src/bqpp/parse/objetiva.py`. Test: `tests/test_objetiva.py`, fixture `tests/fixtures/oab_1f/exame_43_gabarito.txt`.

**Interfaces:**
- Consumes: `GridError`, `ANNULMENT_TOKENS`, `_verdict` from Task 2's module.
- Produces: `read_grid(text: str, *, style: str, section: str | None = None) -> dict[int, str | None]`, now accepting `style="banded"`. Task 6 calls it with `section` formatted from the source entry.

**The fixture must retain all four tipo blocks.** A single-block fixture would pass a broken implementation, which is precisely the E6 failure mode.

- [ ] **Step 1: Write the failing tests.**
  - `read_grid(gabarito, style="banded", section=r"PROVA\s+TIPO\s+1\b")` returns **80 entries**, with `None` at **1 and 74** from the `*` token (E5).
  - **The E6 regression:** the same call must **not** return Tipo 2's letters. Assert a specific item where Tipo 1 and Tipo 2 disagree — the fixture's Tipo 1 item 2 is `C` and Tipo 2 item 2 is `A`.
  - `style="banded"` with `section=None` raises `GridError`.
  - `style="banded"` with a section matching nothing raises `GridError`.
  - A block whose head row and answer row have different lengths raises `GridError`, naming both counts.
  - Recovered numbers that are not contiguous raise `GridError`, reusing the existing check.
  - **Regression:** `paired_rows` and `interleaved` on the Cebraspe, MPRS and MPF fixtures are unchanged, and both still raise when handed the OAB gabarito.

- [ ] **Step 2: Run the tests and watch them fail.**

Run: `uv run pytest tests/test_objetiva.py -q`
Expected: FAIL — `GridError: unknown grid style 'banded'`.

- [ ] **Step 3: Implement.**

Add `"banded"` to `read_grid`'s dispatch and a `_read_banded(text, section)`:

- Raise `GridError` immediately if `section` is falsy — the requirement is structural, not a convenience default.
- Locate `section` as a regex; raise `GridError` if it does not match. Bound the block at the next match of the **same** pattern shape, so a four-tipo file yields only the requested block.
- Within the block, pair each line of ≥ 5 bare integers with the line beneath it. `zip(..., strict=True)`, and raise `GridError` on a length mismatch.
- Map tokens through the existing `_verdict`, so `*` becomes `None` with no new constant.
- Reuse `_read_interleaved`'s contiguity check on the assembled grid.
- Raise `GridError` if the block yields nothing.

Refusing beats defaulting throughout — the module's existing doctrine, and here a wrong key reaches a student as fact.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `uv run pytest tests/test_objetiva.py -q && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit.**

```bash
git add src/bqpp/parse/objetiva.py tests/test_objetiva.py tests/fixtures/oab_1f/
git commit -m "feat: section-scoped banded grid reader for the OAB four-tipo gabarito"
```

---

## Task 4: `answer_key_provisional` — model, migration, vetting

**Files:** Modify `src/bqpp/models.py`, `src/bqpp/db.py`, `src/bqpp/vet.py`, `config/sources.yaml`. Test: `tests/test_migration.py`, `tests/test_vet.py`, extend `tests/test_db.py`.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Question.answer_key_provisional: bool = False`, persisted; vet reason code `"gabarito_preliminar"`. Task 6 sets the field at ingest.

This is the project's **second** migration; Task 1 of the M3 plan is the model to follow.

- [ ] **Step 1: Write the failing tests.**
  - Opening a database created at the **M3 schema** and calling `init_schema()` adds `answer_key_provisional`, preserves every existing row, and leaves `usage_log` untouched.
  - `init_schema()` twice is a no-op — no duplicate-column error.
  - Existing rows default to `0` / `False`, and round-trip through `upsert_question` / `iter_questions`.
  - A question with `answer_key_provisional=True` collects a `gabarito_preliminar` reason from `apply_rules`.
  - `_merge_verdict` escalates an LLM verdict of `ok` to `flagged` when that reason is present — mirroring the existing `no_gabarito` behaviour.
  - `rejected` stays `rejected`; a `nullified` item is still terminally `rejected` and never reaches `flagged`.

- [ ] **Step 2: Run the tests and watch them fail.**

Run: `uv run pytest tests/test_migration.py tests/test_vet.py -q`
Expected: FAIL — no such attribute / no such column.

- [ ] **Step 3: Implement.**

```python
# db.py
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("questions", "stem_context", "TEXT"),
    ("questions", "answer_key_provisional", "INTEGER DEFAULT 0"),
)
```

```python
# vet.py — inside apply_rules, alongside the existing no_gabarito rule
if question.answer_key_provisional:
    reasons.append(VetReason(
        code="gabarito_preliminar",
        detail="gabarito preliminar, sujeito a alteração após recursos",
    ))
```

Replace `_merge_verdict`'s hardcoded `no_gabarito` check with a set, so adding the third code later is a one-line edit:

```python
_ESCALATING_CODES = frozenset({"no_gabarito", "gabarito_preliminar"})
...
if verdict == "ok" and any(r.code in _ESCALATING_CODES for r in rule_reasons):
    verdict = "flagged"
```

Add the field to `models.Question` next to `answer_key`, and to the `upsert_question` column list and the row-to-model mapping in `db.py`.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS across the whole suite, ruff clean.

- [ ] **Step 5: Migrate the live database and verify.**

Run: `uv run bqpp stats`
Expected: still **817 questions**, every subtopic count unchanged. The migration is an `ADD COLUMN` and must not perturb a single row.

- [ ] **Step 6: Commit.**

```bash
git add src/bqpp/models.py src/bqpp/db.py src/bqpp/vet.py tests/
git commit -m "feat: flag provisional answer keys so preliminary gabaritos surface as flagged"
```

**Requirements:** `config/sources.yaml`'s `cebraspe-cadernos` entry gains a note that `PC_DF_26_DELEGADO` is published as *"PROVA OBJETIVA (P1) E GABARITO PRELIMINAR COM JUSTIFICATIVAS"* and therefore carries the same exposure. **Setting the flag for that source is deliberately out of scope here** — it is a Cebraspe-adapter change, and folding it in would let an M2.5 task alter M3 data. Record it in Deferred and do it as its own commit if the professor wants it.

---

## Task 5: Artifact selection and the keyword gate

**Files:** Create `src/bqpp/harvest/oab_1f.py`, `tests/test_oab_1f.py`.

**Interfaces:**
- Consumes: `IndexEntry`, `parse_exam_index` from `harvest/oab_site.py`; `ObjetivaItem` from `parse/objetiva.py`.
- Produces:

```python
@dataclass(frozen=True)
class Artifacts:
    caderno: IndexEntry
    gabarito: IndexEntry
    definitivo: bool

def select_1f_artifacts(entries: list[IndexEntry], *, tipo: int = 1) -> Artifacts | None
def is_criminal(item: ObjetivaItem, keywords: list[str], min_hits: int) -> bool
```

Task 6 consumes both.

This task is pure selection logic over already-parsed index entries — no fetching, no database. That is what makes it independently testable.

- [ ] **Step 1: Write the failing tests.**

Drive them from a small list of real `IndexEntry` labels taken from the cached index pages, including the distractors:

  - Given labels for `Caderno de Prova - Tipo 1..4`, a `Gabaritos Definitivos - Prova Objetiva (1ª fase)` and a `Gabaritos Preliminares da Prova Objetiva (1ª fase)`, the selector returns the **Tipo 1** caderno, the **definitivo** gabarito, and `definitivo=True`.
  - With no definitivo present, it returns the preliminar and `definitivo=False`.
  - With several preliminares (the cached pages carry `- atualizado` variants), it returns the **newest by date**.
  - `tipo=3` selects the Tipo 3 caderno and not Tipo 1.
  - Returns `None` when no caderno is present, and `None` when no gabarito is present — never a half-populated `Artifacts`.
  - **Does not match the 2ª-fase distractors** that live on the same page: `Caderno de provas (Direito Penal)`, `Caderno de provas (Direito Civil)`, or a `Padrão de respostas (Direito Penal)`. This is the mirror of M2's Task 2 assertion and matters because both phases share one index.
  - Does not match 1ª-fase editais: `Resultado Definitivo (após recursos) - Prova Objetiva (1ª fase)`, `Edital - Locais e Horário de Realização da Prova Objetiva (1ª fase)`.
  - `is_criminal` keeps an item whose **stem** carries the signal, keeps one whose signal is only in its **alternatives**, and drops a tributário item. Searching the stem alone must fail the second case.
  - `is_criminal` respects `min_hits`: a single incidental `réu` does not qualify at `min_hits=2`.

- [ ] **Step 2: Run the tests and watch them fail.**

Run: `uv run pytest tests/test_oab_1f.py -q`
Expected: FAIL — `ModuleNotFoundError: bqpp.harvest.oab_1f`.

- [ ] **Step 3: Implement.**

Match labels on accent- and case-normalised text. Exclude any label containing `edital`, `resultado`, `local`, `horário`, `isenção`, `inscrição` or `recurso` **before** testing for the artifact patterns, so an edital about the prova objetiva cannot be mistaken for the prova.

`is_criminal` counts distinct keyword hits over `item.stem` **and every** `choice["text"]`, never the stem alone (E9) — the same rule already applied to comando and rationale elsewhere in the codebase.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `uv run pytest tests/test_oab_1f.py -q && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit.**

```bash
git add src/bqpp/harvest/oab_1f.py tests/test_oab_1f.py
git commit -m "feat: 1a-fase artifact selection and the criminal-material gate"
```

---

## Task 6: Ingestion and wiring

**Files:** Modify `src/bqpp/harvest/oab_1f.py`, `src/bqpp/cli.py`, `config/sources.yaml`. Test: `tests/test_oab_1f.py`.

**Interfaces:**
- Consumes: `select_1f_artifacts`, `is_criminal` (Task 5); `segment_objetiva(..., item_style=)` (Task 2); `read_grid(..., style="banded", section=)` (Task 3); `Question.answer_key_provisional` (Task 4).
- Produces:

```python
def ingest_caderno(caderno_text: str, gabarito_text: str, *, exam, artifacts: Artifacts,
                   source_id: str, db: Database, params: dict,
                   force: bool = False, seen: dict[str, str] | None = None) -> int

def harvest_source(entry, db: Database, settings, *, dry_run: bool = False,
                   force: bool = False, offline: bool = False) -> int
```

`generic_pdf.ingest_prova` is the shape to follow — same signature style, same dedup-through-`content_hash`, same per-item skip logging.

- [ ] **Step 1: Write the failing tests.**
  - `ingest_caderno` over the two fixtures writes one `source_documents` row with `kind="prova"`, `banca="FGV"`, `carreira="oab"`, a `certame` reading `OAB 43º Exame (1ª fase)`, and a populated `exam_year`.
  - **`exam_year` is asserted explicitly.** It is load-bearing: without it the law watchlist can never fire for this source.
  - Only items passing the gate are written; the count matches the fixture's criminal items exactly.
  - Every written question has `format="mcq4"`, four `choices`, and an `answer_key` matching the Tipo 1 grid.
  - `answer_key_provisional` is `False` when `Artifacts.definitivo` is `True`, and `True` when it is `False`.
  - The annulled items carry `nullified=True` and a `None` answer key.
  - Re-running writes **zero** new rows; with `force=True` it rewrites without duplicating.
  - An exam whose caderno text is unhealthy writes **nothing** and does not abort the run.
  - A `GridError` writes **nothing** for that exam and does not abort the run.
  - `harvest_source` skips exams before `min_exam_year`.
  - `--dry-run` writes nothing and opens no socket.

- [ ] **Step 2: Run the tests and watch them fail.**

Run: `uv run pytest tests/test_oab_1f.py -q`
Expected: FAIL — `ingest_caderno` not defined.

- [ ] **Step 3: Implement `ingest_caderno` and `harvest_source`.**

`harvest_source` mirrors `oab_site.harvest_source`: enumerate exam ids from the seed page, fetch each index page (all cached), select artifacts, skip when `exam_year < min_exam_year`, fetch the two PDFs, then:

```python
# `artifacts.caderno` / `artifacts.gabarito` are IndexEntry (href + label);
# `fetcher.get` returns the fetched document, whose bytes are `.body`.
caderno_pdf  = fetcher.get(artifacts.caderno.href)
gabarito_pdf = fetcher.get(artifacts.gabarito.href)

caderno_text  = extract_columns(caderno_pdf.body,  columns=int(p.get("columns", 2)))
gabarito_text = extract_columns(gabarito_pdf.body, columns=1)
```

The gabarito is read at `columns=1` deliberately: it is a full-width table of tipo blocks, and cropping it at the midpoint would split every band.

Gate on `text_health(caderno_text) != "ok"`, then segment with `item_style=p["item_style"]` and read the grid with `section=p["grid_section"].format(tipo=p["tipo"])`.

Error handling, every case per-exam or per-item, never fatal:

| Condition | Action |
|---|---|
| Missing caderno or gabarito | INFO, skip exam |
| `text_health != "ok"` | WARNING, skip exam |
| `GridError` | ERROR, skip exam — never default a key |
| 0 items segmented | WARNING, skip exam |
| Item number absent from grid | INFO, skip item |
| `FetchError` | ERROR, continue to next exam |
| Keyword gate drops | INFO count per exam |

That last row is not optional: a silent gate reads as full coverage when it is not.

- [ ] **Step 4: Wire the adapter in.**

```python
# cli.py
_OFFLINE_CAPABLE = {"oab_site", "cebraspe", "generic_pdf", "oab_1f"}

def _adapters() -> dict:
    from bqpp.harvest import cebraspe, generic_pdf, hf_datasets, oab_1f, oab_site
    return {
        ...,
        "oab_1f": oab_1f.harvest_source,
    }
```

Add the `oab-1f-penal` entry to `config/sources.yaml` exactly as specified in design doc §5.5, with `keep_keywords` as tuned in Task 1.

- [ ] **Step 5: Run the full suite.**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, ruff clean. The M3 sources must be untouched.

- [ ] **Step 6: Commit.**

```bash
git add src/bqpp/harvest/oab_1f.py src/bqpp/cli.py config/sources.yaml tests/test_oab_1f.py
git commit -m "feat: harvest OAB 1a-fase objective provas, 2019-2026"
```

---

## Task 7: Live run and verification

**Files:** Modify this plan document (a Results section, as M2 and M3 both carry).

- [ ] **Step 1: Dry-run first.**

Run: `uv run bqpp harvest --dry-run -v`
Expected: 19 exams listed with their two URLs each, nothing written.

- [ ] **Step 2: Harvest for real.**

Run: `uv run bqpp harvest -v`
Expected: ~38 PDFs at 1.5 s spacing, roughly one minute. Read the log: every skipped exam must be named with a reason, and every exam must report its keyword-gate drop count.

- [ ] **Step 3: Verify idempotence.**

Run: `uv run bqpp harvest -v`
Expected: **zero** new questions written.

- [ ] **Step 4: Classify and vet.**

Run: `uv run bqpp classify -v && uv run bqpp vet -v`

- [ ] **Step 5: Curate and inspect.**

Run: `uv run bqpp curate --semester 2026.2 && uv run bqpp stats`

Then read at least three shortlists by hand and confirm: alternatives render, attribution carries banca/certame/year/URL, no stem is spliced mid-sentence by the column crop, and a preliminary-key item shows ⚠ **Sinalizada** with `gabarito_preliminar` and its detail line.

- [ ] **Step 6: Spot-check answer keys against the source.**

Pick three ingested questions from three different exams and verify each `answer_key` against the published gabarito by eye. This is the E6 guard — a Tipo-2 key leaking into a Tipo-1 question is invisible to every automated check in this plan.

- [ ] **Step 7: Record results and commit.**

```bash
git add docs/superpowers/plans/2026-08-09-banco-questoes-pp-m2.5.md
git commit -m "docs: record the M2.5 real-run results against the definition of done"
```

---

## M2.5 Definition of Done

- All 19 in-scope exams attempted; **every exclusion logged by name with its reason**.
- The corpus gains OAB 1ª-fase `mcq4` questions with official answer keys, and `bqpp stats` shows the increase concentrated in criminal-procedure subtopics.
- `bqpp harvest` is idempotent for this source: a second run writes zero rows.
- **No shortlist entry carries a Tipo 2–4 answer key** — verified by hand on three questions from three exams.
- Every ingested question has an `exam_year`, so the law watchlist can fire.
- Preliminary-key questions surface as `flagged` with `gabarito_preliminar` and its detail visible in the shortlist.
- The live database migrates cleanly: all 817 pre-existing questions intact, `usage_log` untouched.
- MPRS, MPF, Cebraspe and OAB 2ª-fase output is **unchanged**, verified by fixture equality rather than inspection.
- Full suite green with no network and no LLM; `ruff check .` clean.

---

## Deferred (explicitly not M2.5)

| Item | Why |
|---|---|
| **Tipos 2–4 and the tabela de correspondência** | The gabarito ships a Tipo 1 ↔ 2/3/4 correspondence table, so this is buildable — but the items are the same questions reshuffled. Nothing is gained and cross-tipo dedup is a real error surface. |
| **OAB 1ª fase 2010–2018** | `eduagarcia/oab_exams` already covers it. Revisit only if the PDF text proves materially better than the dataset rows. |
| **Backfilling `answer_key_provisional` on `PC_DF_26_DELEGADO`** | The same exposure exists in M3 data today. Deliberately not folded into an M2.5 task, so this milestone does not alter another's rows. One commit whenever the professor wants it. |
| **Per-exam alternative-marker tables** | E3 measured `(A)`–`(D)` throughout. Build one only if Task 1 finds an exam that needs it. |
| **A word-clustering column splitter** | M3 forbade building it speculatively; midpoint cropping is measured sufficient here too. |
| **OCR** | Nothing in scope needs it. A mojibake exam is excluded, not rescued. |
