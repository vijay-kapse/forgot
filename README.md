# forgot

**Names the files that usually change alongside the ones you staged — before the commit lands.**

Zero dependencies. Reads nothing but `.git`. No network, no index, no API key.

```
forgot: 2 files usually change with this edit but are not staged.

  logger_test.go   82%  22 of 84 commits touching logger.go
  docs/doc.md      43%  8 of 84 commits touching logger.go

  git add logger_test.go docs/doc.md

  Percentages weight recent commits above old ones, so they differ from the raw counts.
  Check one: forgot why logger.go logger_test.go
  Deliberate? Commit with --no-verify, or add the path to .forgotignore.
```

That is real output from `gin-gonic/gin`.

## Why

A codebase carries knowledge that is in none of its files: *these things change together*. Edit the serializer, bump the schema. Touch the public helper, add a changelog line. Change the workflow, change its sibling workflow.

People absorb that by working in a repo for months. A coding agent arrives with none of it, and so its characteristic failure is not broken syntax — it is **work that is complete-looking but partial**. The patch is right and the commit is short one file. CI finds out; the reviewer finds out; the agent does not.

`forgot` recovers that knowledge from the one place it is already written down — the commit history — and spends it at the only moment it matters, the commit itself.

## Install

As a [pre-commit](https://pre-commit.com) hook, which is where it earns its keep:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/vijaysk/forgot
    rev: v0.1.0
    hooks:
      - id: forgot          # blocks only on high-confidence misses
      # - id: forgot-warn   # same check, never blocks
```

Or standalone:

```bash
pip install forgot

forgot                              # check what is staged
forgot check src/auth.py            # ask about specific files
forgot check --json                 # machine-readable
forgot why src/auth.py CHANGES.rst  # show the commits behind a number
forgot eval                         # backtest the model on this repo
```

## Does it actually work

The nice thing about this problem is that it grades itself. Real commits are the labels.

**Protocol.** Order every commit by time. Train on the oldest 80%, query the newest 20% the model has never seen. For each held-out commit, reveal one file and ask for the rest. No future information reaches the model.

Two numbers matter, and they trade against each other:

- **coverage** — the share of edits it says anything at all about. Silence is useless; constant guessing is noise.
- **precision@5** — of the files it named, the share that really were in that commit. Measured only over edits where it spoke.
- **hit-rate@5** — the share of those edits where at least one named file really was in the commit.

Baselines, both deliberately given their best shot: `popularity` always names the repo's most-churned files; `naming` applies test/header conventions (`auth.py` → `test_auth.py`, `auth.c` → `auth.h`) and matches them against the repo's real layout, in any directory, not just a conventional `tests/`.

| repo | language | commits | coverage | precision@5 | recall@5 | hit-rate@5 | popularity | naming |
|---|---|---|---|---|---|---|---|---|
| `requests` | Python | 6,495 | 31% | 72% | 48% | **77%** | 50% | 29% |
| `flask` | Python | 5,557 | 52% | 49% | 27% | **68%** | 58% | 18% |
| `pytest` | Python | 17,787 | 42% | 50% | 24% | **61%** | 34% | 32% |
| `django` | Python | 34,964 | 57% | 40% | 25% | **55%** | 16% | 17% |
| `fastapi` | Python | 7,776 | 40% | 65% | 39% | **76%** | 39% | 0% |
| `prettier` | JS/TS | 11,937 | 64% | 69% | 51% | **77%** | 52% | 1% |
| `gin` | Go | 2,020 | 70% | 66% | 55% | **77%** | 46% | 57% |
| **mean** | | | **51%** | **59%** | **38%** | **70%** | 42% | 22% |

16,398 held-out queries across 7 repositories.

So: it speaks on about half of all edits, and when it speaks it correctly names a genuinely missing file **70% of the time** — against 42% for naming the repo's busiest files and 22% for test-naming conventions.

Reproduce the whole table, including the clones:

```bash
python bench/benchmark.py
```

Or on your own repo, which is the number you should actually care about:

```bash
forgot eval
```

## How it works

Pairwise co-change from `git log`, with three guards that do most of the work of keeping it quiet:

**Lift, not raw correlation.** `CHANGES.rst` changes in a third of all commits, so it correlates with everything. Dividing a pairing's strength by the file's own base rate drops files that are merely busy rather than genuinely coupled. This is why `forgot` says nothing when you stage only a README.

Lift is always measured from the single strongest pairing, never from the combined score — otherwise stacking several weak signals would inflate confidence *and* the lift that is supposed to check it, and popular files would walk straight through.

**Recency.** A repo's shape changes. Commits decay with a 180-day half-life, so a pairing that held for 200 commits three years ago and none since loses to one that has held all month. This is why the headline percentage and the all-time counts differ, and the report says so.

**Bulk commits dropped.** A reformat, a vendor drop or a licence-header sweep couples everything to everything. Commits above 50 files are the largest single source of noise in a co-change model, and are ignored.

Renames are followed, so history survives a file being moved. The model is cached in `.git/forgot/` — invisible to `git status`, keyed to `HEAD`, rebuilt when it goes stale.

**It fails open, always.** No repo, an unborn branch, thin history, a corrupt cache, git behaving unexpectedly — every one of those exits 0 in silence. A tool on the commit path does not get to be the reason you cannot commit.

## For agents

`--json` is the interface:

```bash
forgot check --json
```

```json
{
  "staged": ["src/requests/sessions.py"],
  "missing": [
    {
      "path": "src/requests/models.py",
      "confidence": 0.4337,
      "lift": 4.11,
      "because_of": "src/requests/sessions.py",
      "co_commits": 65,
      "total_commits": 294
    }
  ],
  "commits_analyzed": 3737,
  "note": "confidence is recency-weighted; co_commits/total_commits are all-time counts for the same pairing"
}
```

Worth pasting into your repo's `AGENTS.md`:

```markdown
Before committing, run `forgot check --json`. For each entry in `missing`,
either change that file too or say in the commit message why you did not.
Confidence is evidence, not instruction — `forgot why <a> <b>` lists the
commits behind any number.
```

## Configuration

Everything is a flag; nothing is required. The defaults are what the benchmark above was measured with.

| flag | default | what it does |
|---|---|---|
| `--fail-under` | `0.75` | exit 1 only at or above this confidence |
| `--warn-only` | off | always exit 0 |
| `--min-confidence` | `0.4` | floor for mentioning a file at all |
| `--min-lift` | `1.5` | how far above its base rate a file must co-occur |
| `--min-support` | `5` | commits a file needs before it may suggest anything |
| `--min-co-count` | `3` | commits a pairing needs |
| `--top-k` | `5` | most files to name |
| `--half-life` | `180` | days after which a commit counts half as much |
| `--max-commits` | `5000` | history depth |
| `--max-files-per-commit` | `50` | ignore commits larger than this |
| `--combine` | `noisy-or` | how evidence from several staged files is pooled (`max`, `mean`) |

Paths listed in `.forgotignore` (glob patterns, one per line) are never suggested.

## Limitations

Stated plainly, because the benchmark above already implies them.

- **It is silent about half the time.** Coupling that history has not recorded cannot be recovered. A new file, a new subsystem or a repo with a short history gets nothing — by design, rather than a guess.
- **It is correlation, not causation.** `forgot` knows that two files moved together, never why. The `why` subcommand exists so you can check rather than trust.
- **Squashed history hides it.** A repo that squashes every PR to one commit records far less about which files move together.
- **Diffuse test layouts weaken it.** Where one source file maps to many small test files (Django's `tests/<app>/`), coupling spreads thin and coverage drops — visible as Django's 55% in the table.
- **Not a correctness check.** It tells you a file is *usually* part of this change. Whether it is this time is your call.

## License

MIT
