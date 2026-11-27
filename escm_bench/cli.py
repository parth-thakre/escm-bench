"""Unified CLI for escm-bench (`escm-bench arena|oracle|sandbox|audit|leaderboard`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from escm_bench.arena.bots import (
    FirstLegalBot,
    HeuristicSnowballBot,
    OpenAILLMTrainerBot,
    RandomBot,
    SubprocessBot,
    TrainerBot,
)
from escm_bench.arena.runner import run_arena_suite
from escm_bench.arena.scenarios import ARENA_SCENARIOS
from escm_bench.audit.anti_sabotage import run_full_audit
from escm_bench.leaderboard.report import load_scorecards, render_dark_html, render_markdown
from escm_bench.oracle.diff_oracle import grade_engine
from escm_bench.sandbox.generator import TRACK_PROMPTS, init_sandbox


def _default_results_dir() -> str:
    return str(Path.cwd() / "results")


def _save_scorecard(card: dict, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / f"{card['run_id']}.json"
    out.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return out


def _cmd_arena(args: argparse.Namespace) -> int:
    bot: TrainerBot
    if args.bot == "first-legal":
        bot = FirstLegalBot()
    elif args.bot == "random":
        bot = RandomBot(seed=args.seed)
    elif args.bot == "heuristic":
        bot = HeuristicSnowballBot()
    elif args.bot == "llm":
        if not args.model:
            print("error: --model is required when --bot llm", file=sys.stderr)
            return 2
        bot = OpenAILLMTrainerBot(
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            temperature=args.temperature,
            timeout_sec=args.timeout,
        )
    elif args.bot == "cmd":
        if not args.command:
            print("error: --command is required when --bot cmd", file=sys.stderr)
            return 2
        bot = SubprocessBot(command=args.command, name=args.model or None, timeout_sec=args.timeout)
    else:
        print(f"error: unknown bot '{args.bot}'", file=sys.stderr)
        return 2

    scenarios = args.scenarios.split(",") if args.scenarios else None
    sc = run_arena_suite(
        bot=bot,
        scenario_names=scenarios,
        scenarios_file=Path(args.scenarios_file) if args.scenarios_file else None,
        referee_cmd=args.referee_cmd or None,
        seeds_per_scenario=args.seeds,
        base_seed=args.seed,
        player_policy=args.player_policy,
        fog_of_war=not args.no_fog,
        track=args.track,
        run_id=args.run_id,
    )
    out = _save_scorecard(sc.to_dict(), Path(args.results_dir))
    print(json.dumps(sc.summary, indent=2))
    print(f"Saved scorecard: {out}")
    return 0


def _cmd_oracle(args: argparse.Namespace) -> int:
    golden_path = Path(args.golden) if args.golden else None
    cases_file = Path(args.cases_file) if args.cases_file else None
    sc = grade_engine(
        engine_spec=args.engine,
        model_name=args.model,
        include_mechanics=not args.no_mechanics,
        cases_file=cases_file,
        referee_cmd=args.referee_cmd or None,
        fuzz_count=args.fuzz,
        fuzz_seed=args.fuzz_seed,
        golden_path=golden_path,
        perf_repeats=args.perf_repeats,
        docker_cpus=args.docker_cpus,
        timeout_sec=args.timeout,
        track=args.track,
        mode=args.mode,
        run_id=args.run_id,
    )
    out = _save_scorecard(sc.to_dict(), Path(args.results_dir))
    print(json.dumps(sc.summary, indent=2))
    print(f"Saved scorecard: {out}")
    return 0 if sc.summary["perfect_parity"] else 1


def _cmd_sandbox(args: argparse.Namespace) -> int:
    meta = init_sandbox(
        run_id=args.run_id,
        track=args.track,
        model_label=args.model,
        starter_repo=Path(args.starter_repo) if args.starter_repo else None,
        base_dir=Path(args.dir) if args.dir else None,
        mount_showdown_ref=not args.no_ref,
        allow_network=args.allow_network,
        memory=args.memory,
        cpus=args.cpus,
    )
    print(json.dumps(meta, indent=2))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    sc = run_full_audit(
        workspace=Path(args.workspace),
        model_name=args.model,
        measured_scorecard_path=Path(args.scorecard) if args.scorecard else None,
        self_report_path=Path(args.self_report) if args.self_report else None,
        candidate_dir=Path(args.candidate_dir) if args.candidate_dir else None,
        require_self_report=not args.allow_missing_report,
        run_id=args.run_id,
    )
    out = _save_scorecard(sc.to_dict(), Path(args.results_dir))
    print(json.dumps(sc.summary, indent=2))
    print(f"Saved audit scorecard: {out}")
    return 0 if sc.summary["verdict"] == "CLEAN" else 1


def _cmd_leaderboard(args: argparse.Namespace) -> int:
    results_dir = Path(args.results_dir)
    cards = load_scorecards(results_dir)
    md = render_markdown(cards)
    print(md)
    if args.html:
        html_path = Path(args.html)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_dark_html(cards), encoding="utf-8")
        print(f"Wrote dark-mode leaderboard HTML: {html_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="escm-bench", description="Gen 7 Pokemon USUM AI & anti-sabotage coding benchmark suite")
    sub = p.add_subparsers(dest="subcmd", required=True)

    # arena
    pa = sub.add_parser("arena", help="Track 1 / 4: Run a Trainer AI on untouched Gen 7 Showdown under the Snowball objective")
    pa.add_argument("--bot", choices=["first-legal", "random", "heuristic", "llm", "cmd"], default="heuristic")
    pa.add_argument("--model", default="", help="Model name (required for --bot llm; optional label for --bot cmd)")
    pa.add_argument("--base-url", default="http://127.0.0.1:11434/v1", help="OpenAI-compatible endpoint (Ollama/vLLM/OpenAI)")
    pa.add_argument("--api-key", default=None)
    pa.add_argument("--command", default="", help="External decide-bot/1 command (or docker run ...)")
    pa.add_argument("--scenarios", default="", help=f"Comma-separated scenario IDs (default: all {len(ARENA_SCENARIOS)})")
    pa.add_argument("--scenarios-file", default="", help="Optional external JSON file with private ROM-hack or game scenarios")
    pa.add_argument("--referee-cmd", default="", help="Optional custom referee command speaking the arena JSON-RPC protocol")
    pa.add_argument("--seeds", type=int, default=2, help="Seeds per scenario")
    pa.add_argument("--seed", type=int, default=1001)
    pa.add_argument("--player-policy", choices=["conservative_nuzlocke", "aggressive", "noisy_human", "random"], default="conservative_nuzlocke")
    pa.add_argument("--no-fog", action="store_true", help="Disable fog of war (omniscient view)")
    pa.add_argument("--temperature", type=float, default=0.1)
    pa.add_argument("--timeout", type=float, default=30.0)
    pa.add_argument("--track", default="track1_arena")
    pa.add_argument("--run-id", default=None)
    pa.add_argument("--results-dir", default=_default_results_dir())
    pa.set_defaults(func=_cmd_arena)

    # oracle
    po = sub.add_parser("oracle", help="Tracks 2 / 3 / 5: Grade a Gen 7 engine via escm-diff-oracle/1 + hidden fuzz corpus")
    po.add_argument("--engine", required=True, help="'showdown' (ref), 'cmd:<binary>' (dev-only), or 'docker:escm-bench-grade:latest::<host_dir>::<cmd>' (isolated)")
    po.add_argument("--model", required=True, help="Label for leaderboard (e.g., escm-opus-5-5)")
    po.add_argument("--golden", default=None, help="Optional external escm gen7_golden.json path")
    po.add_argument("--cases-file", default=None, help="Optional external JSON/NDJSON file of private oracle test cases")
    po.add_argument("--referee-cmd", default="", help="Optional custom reference oracle command speaking escm-diff-oracle/1")
    po.add_argument("--no-mechanics", action="store_true")
    po.add_argument("--fuzz", type=int, default=None, help="Unseen Gen 7 fuzz battles (default: 20 for official Gen 7, 0 for custom suites)")
    po.add_argument("--fuzz-seed", type=int, default=None, help="Optional explicit fuzz seed (default: fresh cryptographic random seed)")
    po.add_argument("--perf-repeats", type=int, default=1, help="Verified replay passes timed by host wall-clock for Track 3 throughput")
    po.add_argument("--docker-cpus", default="16", help="CPU quota for Docker grading container (default: 16)")
    po.add_argument("--timeout", type=float, default=300.0, help="Per-pass candidate timeout in seconds")
    po.add_argument("--track", default="track2_oracle")
    po.add_argument("--mode", default="engine-grade")
    po.add_argument("--run-id", default=None)
    po.add_argument("--results-dir", default=_default_results_dir())
    po.set_defaults(func=_cmd_oracle)

    # sandbox
    ps = sub.add_parser("sandbox", help="Initialize an isolated local-only Docker sandbox for a coding agent run")
    ps.add_argument("--run-id", required=True)
    ps.add_argument("--track", choices=list(TRACK_PROMPTS.keys()), default="track5_from_scratch")
    ps.add_argument("--model", default="candidate")
    ps.add_argument("--starter-repo", default="", help="Starting engine directory (required for track2_parity and track3_perf)")
    ps.add_argument("--dir", default=None)
    ps.add_argument("--no-ref", action="store_true", help="Do not mount read-only pokemon-showdown reference")
    ps.add_argument("--allow-network", action="store_true", help="Allow container egress (default: --network=none)")
    ps.add_argument("--memory", default="8g")
    ps.add_argument("--cpus", default="8")
    ps.set_defaults(func=_cmd_sandbox)

    # audit
    pu = sub.add_parser("audit", help="Scan candidate workspace for sabotage patterns & audit self_report.json honesty")
    pu.add_argument("--workspace", required=True)
    pu.add_argument("--model", required=True)
    pu.add_argument("--scorecard", default=None, help="Measured oracle/arena scorecard JSON to compare against self_report.json")
    pu.add_argument("--self-report", default=None, help="Override path to self_report.json")
    pu.add_argument("--candidate-dir", default=None, help="Optional separate grading artifact dir (/candidate) to scan for bundled runtimes")
    pu.add_argument("--allow-missing-report", action="store_true", help="Do not flag missing self_report.json")
    pu.add_argument("--run-id", default=None)
    pu.add_argument("--results-dir", default=_default_results_dir())
    pu.set_defaults(func=_cmd_audit)

    # leaderboard
    pl = sub.add_parser("leaderboard", help="Render suite-grouped Markdown + dark-mode HTML leaderboard from results/")
    pl.add_argument("--results-dir", default=_default_results_dir())
    pl.add_argument("--html", default=str(Path.cwd() / "results" / "leaderboard.html"))
    pl.set_defaults(func=_cmd_leaderboard)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
