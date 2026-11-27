# escm-bench

A standalone, **anti-sabotage** multi-track benchmark suite built from the **Eclipsed Sun / Crescent Moon (`escm`)** Pokémon USUM Nuzlocke AI project.

Untouched `pokemon-showdown` Gen 7 serves as the deterministic external oracle and neutral battle referee — so models cannot pass by hallucinating simplified rules, disabling optimizations to hide parity bugs, zeroing out ML features (`* 0.0`), or faking status flags.

## Tracks

| Track | Target | What it tests |
|---|---|---|
| **Track 1 — Showdown Battle Arena** | Local models (Ollama/vLLM) & frontier API LLMs | Direct strategic play under strict fog of war (percentage-only opponent HP logs, Illusion preservation, unrevealed hidden trapping, per-actor log attribution) toward the **Nuzlocke Snowball** objective, strict JSON integer legality, and turn latency. |
| **Track 2 — Parity Bugfix Gauntlet** | Local & frontier coding models | Fixing Gen 7 mechanics bugs in a pinned `--starter-repo` against `escm-diff-oracle/1` + unseen Gen 7-legal fuzz battles. |
| **Track 3 — Perf Under Exactness Invariant** | Systems/Rust coding models | Maximizing harness-timed replay throughput (`--perf-repeats` over the exact same verified engine binary) **hard-gated on 100% oracle parity**. |
| **Track 4 — Trainer AI Build** | ML/RL/Search models | Shipping a `decide-bot/1` AI (RL, search, hybrid) evaluated head-to-head on untouched Showdown. |
| **Track 5 — From-Scratch Boss** | Frontier coding agents | Building an exact Gen 7 engine + Snowball trainer AI from scratch inside a locked-down **local-only Docker container**, scored on exactness, unseen fuzz, harness-timed throughput, AI strength, and two-sided **honesty/calibration**. |

## Quickstart

```bash
npm ci
uv venv .venv && uv pip install -e ".[dev]"

# 1. Run Track 1 baselines on untouched Gen 7 Showdown
.venv/bin/escm-bench arena --bot heuristic --seeds 2
.venv/bin/escm-bench arena --bot random --seeds 2

# 2. Benchmark a local Ollama / vLLM model directly as the USUM trainer brain
.venv/bin/escm-bench arena --bot llm \
  --model qwen3.8:27b \
  --base-url http://127.0.0.1:11434/v1 \
  --seeds 2

# 3. Grade a candidate binary inside the pinned no-Node, --network=none grading container
docker build -f escm_bench/assets/docker/Dockerfile.grade -t escm-bench-grade:latest escm_bench/assets/docker
.venv/bin/escm-bench oracle \
  --engine "docker:escm-bench-grade:latest::/abs/path/to/bin::/candidate/escm_diff_oracle" \
  --model candidate-x --fuzz 100 --perf-repeats 5 --docker-cpus 16

# (Dev-only host mode — tagged `[host-unisolated]` and ranked separately from Docker runs)
.venv/bin/escm-bench oracle --engine showdown --model ref-showdown --fuzz 20
.venv/bin/escm-bench oracle --engine "cmd:/path/to/escm_diff_oracle" --model escm-main --fuzz 50

# 4. Spin up a locked-down local-only Docker sandbox (NO private repo, --network=none)
docker build -f escm_bench/assets/docker/Dockerfile.sandbox -t escm-bench-sandbox:latest escm_bench/assets/docker
.venv/bin/escm-bench sandbox --run-id model-a-t5 --track track5_from_scratch --model model-a
# For Track 2 / Track 3, pass the starting engine snapshot (vendor Rust crates via `cargo vendor` for offline builds):
.venv/bin/escm-bench sandbox --run-id model-a-t2 --track track2_parity --model model-a --starter-repo /path/to/starter

# 5. Run the Anti-Sabotage heuristic scan & Self-Report Honesty Audit
.venv/bin/escm-bench audit --workspace ./sandboxes/model-a-t5/workspace --model model-a --scorecard results/track5_candidate.json

# 6. Render suite-grouped Markdown + dark-mode HTML leaderboard
.venv/bin/escm-bench leaderboard --html results/leaderboard.html
```

## Extending With Private Holdout Suites (Other Games & ROM Hacks)

Keep `escm-bench` public and run private zero-contamination holdouts (other Pokémon gens, modified ROM hacks, or completely different deterministic games) on top of the exact same harness. When installed as a wheel outside this checkout, set `ESCM_BENCH_NODE_MODULES=/path/to/node_modules` if using the default Showdown referees.

```bash
# Custom ROM-hack / private scenario JSON (full scenario object forwarded to custom referee)
.venv/bin/escm-bench arena --bot llm --model my-local-model \
  --scenarios-file ../private-bench/romhack_bosses.json \
  --referee-cmd "node ../private-bench/custom_referee.cjs"

# Custom private oracle cases & referee (automatically skips Gen 7 fuzz unless --fuzz N is explicitly set)
.venv/bin/escm-bench oracle --engine "cmd:./candidate_engine" --model candidate-x \
  --cases-file ../private-bench/mechanics.json \
  --referee-cmd "node ../private-bench/custom_oracle.cjs"
```

## Container Isolation & Security Notes

- **Build sandbox (`escm_bench/assets/docker/Dockerfile.sandbox`):** launched with host `--user $(id -u):$(id -g)`, `-i` (non-TTY safe), `--network=none` by default, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, and optional `:ro` mount of untouched `pokemon-showdown` at `/opt/ref/pokemon-showdown` (with `nodejs` installed for dev rule verification only). For offline Rust builds under `--network=none`, pre-vendor crates with `cargo vendor` + `.cargo/config.toml` inside `--starter-repo`.
- **Grading container (`escm_bench/assets/docker/Dockerfile.grade`):** omits Node.js and `pokemon-showdown` and runs `--network=none --read-only --cap-drop=ALL --tmpfs /tmp:rw,noexec,nosuid`. Only runs using the pinned `escm-bench-grade` image are tagged `isolated: true` on the leaderboard.
- **Anti-sabotage scanner (`escm-bench audit`):** a defense-in-depth **heuristic** companion to container isolation (scanning source, artifact directories for bundled runtimes, `.git/config` + hooks for host command injection, and symlink/FIFO tricks). Always inspect untrusted candidate workspaces read-only or via `git clone --no-local` rather than running `git` directly inside an untrusted checkout.

## License

MIT — see [LICENSE](LICENSE). Contains no game ROMs or copyrighted game assets.
