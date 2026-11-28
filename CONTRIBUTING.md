# Contributing to `escm-bench`

## Running Tests Locally

```bash
npm ci
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/pytest -v
```

## Plugging In Private Holdout Suites (Other Games & ROM Hacks)

`escm-bench` is designed to be imported as a reusable harness by private holdout benchmarks without duplicating any arena, oracle, Docker isolation, anti-sabotage, or leaderboard code:

- **Custom Scenarios (Track 1 / 4):** pass `--scenarios-file /path/to/private_scenarios.json` to `escm-bench arena`.
- **Custom Oracle Cases (Track 2 / 3 / 5):** pass `--cases-file /path/to/private_cases.json` (or `--golden /path/to/golden.ndjson`) to `escm-bench oracle`.
- **Custom Game / ROM-Hack Referees:** pass `--referee-cmd "<command>"` to either `escm-bench arena` (line-delimited `init`/`step` JSON-RPC) or `escm-bench oracle` (`escm-diff-oracle/1` stdin/stdout JSON).

## Submitting Benchmark Results

When opening a PR with new model scorecards, include:
1. Exact model identifier, quantization (if local), and serving runtime (Ollama/vLLM/API).
2. The raw JSON scorecard emitted by `escm-bench arena` or `escm-bench oracle`.
3. For coding-agent runs (Tracks 2/3/5), the `escm-bench audit` output alongside the measured scorecard.
