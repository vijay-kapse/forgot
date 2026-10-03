# What git history already knows about your next commit

A repository records something that appears in none of its files: *these things
change together*. Edit the serializer, bump the schema. Touch the public helper,
add a changelog line. That knowledge is recoverable from commit history alone,
and it is strong enough to be worth acting on.

Measured across seven repositories in three languages: given **one** file from a
commit, a co-change model names a genuinely missing file **70% of the time it
speaks**, and it speaks on about half of all edits.

## This is not a new idea

It is a 2004 idea, and anyone writing about it owes the original authors a
citation before anything else.

**[Mining Version Histories to Guide Software Changes](https://www.st.cs.uni-saarland.de/papers/icse2004/?lang=en)** —
Thomas Zimmermann, Peter Weißgerber, Stephan Diehl and Andreas Zeller, ICSE
2004. Their tool, ROSE, applied association-rule mining to CVS histories under
the slogan *"Programmers who changed these functions also changed..."*. Their
stated motivation was, in their words, to **prevent errors due to incomplete
changes** — the same problem this repo exists for. Reported results: the top
three suggestions contained a correct location more than 70% of the time, and
26% of further changed files were predicted correctly.

**[Predicting Source Code Changes by Mining Change History](https://research.ibm.com/publications/predicting-source-code-changes-by-mining-change-history)** —
Annie T. T. Ying, Gail C. Murphy, Raymond Ng and Mark C. Chu-Carroll, IEEE
Transactions on Software Engineering 30(9):574–586, 2004. Independently and in
the same year, mining change patterns from history to recommend relevant code,
evaluated on Eclipse and Mozilla.

**eROSE** — the Eclipse plugin built on ROSE (Zimmermann, OOPSLA 2005). Its
project page is now [archived as "no longer current"](https://www.st.cs.uni-saarland.de/softevo/erose/?lang=en).

So the mechanism here is twenty-two years old and was never secret. The useful
question is not whether it works — that was settled in 2004 — but why nobody
uses it, and whether that has changed.

### A caution about comparing the numbers

ROSE's ">70% for the top three suggestions" and the 70% below are **not directly
comparable**, and I am claiming neither parity nor improvement. They differ in
`k`, in corpus and era (CVS-era Java/C against 2020s Python, JS/TS and Go), in
granularity (ROSE also predicted functions and variables, not just files), and
in conditioning — the figure below is computed only over the edits where the
model says anything at all. The resemblance is a coincidence worth noting, not a
result.

## What is actually new here

Three things, none of them the algorithm:

1. **A modern, multi-language replication.** 16,398 held-out queries across
   Python, JS/TS and Go repositories, on a time-ordered split, with baselines
   reported — which the 2004 work largely did not do.
2. **A reason the idea might matter now that it did not in 2004.** See below.
3. **It is installed rather than described.** `pip install forgot`, one line in
   `.pre-commit-config.yaml`, zero runtime dependencies.

## Why a 2004 idea might stick in 2026

The honest reason ROSE did not change the world: **a human working in a repo for
six months absorbs this knowledge anyway.** A recommender that tells an
experienced maintainer "you usually also touch `CHANGES.rst`" is telling them
something they already know. The value was real but marginal, and marginal value
does not overcome the cost of an IDE plugin.

Two things have changed.

**The reader changed.** A large share of commits are now written by coding agents
that arrive with none of that tacit knowledge and lose what they learn between
sessions. A [2026 census of 180 million repositories](https://arxiv.org/pdf/2606.24429)
reports GitHub Copilot's SWE agent at ~1.13M commits across 85,739 projects, and
Claude Code at ~850k commits across 17,295 projects between March and November
2025. For that reader the recommendation is not a reminder — it is information it
could not otherwise have. Agents' characteristic failure is not broken syntax but
**work that looks complete and is partial**, which is precisely the failure ROSE
set out to prevent.

**The cost changed.** eROSE's documented practical blocker was that building its
database "takes a while and cannot be interrupted," with the authors recommending
it be used only on small projects until incremental preprocessing existed. On the
repos below, building the model from scratch now takes:

| repo | commits | cold build | cached | model on disk |
|---|---|---|---|---|
| `requests` | 6,495 | 0.47s | 0.19s | 352 KB |
| `prettier` | 11,937 | 2.82s | 0.24s | 1.4 MB |
| `django` | 34,964 | 1.49s | 0.20s | 976 KB |

Over 90% of observed pairings are one-off coincidences that can never clear the
evidence threshold; dropping them at build time shrinks the model about ninefold
without changing a single prediction. A fifth of a second on the commit path is
a different proposition from an IDE plugin you must wait for.

## Method

Real commits are the labels, so the question grades itself.

Order every commit by time. Train on the oldest 80%, query the newest 20% the
model has never seen. For each held-out commit, reveal **one** file and ask for
the rest. Predictions are restricted to files seen during training. No future
information reaches the model.

Three metrics, because one alone is misleading:

- **coverage** — share of edits the model says anything about. Silence is
  useless; constant guessing is noise. Reporting precision without coverage
  would let a model that answers twice and is right both times look perfect.
- **precision@5** — of the files named, the share genuinely in that commit.
  Computed only over edits where the model spoke.
- **hit-rate@5** — share of those edits where at least one named file was real.

The model itself is pairwise co-change with three guards: **lift** measured from
the strongest single pairing (so pooled weak evidence cannot inflate the guard
meant to check it), a **180-day recency half-life**, and **dropping commits above
50 files**, since reformats and vendor drops couple everything to everything.
Thresholds come from a sweep (`bench/sweep.py`), not from taste.

## Results

| repo | language | commits | coverage | precision@5 | recall@5 | hit-rate@5 | popularity | naming |
|---|---|---|---|---|---|---|---|---|
| `requests` | Python | 6,495 | 31% | 72% | 48% | **77%** | 50% | 29% |
| `flask` | Python | 5,557 | 52% | 49% | 27% | **68%** | 58% | 18% |
| `pytest` | Python | 17,787 | 42% | 50% | 24% | **61%** | 34% | 32% |
| `django` | Python | 34,964 | 57% | 40% | 25% | **55%** | 16% | 17% |
| `fastapi` | Python | 7,776 | 40% | 65% | 39% | **76%** | 39% | 0% |
| `prettier` | JS/TS | 11,937 | 65% | 69% | 51% | **77%** | 52% | 1% |
| `gin` | Go | 2,020 | 70% | 66% | 55% | **77%** | 46% | 57% |
| **mean** | | | **51%** | **59%** | **38%** | **70%** | 42% | 22% |

16,398 held-out queries.

### The baselines were given their best shot

A weak baseline is the easiest way to lie with a benchmark, so both are stronger
than they need to be.

**`popularity`** names the repo's most-churned files. It has 100% coverage by
construction and is harder to beat than it looks — in a repo where the changelog
moves constantly, "always guess the changelog" is a real strategy. It reaches 58%
on flask.

**`naming`** applies conventions (`auth.py` → `test_auth.py`, `auth.c` →
`auth.h`) and matches them against the repo's actual layout **in any directory**.
The first version only looked in a conventional `tests/`, which scored 0% on
pytest purely because pytest uses `testing/`. Fixing that lifted it to 32% there
and 57% on gin. It is worth being explicit that this correction made the
headline result *less* impressive, which is the direction corrections usually go
when you are not fooling yourself.

The co-change model beats both on every repository.

## Limitations

- **Silent on roughly half of edits.** Coupling that history never recorded
  cannot be recovered. New files, new subsystems and young repos get nothing.
  That is a deliberate trade: guessing there would cost the precision that makes
  it worth installing.
- **Correlation, never causation.** The model knows two files moved together and
  nothing about why. `forgot why <a> <b>` exists so a claim can be checked
  rather than trusted.
- **Squashed history hides the signal.** A repo that squashes every PR into one
  commit records far less about what moves with what.
- **Diffuse test layouts weaken it.** Where one source file maps to many small
  test files — Django's `tests/<app>/` — coupling spreads thin. Django's 55% is
  the floor in this table, and it is the largest repo in it.
- **Seven repositories is a small sample**, all popular, all well-maintained,
  none proprietary, none with a squash-merge policy. These results should not be
  assumed to transfer to a private monorepo.
- **One file revealed is not how editing works.** Real agents stage several files
  before committing, which gives the model more to go on than this protocol does.
  The benchmark is therefore pessimistic in one direction and artificial in
  another.

## The question this does not answer

The motivating claim — that agents miss co-changing files more often than humans
do — is **asserted here, not measured**. It is plausible and it is testable, and
the data to test it is public: the agent-commit census above identifies millions
of agent-authored commits across tens of thousands of projects.

The experiment: partition a repository's history into agent-authored and
human-authored commits, then compare how often each kind omits a file the model
predicted with high confidence, controlling for commit size and subsystem. If
agent commits are measurably more incomplete, the motivation stops being a story
and becomes a result. If they are not, that is worth knowing too, and this tool
is a general convenience rather than an agent-era one.

That is the interesting paper here. This repository is the instrument.

## Reproduce

```bash
pip install forgot
git clone https://github.com/vijay-kapse/forgot && cd forgot
python bench/benchmark.py     # the table above, clones included
python bench/sweep.py         # the threshold sweep behind the defaults
forgot eval                   # the only number that matters: your repo
```

## References

1. T. Zimmermann, P. Weißgerber, S. Diehl, A. Zeller. *Mining Version Histories
   to Guide Software Changes.* ICSE 2004, Edinburgh.
2. A. T. T. Ying, G. C. Murphy, R. Ng, M. C. Chu-Carroll. *Predicting Source
   Code Changes by Mining Change History.* IEEE TSE 30(9):574–586, 2004.
3. T. Zimmermann. *eROSE: Guiding Programmers in Eclipse.* OOPSLA 2005
   (companion). Project page archived.
4. *Detecting AI Coding Agents in Open Source: A Validated Multi-Method Census
   of 180 Million Repositories.* arXiv:2606.24429, 2026.
