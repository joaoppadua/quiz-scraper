# banco-questoes-pp

Exam-question corpus builder and semester curation pipeline for **Processo Penal 2**
(Faculdade de Direito, UFF).

From 2026.2 on, each subtopic of the course opens with one question taken from a
Brazilian public exam for a *carreira jurídica*. Students work on it individually;
the next class resolves it together. This tool builds the corpus those questions
come from and produces a per-subtopic shortlist for the professor to pick from.

**It never picks the question.** It harvests, classifies, vets and shortlists —
the professor chooses. See `SPEC-questoes-pipeline.md` for the full design.

---

## Install

```bash
uv sync --extra dev
cp .env.example .env      # then fill in the key for your configured backend
```

Requires Python ≥ 3.11.

## The four-command workflow

```bash
bqpp harvest                      # download sources -> corpus (idempotent)
bqpp classify                     # LLM: discipline + subtopic ids   (tier: fast)
bqpp vet                          # rules + LLM: outdated? ambiguous? (tier: strong)
bqpp curate --semester 2026.2     # write shortlists/2026-2/*.md
```

The first `harvest` fetches 45 PDFs from the OAB at 1.5 s apart, so it takes a
couple of minutes. After that everything is cached on disk and re-running is
instant. `bqpp parse` re-reads that cache and re-segments without touching the
network — use it when only the parsing changed.

Every command takes `--dry-run` (do everything except write) and `-v`. All four are
safe to re-run: work is keyed by deterministic ids and existing rows are skipped
unless you pass `--force`.

Three more:

```bash
bqpp stats                        # corpus counts + per-subtopic coverage
bqpp parse                        # re-segment cached PDFs, no network
bqpp seed                         # ingest your own questions (config/seed_questions.yaml)
bqpp history                      # what you used in class, and your notes
bqpp export                       # data/export/questions.jsonl
```

## Where the shortlists go

`shortlists/` is gitignored, so generated class material never enters this (public)
repo. On the machine that runs the pipeline it is a **symlink into the course folder
on Google Drive**, so `curate` writes straight there and the files sync to every
device with no copy step:

```bash
ln -s "/path/to/Drive/UFF/Curso-processo-penal-2/shortlists" shortlists
```

Note the `.gitignore` entry is `shortlists` with **no trailing slash** — git treats a
symlink as a file, so `shortlists/` would fail to match it and commit the absolute
path. On a fresh clone with no symlink, `curate` just writes a normal local directory.

Markdown viewers differ: the `<details>` block that hides the gabarito renders on
GitHub and in editors like Obsidian, but Drive's own preview shows the raw tags — and
therefore the answer. Read them in a markdown app if that matters for class.

## Reading a shortlist

`shortlists/2026-2/T1.2.md` is self-contained — open it on GitHub or Drive and you
have everything: the question verbatim, a collapsed `<details>` block with the
answer key and the banca's own commentary, the provenance line (banca, certame,
year, URL), and any vetting warnings.

A `⚠ Sinalizada` banner is **not** a reason to skip the question. The most common
flag is `resposta_mudou_mas_util` — the question is fine but the law moved under it
(usually the *Pacote Anticrime*). Those are often the best classroom material.

When you pick one, copy the command at the bottom of the entry:

```bash
bqpp use <question_id> --semester 2026.2 --subtopic T1.2
```

That records it in the usage log, and the question will not be offered again in a
**later** semester. It stays in the current semester's shortlist, marked
`✅ Já escolhida` — re-running `curate` mid-selection must not quietly swap your pick
for the next-ranked question.

The usage log is also the only record of what you actually taught, and `--note` is
where the "how did it go in class" goes. `bqpp history --semester 2026.2` reads it
back. Nothing else does, so it is worth keeping in the backup of `data/corpus.sqlite`.

## Configuration

| File | What you change there |
|---|---|
| `config/taxonomy.yaml` | The subtopic list. Adding is safe; **renaming an id orphans usage-log rows and existing shortlists.** |
| `config/law_watchlist.yaml` | Legal changes that can invalidate an older answer. An entry fires when a question predates `effective` and matches one of `affects`. Extend this as the law moves. |
| `config/sources.yaml` | Which datasets/exams to harvest and how to filter them. |
| `config/settings.toml` | LLM backend and model ids, ranking weights, shortlist size, paths. |

### Switching LLM provider

`config/settings.toml` selects the backend; nothing in the code hardcodes a vendor.

```toml
[llm]
backend          = "gemini"      # primary
fallback_backend = "openai"      # used on timeouts / rate limits / 5xx; "" to disable
fast_model       = "gemini-3.5-flash-lite"   # classification (high volume)
strong_model     = "gemini-3.6-flash"        # vetting (needs judgement)
```

Model ids live in config precisely because they drift — bump them here, no code
change. Adding a third provider is one ~40-line module implementing the
`LLMBackend` protocol in `src/bqpp/llm/base.py`.

Every LLM response is validated against a JSON schema in code and re-prompted with
the validation error up to `max_attempts` times. Unvalidated output is never stored.

## Where the questions come from

| Source | What it gives |
|---|---|
| `eduagarcia/oab_exams` (HF) | 177 OAB 1ª-fase multiple-choice items, 2010–2018 |
| `maritaca-ai/oab-bench` (HF) | 30 OAB 2ª-fase discursive items, exams 39º–44º, with examiner guidelines |
| `oab-2f-penal` (OAB site) | **the 2ª-fase Direito Penal *padrão de respostas* for every exam since 2010** |

That third one is the M2 milestone and the richest material in the corpus. Each
*padrão* carries the question verbatim **and the banca's own `Gabarito
Comentado`** — the reasoning FGV itself published after the recursos phase, which
is exactly what you want in front of a class.

### Why it fetches from the OAB and not from FGV

The spec pointed this at `oab.fgv.br`. That site no longer serves exam PDFs:
every `home.aspx?key=N` returns the same 7 kB page with a *seccional* dropdown
and no links at all. The Conselho Federal publishes the byte-identical files at
`examedeordem.oab.org.br`, over ordinary GET, so that is where `harvest` goes.
FGV is still recorded as the *banca*; the OAB is only the host.

Of the 46 exams: 45 published a Penal padrão (the 47º is mid-cycle), 35 use a
layout the segmenter recognises, and those yield **139 questions** — 111
discursivas and 28 peças — every one with the banca's commentary attached. The
ten older exams and the sections whose enunciado is a scanned image are logged
and skipped rather than ingested half-empty.

## Scope

**M0 + M1 + M2.** Not built yet:

- **M3** — Cebraspe *certo/errado* and the generic MP/TJ PDF adapter
- **M2.5** — the 1ª-fase objective provas (two-column reflow; deferred deliberately,
  since multiple-choice ranks lowest for this teaching method)

Much of the corpus predates the *Pacote Anticrime* (2020), so `bqpp vet` does
real work here rather than rubber-stamping. Run `bqpp stats` after curating:
a subtopic with a red `0` is a coverage gap, not a bug. Two are expected —
**T1.1** and **T3.3** are doctrinal topics objective exams avoid. Fill them
yourself via `config/seed_questions.yaml`, or wait for M3.

## Tests

```bash
uv run pytest
```

The suite never touches the network or an LLM: provider SDKs are stubbed, and the
HuggingFace adapters are tested against committed real-row fixtures in
`tests/fixtures/`.

## Legal note

Exam questions are public documents from public selection procedures; classroom
reproduction with attribution is standard practice. Every shortlist entry carries
full attribution and reproduces the text verbatim, never paraphrased. Nothing is
ingested from commercial aggregators. The corpus is for internal teaching use — if
it is ever published, revisit licensing per source.
