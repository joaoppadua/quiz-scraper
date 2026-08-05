# SPEC — `banco-questoes-pp`: Exam-Question Corpus Builder & Semester Curation Pipeline

**Status:** draft v1.0 (2026-08-04) · **Author:** João Pedro Pádua (spec drafted with Claude)
**Target implementer:** an LLM coding agent working in a fresh repository.
**Language:** Python ≥ 3.11. Code, identifiers and docs in English; domain terms and all question content in pt-BR.

---

## 1. Purpose and background

From semester 2026.2 on, the discipline *Processo Penal 2* (Faculdade de Direito, UFF) opens each **subtopic** with one question taken from a Brazilian public exam for a *carreira jurídica* (OAB, Ministério Público, Magistratura, Delegado/carreiras policiais, Defensoria, etc.). Students first work on the question individually; in the following class the professor resolves it together with them. The goal is active use of legal concepts rather than passive lecturing.

This project supports that method with two workflows:

1. **Corpus builder** (run occasionally): harvest publicly available exam questions from official sources, parse them into structured records, classify them against the course's subtopic taxonomy, and vet them for validity (annulled questions, outdated law).
2. **Semester curation run** (run once per semester, or per topic): from the local corpus, produce a **markdown shortlist** of 3–5 vetted candidate questions per subtopic, with source links and answer keys, for the professor to review and pick from.

Design constraints agreed upfront:

- **Provider-agnostic LLM layer.** No hard dependency on a single LLM vendor; a thin interface with pluggable backends.
- **Only public, official (or officially mirrored) sources.** No scraping of paywalled commercial aggregators (QConcursos, TEC Concursos, etc.) — their terms bar it and it isn't needed.
- **Human-in-the-loop.** The pipeline never decides which question is used in class; it shortlists, the professor chooses.
- **Small scale, high reliability.** ~20 subtopics × 1–2 questions/semester ≈ 30–40 questions actually used per semester. A corpus of a few hundred well-vetted *processo penal* questions is a success. Prefer correctness and provenance over volume.

## 2. Scope

**In scope (v1):**
- Discipline filter: *Direito Processual Penal* (the schema must be discipline-generic, but only this discipline is populated in v1).
- Question formats: multiple choice (4 or 5 alternatives), Cebraspe-style *certo/errado*, discursive/essay questions (*dissertativas*, including OAB 2ª fase *questões* and, where feasible, their *gabaritos comentados/justificados*).
- Sources: see §6. Priority order: Hugging Face structured datasets (bootstrap) → FGV/OAB official PDFs → Cebraspe official PDFs → state MP/TJ transparency pages.
- Storage: local SQLite + flat JSON export; PDFs cached on disk.
- CLI-driven; no web UI.

**Out of scope (v1):** *peças profissionais* (2ª fase practical drafting tasks — flag them in parsing but don't model their internal structure); OCR of scanned PDFs (skip non-born-digital files, log them); other disciplines; any student-facing delivery (handouts are produced manually by the professor from the shortlist); scheduling/automation daemons.

## 3. Glossary

| Term | Meaning |
|---|---|
| *banca* | Examining board that authors the exam (FGV, Cebraspe, FCC, Vunesp, MPF/MP state boards…). |
| *carreira* | The career the exam selects for (OAB/advocacia, magistratura, MP/promotor, delegado, defensor…). |
| *certame / concurso* | A specific exam event (e.g., "XXXVIII Exame de Ordem", "TJ-SP Juiz 2023"). |
| *gabarito* | Answer key. *Gabarito justificado/comentado*: answer key with the banca's reasoning (FGV publishes these for OAB). |
| *questão anulada* | Question officially nullified by the banca after appeals. Must never be shortlisted (but keep in corpus, flagged). |
| *desatualizada* | Question whose correct answer changed because the law or controlling precedent changed after the exam date. |
| *certo/errado* | Cebraspe's true/false item format. |
| *1ª/2ª fase* | OAB exam phases: phase 1 is multiple choice; phase 2 is discipline-specific, discursive + practical. |

## 4. Architecture overview

```
                    ┌─────────────────────────  corpus builder  ─────────────────────────┐
 sources.yaml ──►  harvest  ──►  raw store  ──►  parse/segment  ──►  classify  ──►  vet  ──►  SQLite
 (registry)        (download      (PDFs +        (structured        (LLM: subtopic   (LLM+rules:
                    PDFs/datasets) manifest)      Question records)   + metadata)      valid/flagged)
                                                                                          │
                    ┌───────────────────────  semester curation  ────────────────────────┘
                    curate ──► shortlists/2026-2/T1.1-principios.md … (one file per subtopic)
                                └── usage log updated when the professor marks a question as used
```

Each stage is idempotent and re-runnable; stages communicate only through the database and the file cache, so any stage can be re-run in isolation.

## 5. Repository layout

```
banco-questoes-pp/
├── SPEC.md                     # this file
├── README.md
├── pyproject.toml              # uv/pip installable; deps pinned
├── config/
│   ├── sources.yaml            # source registry (§6)
│   ├── taxonomy.yaml           # canonical subtopic taxonomy (§7)
│   ├── law_watchlist.yaml      # law/jurisprudence change watchlist for vetting (§10)
│   └── settings.toml           # paths, LLM backend selection, model names, rate limits
├── src/bqpp/
│   ├── models.py               # dataclasses / pydantic models (§8)
│   ├── db.py                   # SQLite access layer (§8)
│   ├── llm/
│   │   ├── base.py             # LLMClient protocol (§9)
│   │   ├── anthropic_client.py # one concrete backend (implement first)
│   │   ├── openai_client.py    # optional second backend
│   │   └── json_call.py        # schema-constrained call helper with retries
│   ├── harvest/
│   │   ├── registry.py         # loads sources.yaml
│   │   ├── hf_datasets.py      # Hugging Face bootstrap adapter
│   │   ├── fgv_oab.py          # oab.fgv.br adapter
│   │   ├── cebraspe.py         # cdn.cebraspe.org.br adapter
│   │   └── generic_pdf.py      # plain "list of PDF URLs" adapter (MP/TJ pages)
│   ├── parse/
│   │   ├── segmenter.py        # PDF → question blocks (per-banca strategies)
│   │   ├── gabarito.py         # answer-key parsing and alignment
│   │   └── formats.py          # MCQ / certo-errado / dissertativa detection
│   ├── classify.py             # LLM classification stage
│   ├── vet.py                  # rule + LLM vetting stage
│   ├── curate.py               # shortlist generation + usage log
│   └── cli.py                  # typer CLI (§12)
├── prompts/
│   ├── classify.md             # prompt template (§9.3)
│   └── vet.md                  # prompt template (§10.2)
├── data/                       # gitignored
│   ├── raw/                    # downloaded PDFs, one dir per source
│   ├── corpus.sqlite
│   └── export/                 # JSON exports
├── shortlists/                 # committed to the repo (curation outputs)
└── tests/
    ├── fixtures/               # small sample PDFs + expected parse JSON
    └── test_*.py
```

## 6. Source registry (`config/sources.yaml`)

Each source entry declares an adapter and adapter-specific parameters. v1 ships with these entries (implementer: verify URLs at build time; they were checked 2026-08):

```yaml
sources:
  - id: hf-oab-exams
    adapter: hf_datasets
    dataset: eduagarcia/oab_exams          # 2,210 OAB 1ª fase questions, 2010–2018,
    subject_filter: ["Criminal Procedure"]  # already labeled by subject; parquet
    notes: bootstrap source — no PDF parsing needed

  - id: hf-oab-bench
    adapter: hf_datasets
    dataset: maritaca-ai/oab-bench          # 105 OAB 2ª fase discursive questions
    subject_filter: ["Criminal"]            # includes examiner guidelines
    notes: discursive bootstrap; check license/README before redistribution

  - id: fgv-oab
    adapter: fgv_oab
    base_url: https://oab.fgv.br
    what: [prova_1a_fase, prova_2a_fase_penal, gabarito, gabarito_justificado]
    notes: index pages list per-exam PDF links; 2ª fase Penal + gabarito justificado
           are the highest-value items for the teaching method

  - id: cebraspe
    adapter: cebraspe
    base_url: https://cdn.cebraspe.org.br/concursos/
    concursos: []        # explicit allow-list, filled manually per harvest run,
                         # e.g. penal-heavy certames (delegado PF/PC, magistratura, MP)
    notes: open CDN; provas and gabaritos as PDFs; certo/errado format

  - id: mp-tj-pages
    adapter: generic_pdf
    urls: []             # manually curated list of direct PDF links from
                         # MP/TJ transparency pages (MPRJ, MPSC, MPRS, TJs)
```

Harvest etiquette (hard requirements): descriptive User-Agent identifying the project; ≤ 1 request/second per host; on-disk cache keyed by URL hash — never re-download an unchanged file; every downloaded file recorded in a manifest (URL, SHA-256, fetch date, HTTP headers) for provenance.

## 7. Canonical taxonomy (`config/taxonomy.yaml`)

Mirrors the 2026.2 program's *Conteúdo programático*. Subtopic IDs are stable keys used everywhere (DB, prompts, shortlist filenames). Keep labels in pt-BR.

```yaml
discipline: direito-processual-penal
topics:
  - id: T1
    label: "Medidas cautelares pessoais"
    subtopics:
      - {id: T1.1, label: "Princípios e marco regulatório das medidas cautelares"}
      - {id: T1.2, label: "Prisão preventiva e medidas cautelares diversas da prisão (art. 319 do CPP)"}
      - {id: T1.3, label: "Prisão temporária"}
      - {id: T1.4, label: "Prisão em flagrante e audiência de custódia"}
  - id: T2
    label: "Processo e procedimento"
    subtopics:
      - {id: T2.1, label: "Conceito de processo e pressupostos processuais"}
      - {id: T2.2, label: "Nulidades e defeitos dos atos processuais"}
      - {id: T2.3, label: "Conceito de procedimento; tipos de procedimento/ritos"}
      - {id: T2.4, label: "Procedimento comum: denúncia, citação e resposta à acusação"}
      - {id: T2.5, label: "Procedimento comum: absolvição sumária e saneamento (arts. 397 e 399 do CPP)"}
      - {id: T2.6, label: "Procedimento comum: audiência de instrução e julgamento (AIJ)"}
      - {id: T2.7, label: "Procedimento comum: sentença"}
      - {id: T2.8, label: "Tribunal do júri: generalidades"}
      - {id: T2.9, label: "Tribunal do júri: procedimento e plenário"}
  - id: T3
    label: "Prova"
    subtopics:
      - {id: T3.1, label: "Objetivos e erros do processo penal"}
      - {id: T3.2, label: "Ônus da prova"}
      - {id: T3.3, label: "Regimes de avaliação da prova e standards probatórios"}
      - {id: T3.4, label: "Provas em espécie"}
      - {id: T3.5, label: "Ilicitude da prova"}   # cross-cutting; used by the case method too
  - id: T4
    label: "Recursos e habeas corpus"
    subtopics:
      - {id: T4.1, label: "\"Teoria\" dos recursos no processo penal"}
      - {id: T4.2, label: "Recursos em espécie no processo penal"}
      - {id: T4.3, label: "Habeas corpus: cabimento, natureza jurídica, legitimidade, competência, estrutura"}
```

A question may map to up to 2 subtopics (`subtopic_ids`, primary first). Questions that are clearly *processo penal* but fit no subtopic get `subtopic_ids: []` and `classified_note` (they stay in the corpus, excluded from shortlists).

## 8. Data model

SQLite (single file, WAL mode). Use pydantic models mirroring these tables; `db.py` is the only module that touches SQL.

```sql
CREATE TABLE source_documents (
  id            TEXT PRIMARY KEY,     -- sha256 of file
  source_id     TEXT NOT NULL,        -- FK -> sources.yaml id
  url           TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,        -- ISO 8601
  kind          TEXT NOT NULL,        -- prova | gabarito | gabarito_justificado | dataset
  banca         TEXT,                 -- FGV | CEBRASPE | ...
  carreira      TEXT,                 -- oab | magistratura | mp | delegado | defensoria | outra
  certame       TEXT,                 -- e.g. "OAB XXXVIII", "PC-DF Delegado 2021"
  exam_year     INTEGER,
  local_path    TEXT
);

CREATE TABLE questions (
  id             TEXT PRIMARY KEY,    -- deterministic: sha256(source_doc_id + question_number)
  source_doc_id  TEXT NOT NULL REFERENCES source_documents(id),
  question_number TEXT,
  format         TEXT NOT NULL,       -- mcq4 | mcq5 | certo_errado | dissertativa | peca
  stem           TEXT NOT NULL,       -- full question text, pt-BR, verbatim
  choices        TEXT,                -- JSON: [{"label":"A","text":"..."}] ; NULL for non-MCQ
  answer_key     TEXT,                -- "A".."E" | "C"/"E" | NULL (discursive)
  answer_rationale TEXT,              -- banca's gabarito justificado / grading guideline, if available
  nullified      INTEGER DEFAULT 0,
  -- classification stage:
  discipline     TEXT,                -- direito-processual-penal | other | mixed
  subtopic_ids   TEXT,                -- JSON array of taxonomy ids, primary first
  difficulty     TEXT,                -- easy | medium | hard (LLM estimate; advisory only)
  classified_note TEXT,
  classify_model TEXT, classified_at TEXT,
  -- vetting stage:
  vet_status     TEXT DEFAULT 'unvetted',  -- unvetted | ok | flagged | rejected
  vet_reasons    TEXT,                -- JSON array of {code, detail} (§10)
  vet_model      TEXT, vetted_at TEXT
);

CREATE TABLE usage_log (
  question_id  TEXT REFERENCES questions(id),
  semester     TEXT NOT NULL,        -- "2026.2"
  subtopic_id  TEXT NOT NULL,
  used_at      TEXT,
  note         TEXT,
  PRIMARY KEY (question_id, semester)
);
```

Export: `bqpp export` writes `data/export/questions.jsonl` (one JSON object per question, joined with its source document) — the interchange format for anything downstream.

## 9. LLM layer (provider-agnostic)

### 9.1 Interface

```python
class LLMClient(Protocol):
    def complete_json(self, *, system: str, user: str,
                      json_schema: dict, model_tier: Literal["fast", "strong"],
                      max_tokens: int = 2048) -> dict: ...
```

- Backends are selected in `settings.toml` (`[llm] backend = "anthropic"`, plus per-tier model names, e.g. `fast = "claude-haiku-..."`, `strong = "claude-sonnet-..."`). Implement the Anthropic backend first; the interface must make an OpenAI-compatible or local (Ollama) backend a ~50-line addition.
- `complete_json` MUST validate the response against `json_schema` and retry up to 3 times on invalid JSON (re-prompting with the validation error). Fail loudly after retries; never store unvalidated output.
- All LLM calls log: model, tier, token counts, latency (to a local `llm_calls` table or logfile). Batch where the backend supports it; otherwise sequential with rate limiting.
- Classification uses tier `fast`; vetting uses tier `strong`.

### 9.2 Cost envelope (sanity check, not a hard limit)

Corpus of ~2,000 questions: classification ≈ 2,000 fast-tier calls of ~1k tokens each; vetting only runs on questions classified as *processo penal* (a few hundred strong-tier calls). This is deliberately cheap; do not over-engineer batching infrastructure.

### 9.3 Classification prompt (`prompts/classify.md`)

Template variables: `{taxonomy_yaml}`, `{question_json}`. Core instructions (implementer: keep this exact contract):

> You are classifying one question from a Brazilian legal exam. The question is in Portuguese.
> 1. Decide the discipline: `direito-processual-penal`, `other`, or `mixed` (procedural-penal reasoning is required but combined with another discipline).
> 2. If processual penal (or mixed), assign 1–2 subtopic ids from the taxonomy below (primary first). If none fits, return an empty list and explain briefly in `note`.
> 3. Estimate `difficulty` (easy/medium/hard) for a final-year law student.
> Return ONLY JSON matching the schema. Do not translate or alter the question.

JSON schema: `{discipline, subtopic_ids: string[], difficulty, note}` — all enums/ids validated in code against `taxonomy.yaml` after the call (an invalid subtopic id ⇒ retry once with the error appended, then store as unclassified).

## 10. Vetting stage

Two layers, cheap rules first:

### 10.1 Rule layer (no LLM)

- `nullified == 1` ⇒ `rejected`, reason `anulada`.
- Missing `answer_key` for MCQ/certo-errado ⇒ `flagged`, reason `no_gabarito`.
- `exam_year < cutoff` for subtopics matched in `law_watchlist.yaml` ⇒ mandatory LLM vetting with the watchlist entry injected into the prompt (see below). `config/law_watchlist.yaml` v1 seed:

```yaml
watchlist:
  - id: pacote-anticrime
    change: "Lei 13.964/2019 (Pacote Anticrime): rewrote prisão preventiva (arts. 311–316 CPP),
             created juiz das garantias, ANPP (art. 28-A CPP), changed cadeia de custódia (arts. 158-A ff.)"
    effective: 2020-01-23
    affects: [T1.1, T1.2, T1.4, T2.4, T3.4, T3.5]
  - id: execucao-provisoria-pena
    change: "STF ADCs 43/44/54 (2019): execução da pena só após trânsito em julgado"
    effective: 2019-11-07
    affects: [T4.1, T4.2, T4.3]
  - id: juiz-garantias-vigencia
    change: "STF ADIs 6.298 ff. (2023): juiz das garantias — implementation rules and deadlines"
    effective: 2023-08-24
    affects: [T2.1, T2.2, T2.4]
  # extend as needed; the professor maintains this file
```

### 10.2 LLM layer (`prompts/vet.md`, tier `strong`)

Runs on every question not already rejected, with any matching watchlist entries interpolated. Contract:

> You are vetting a Brazilian criminal-procedure exam question for classroom use in {current_year}. Assess:
> 1. **Outdatedness:** given legislative and jurisprudential changes (including, but not limited to, the listed watchlist items), is the official answer still correct today? If the *question* is still pedagogically usable but the *answer* changed, say so — that can be classroom gold, not garbage.
> 2. **Quality:** is the question well-formed, unambiguous, and does it actually test use of concepts (not pure memorization of article numbers)?
> Return JSON: `{verdict: ok|flagged|rejected, reasons: [{code, detail}], pedagogy_note}`.
> Codes: `desatualizada`, `resposta_mudou_mas_util`, `ambigua`, `decoreba`, `outros`.

Note the deliberate nuance: `resposta_mudou_mas_util` maps to `flagged`, **not** `rejected` — a question whose answer changed with the Pacote Anticrime is exactly the kind of thing worth discussing in class, and the shortlist shows it with a warning banner.

## 11. Curation stage (`bqpp curate`)

Input: `--semester 2026.2 --subtopics T1.1,T1.2,...` (default: all). For each subtopic:

1. Candidate pool = questions with this subtopic id (primary or secondary), `vet_status in (ok, flagged)`, not in `usage_log` for any previous semester.
2. Ranking (deterministic, no LLM): prefer `ok` over `flagged`; prefer dissertativa > certo_errado > mcq (open formats fit the method best — configurable weight in `settings.toml`); prefer newer `exam_year`; prefer questions with `answer_rationale` present (banca-authored resolution material); tie-break by carreira diversity across the semester's shortlists.
3. Emit `shortlists/{semester}/{subtopic_id}.md` with the top 5, each entry containing: full stem + choices verbatim; collapsed answer block (`<details>` tag) with answer key and rationale; provenance line (banca, certame, year, source URL); vet status with reasons; the LLM's `pedagogy_note`.
4. `bqpp use <question_id> --semester 2026.2 --subtopic T1.2` records the professor's pick in `usage_log` (this is the only write the curation front-end performs).

The shortlist file must be self-contained and readable on GitHub/Drive — the professor's entire review loop is "open markdown file, read, run one CLI command".

## 12. CLI (typer)

```
bqpp harvest  [--source ID]            # download / refresh sources
bqpp parse    [--source ID]            # segment PDFs → questions (idempotent, upsert by id)
bqpp classify [--only-unclassified]    # LLM classification
bqpp vet      [--only-unvetted]        # rules + LLM vetting
bqpp curate   --semester 2026.2 [--subtopics ...]
bqpp use      QUESTION_ID --semester S --subtopic T
bqpp stats                             # corpus counts by subtopic/format/vet_status (table output)
bqpp export                            # JSONL export
```

Every command supports `--dry-run` and `-v`. `harvest`/`parse`/`classify`/`vet` are safe to re-run: work is keyed by deterministic IDs and existing rows are skipped unless `--force`.

## 13. Parsing notes (the hard 20%)

- Work only with born-digital PDFs (`pdfplumber`); if a page yields no text layer, skip the document and log `needs_ocr`.
- Per-banca segmentation strategies, selected by the source adapter:
  - **FGV/OAB 1ª fase:** questions numbered `Questão N` or `N.`; alternatives `(A)–(D)`. Two-column layouts occur — use pdfplumber word coordinates to re-flow columns before segmenting.
  - **FGV/OAB 2ª fase Penal:** `PEÇA PRÁTICO-PROFISSIONAL` section (tag as `peca`, store stem only) + `QUESTÃO 1..4` discursive items; the *gabarito justificado* PDF maps to items by the same numbering — store into `answer_rationale`.
  - **Cebraspe:** items numbered continuously, judged `C`/`E`; gabarito PDFs are grids of item→letter (see fixture); `X` in gabarito = nullified ⇒ set `nullified=1`.
- Gabarito alignment is by (certame, question_number). A question without an aligned key is still stored (`answer_key NULL`) and flagged by the vet rule layer.
- **Acceptance test:** for each banca adapter, ≥ 1 fixture PDF in `tests/fixtures/` with a hand-written expected-output JSON; parser must reproduce it exactly. Target ≥ 95% of questions in a fixture document segmented correctly before an adapter is considered done.

## 14. Milestones (implement in this order)

- **M0 — skeleton:** repo layout, models, DB, settings, LLM interface + Anthropic backend, CLI stubs, CI running tests. *Definition of done: `bqpp stats` runs on an empty DB.*
- **M1 — bootstrap end-to-end (no PDF parsing):** `hf_datasets` adapter ingests `eduagarcia/oab_exams` (filter: Criminal Procedure) and `maritaca-ai/oab-bench` (criminal subset); classify → vet → curate produce real shortlists. *This milestone alone already delivers usable shortlists for most subtopics.* Definition of done: `shortlists/2026-2/` populated with plausible content for ≥ 15 subtopics.
- **M2 — FGV/OAB PDFs:** `fgv_oab` adapter + 1ª/2ª fase parsers + gabarito justificado ingestion. This upgrades shortlist quality (rationale-rich discursive items, years > 2018).
- **M3 — Cebraspe + generic PDFs:** certo/errado pipeline; manually curated concursos allow-list (delegado, magistratura, MP).
- **M4 — polish:** carreira-diversity ranking, `bqpp stats` report per semester, taxonomy/watchlist editing docs, JSONL export round-trip test.

## 15. Legal & ethical notes

- Exam questions are published public documents from public selection procedures; classroom reproduction with attribution is standard practice. Every shortlist entry must carry full attribution (banca, certame, year, URL). Verbatim text, never paraphrased.
- Do not ingest from commercial aggregators or redistribute their compilations. HF dataset licenses must be checked at ingest time and recorded in the manifest.
- The corpus is for internal teaching use; if it is ever published, revisit licensing per source.

## 16. Non-goals / future ideas (parked)

Web UI; automatic handout (docx/PDF) generation from a picked question; usage-aware spaced coverage across semesters; extension to *Processo Penal 1* / other disciplines (schema already permits it); OCR for scanned pre-2010 exams; fine-grained difficulty calibration from banca statistics.

---

*Implementation questions that arise should be resolved in favor of: smaller scope, deterministic behavior, provenance preserved, professor decides.*
