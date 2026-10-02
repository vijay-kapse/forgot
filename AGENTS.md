# Working in this repo

`forgot` predicts which files usually change together, from git history alone.

## Rules

- **Zero runtime dependencies.** The standard library only. This is a load-bearing
  promise of the project, not a preference.
- **Fail open.** Anything on the commit path exits 0 when it cannot do its job.
  Never let an error here block someone's commit.
- **Every number is reproducible.** The README's benchmark table comes out of
  `python bench/benchmark.py`. If you change the model, regenerate it; never
  hand-edit a figure.
- **Keep the baselines strong.** `popularity` and `naming` in `src/forgot/evaluate.py`
  exist to challenge the model. Weakening one to make the model look better is
  the worst thing you could do to this project.

## Before committing

```bash
pytest -q
forgot check --json      # this repo uses its own hook
```

## Layout

| path | role |
|---|---|
| `src/forgot/history.py` | git log parsing, rename following |
| `src/forgot/model.py` | co-change model, lift and decay guards |
| `src/forgot/evaluate.py` | backtest harness and baselines |
| `src/forgot/report.py` | the terminal and JSON output |
| `src/forgot/cli.py` | argument parsing, exit codes |
| `bench/benchmark.py` | regenerates the README table |
