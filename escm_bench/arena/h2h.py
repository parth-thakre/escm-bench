"""Head-to-head mirrored Showdown Arena runner (Candidate Trained NN vs Our `escm` Baseline Model)."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from escm_bench.arena.bots import TrainerBot
from escm_bench.arena.runner import ShowdownArenaRunner, _percentile
from escm_bench.arena.scenarios import ARENA_SCENARIOS, get_scenarios
from escm_bench.contracts import Scorecard


@dataclass
class H2HBattleResult:
    scenario_id: str
    seed: list[int]
    candidate_side: str  # "p1" | "p2"
    turns: int
    winner: str | None  # "candidate" | "baseline" | None (draw/turn-cap)
    candidate_snowball: float
    baseline_snowball: float
    candidate_kos_inflicted: int
    baseline_kos_inflicted: int
    candidate_decisions: int
    candidate_valid_decisions: int
    candidate_latencies_ms: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_one_h2h_battle(
    runner: ShowdownArenaRunner,
    candidate_bot: TrainerBot,
    baseline_bot: TrainerBot,
    scenario: dict[str, Any],
    seed: list[int],
    *,
    candidate_side: str,
    fog_of_war: bool = True,
    max_turns: int = 120,
) -> H2HBattleResult:
    bots = {
        "p1": candidate_bot if candidate_side == "p1" else baseline_bot,
        "p2": baseline_bot if candidate_side == "p1" else candidate_bot,
    }
    resp = runner._rpc(
        {
            "cmd": "init",
            "mode": "h2h",
            "seed": seed,
            "p1": scenario.get("p1"),
            "p2": scenario.get("p2"),
            "p1_aura": scenario.get("p1_aura"),
            "p2_aura": scenario.get("p2_aura"),
            "scenario": scenario,
            "fog_of_war": fog_of_war,
            "max_turns": max_turns,
        }
    )

    cand_decisions = 0
    cand_valid = 0
    cand_latencies: list[float] = []

    guard = 0
    while not resp["done"] and resp.get("pending_sides") and guard < 800:
        guard += 1
        actions_payload: dict[str, int] = {}
        for side_tag in resp["pending_sides"]:
            legal = resp["legal_actions"][side_tag]
            state = resp["states"][side_tag]
            dec = bots[side_tag].choose_action(state, legal)
            if side_tag == candidate_side:
                cand_decisions += 1
                if dec.valid:
                    cand_valid += 1
                if dec.latency_ms > 0:
                    cand_latencies.append(dec.latency_ms)
            actions_payload[side_tag] = dec.action_id
        resp = runner._rpc({"cmd": "step", "actions": actions_payload})

    m = resp["metrics"]
    raw_winner = m.get("winner")  # "p1" | "p2" | None
    if raw_winner is None:
        winner = None
    elif raw_winner == candidate_side:
        winner = "candidate"
    else:
        winner = "baseline"

    if candidate_side == "p1":
        cand_sb = float(m["p1_snowball_reward"])
        base_sb = float(m["p2_snowball_reward"])
        cand_kos = int(m["p1_kos_inflicted"])
        base_kos = int(m["p2_kos_inflicted"])
    else:
        cand_sb = float(m["p2_snowball_reward"])
        base_sb = float(m["p1_snowball_reward"])
        cand_kos = int(m["p2_kos_inflicted"])
        base_kos = int(m["p1_kos_inflicted"])

    return H2HBattleResult(
        scenario_id=scenario["id"],
        seed=seed,
        candidate_side=candidate_side,
        turns=int(m["turn"]),
        winner=winner,
        candidate_snowball=cand_sb,
        baseline_snowball=base_sb,
        candidate_kos_inflicted=cand_kos,
        baseline_kos_inflicted=base_kos,
        candidate_decisions=cand_decisions,
        candidate_valid_decisions=cand_valid,
        candidate_latencies_ms=cand_latencies,
    )


def _h2h_suite_key(
    scenarios: list[dict[str, Any]],
    scenarios_file: Path | None,
    referee_cmd: str | None,
    baseline_name: str,
    fog_of_war: bool,
    seeds_per_scenario: int,
    mirror_sides: bool,
) -> str:
    ids = [s["id"] for s in scenarios]
    content_hash = hashlib.sha256(
        json.dumps({"scenarios": scenarios, "referee": referee_cmd or "showdown"}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    if scenarios_file is None and referee_cmd is None and ids == list(ARENA_SCENARIOS.keys()):
        base = f"gen7-h2h-{len(ids)}scen"
    elif scenarios_file is not None or referee_cmd is not None:
        base = f"custom-h2h-{len(ids)}scen-{content_hash}"
    else:
        base = f"gen7-h2h-subset({','.join(sorted(ids))})"
    fog_tag = "fog" if fog_of_war else "nofog"
    mir_tag = "mirrored" if mirror_sides else "p1only"
    return f"{base} | vs:{baseline_name} | {fog_tag} | {mir_tag} | s{seeds_per_scenario}"


def run_head_to_head(
    candidate_bot: TrainerBot,
    baseline_bot: TrainerBot,
    *,
    scenario_names: list[str] | None = None,
    scenarios_file: Path | None = None,
    referee_cmd: str | None = None,
    seeds_per_scenario: int = 2,
    base_seed: int = 2001,
    fog_of_war: bool = True,
    mirror_sides: bool = True,
    track: str = "h2h",
    run_id: str | None = None,
) -> Scorecard:
    """Pit candidate's trained NN bot directly against our reference `escm` model on untouched Showdown."""
    scenarios = get_scenarios(scenario_names, scenarios_file=scenarios_file)
    runner = ShowdownArenaRunner(referee_cmd=referee_cmd)
    battles: list[H2HBattleResult] = []
    sides = ("p1", "p2") if mirror_sides else ("p1",)
    try:
        for s_idx, scen in enumerate(scenarios):
            for k in range(seeds_per_scenario):
                seed = [
                    (base_seed + s_idx * 97 + k * 13) % 65535 + 1,
                    (base_seed * 3 + s_idx * 31 + k * 29) % 65535 + 1,
                    (base_seed * 7 + s_idx * 53 + k * 41) % 65535 + 1,
                    (base_seed * 11 + s_idx * 71 + k * 59) % 65535 + 1,
                ]
                for cand_side in sides:
                    battles.append(
                        _run_one_h2h_battle(
                            runner,
                            candidate_bot,
                            baseline_bot,
                            scen,
                            seed,
                            candidate_side=cand_side,
                            fog_of_war=fog_of_war,
                        )
                    )
    finally:
        runner.close()
        candidate_bot.close()
        baseline_bot.close()

    n = max(1, len(battles))
    cand_wins = sum(1 for b in battles if b.winner == "candidate")
    base_wins = sum(1 for b in battles if b.winner == "baseline")
    draws = sum(1 for b in battles if b.winner is None)

    cand_sb_mean = statistics.mean(b.candidate_snowball for b in battles) if battles else 0.0
    base_sb_mean = statistics.mean(b.baseline_snowball for b in battles) if battles else 0.0
    cand_kos_mean = statistics.mean(b.candidate_kos_inflicted for b in battles) if battles else 0.0
    base_kos_mean = statistics.mean(b.baseline_kos_inflicted for b in battles) if battles else 0.0

    # Side-balanced boss-role (p1) comparison when mirrored
    p1_cand = [b for b in battles if b.candidate_side == "p1"]
    p2_cand = [b for b in battles if b.candidate_side == "p2"]
    cand_boss_sb = statistics.mean(b.candidate_snowball for b in p1_cand) if p1_cand else cand_sb_mean
    base_boss_sb = statistics.mean(b.baseline_snowball for b in p2_cand) if p2_cand else base_sb_mean

    total_dec = sum(b.candidate_decisions for b in battles)
    total_valid = sum(b.candidate_valid_decisions for b in battles)
    all_lat = [x for b in battles for x in b.candidate_latencies_ms]

    suite_key = _h2h_suite_key(
        scenarios,
        scenarios_file,
        referee_cmd,
        baseline_bot.name,
        fog_of_war,
        seeds_per_scenario,
        mirror_sides,
    )
    summary = {
        "suite_key": suite_key,
        "baseline_model": baseline_bot.name,
        "battles": len(battles),
        "mirror_sides": mirror_sides,
        "fog_of_war": fog_of_war,
        "candidate_wins": cand_wins,
        "baseline_wins": base_wins,
        "draws": draws,
        "candidate_win_rate_pct": round(100.0 * cand_wins / n, 1),
        "baseline_win_rate_pct": round(100.0 * base_wins / n, 1),
        "draw_pct": round(100.0 * draws / n, 1),
        "candidate_snowball_mean": round(cand_sb_mean, 3),
        "baseline_snowball_mean": round(base_sb_mean, 3),
        "snowball_reward_delta": round(cand_sb_mean - base_sb_mean, 3),
        "candidate_boss_snowball_mean": round(cand_boss_sb, 3),
        "baseline_boss_snowball_mean": round(base_boss_sb, 3),
        "boss_snowball_delta": round(cand_boss_sb - base_boss_sb, 3),
        "candidate_kos_per_fight": round(cand_kos_mean, 3),
        "baseline_kos_per_fight": round(base_kos_mean, 3),
        "kos_delta": round(cand_kos_mean - base_kos_mean, 3),
        "candidate_legality_pct": round(100.0 * total_valid / max(1, total_dec), 2),
        "candidate_latency_p50_ms": _percentile(all_lat, 50),
        "candidate_latency_p95_ms": _percentile(all_lat, 95),
    }

    safe_model = candidate_bot.name.replace("/", "_").replace(":", "_").replace(" ", "_")
    safe_base = baseline_bot.name.replace("/", "_").replace(":", "_").replace(" ", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nonce = hashlib.sha256(f"{time.time_ns()}-{safe_model}-{safe_base}".encode("utf-8")).hexdigest()[:6]
    rid = run_id or f"{track}_{safe_model}_vs_{safe_base}_{ts}_{nonce}"
    return Scorecard(
        run_id=rid,
        track=track,
        model=candidate_bot.name,
        mode=f"h2h-vs-{baseline_bot.name}",
        summary=summary,
        details={"battles": [b.to_dict() for b in battles]},
    )
