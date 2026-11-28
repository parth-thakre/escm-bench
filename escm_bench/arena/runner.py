"""Track 1 / Track 4 / Track 5 Arena runner using untouched Pokemon Showdown (or custom external referee)."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shlex
import statistics
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from escm_bench.arena.bots import TrainerBot, timed_write_line
from escm_bench.arena.scenarios import ARENA_SCENARIOS, get_scenarios
from escm_bench.contracts import ArenaBattleResult, Scorecard

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parent
REFEREE_SCRIPT = PKG_ROOT / "assets" / "referee" / "showdown_arena.cjs"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((pct / 100.0) * (len(s) - 1)))))
    return round(s[idx], 2)


def _node_env() -> dict[str, str]:
    env = dict(os.environ)
    candidates: list[str] = []
    if env.get("ESCM_BENCH_NODE_MODULES"):
        candidates.append(env["ESCM_BENCH_NODE_MODULES"])
    for p in (Path.cwd() / "node_modules", REPO_ROOT / "node_modules"):
        if p.exists():
            candidates.append(str(p))
    if env.get("NODE_PATH"):
        candidates.append(env["NODE_PATH"])
    if candidates:
        env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(candidates))
    return env


class ShowdownArenaRunner:
    """Persistent referee worker with stderr draining and end-to-end write+read RPC timeout enforcement."""

    def __init__(
        self,
        node_bin: str = "node",
        referee_cmd: str | None = None,
        rpc_timeout_sec: float = 30.0,
    ) -> None:
        self.rpc_timeout_sec = rpc_timeout_sec
        argv = shlex.split(referee_cmd) if referee_cmd else [node_bin, str(REFEREE_SCRIPT)]
        self._proc = subprocess.Popen(
            argv,
            env=_node_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._out_q: queue.Queue[str | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=40)

        def _read_stdout() -> None:
            assert self._proc.stdout is not None
            try:
                for line in self._proc.stdout:
                    self._out_q.put(line)
            finally:
                self._out_q.put(None)

        def _drain_stderr() -> None:
            assert self._proc.stderr is not None
            for line in self._proc.stderr:
                self._stderr_tail.append(line.rstrip())

        threading.Thread(target=_read_stdout, daemon=True).start()
        threading.Thread(target=_drain_stderr, daemon=True).start()

    def _rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        deadline = time.perf_counter() + self.rpc_timeout_sec
        req_line = json.dumps(payload, separators=(",", ":")) + "\n"
        write_budget = max(0.001, deadline - time.perf_counter())
        wrote = timed_write_line(self._proc, req_line, write_budget)
        if not wrote:
            self.close()
            raise TimeoutError(f"Arena referee stdin write timed out after {self.rpc_timeout_sec}s")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            self.close()
            raise TimeoutError(f"Arena referee timed out after {self.rpc_timeout_sec}s")
        try:
            line = self._out_q.get(timeout=remaining)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError(f"Arena referee timed out after {self.rpc_timeout_sec}s") from exc

        if line is None:
            err = "\n".join(self._stderr_tail)
            raise RuntimeError(f"Arena referee exited unexpectedly: {err}")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise RuntimeError(f"Arena referee error: {resp.get('error')}")
        return resp

    def run_battle(
        self,
        bot: TrainerBot,
        scenario: dict[str, Any],
        seed: list[int],
        player_policy: str = "conservative_nuzlocke",
        fog_of_war: bool = True,
        max_turns: int = 120,
    ) -> ArenaBattleResult:
        resp = self._rpc(
            {
                "cmd": "init",
                "seed": seed,
                "p1": scenario.get("p1"),
                "p2": scenario.get("p2"),
                "p1_aura": scenario.get("p1_aura"),
                "scenario": scenario,
                "player_policy": player_policy,
                "fog_of_war": fog_of_war,
                "max_turns": max_turns,
            }
        )

        decisions = 0
        valid_decisions = 0
        parse_errors = 0
        illegal_actions = 0
        timeouts = 0
        latencies: list[float] = []
        prompt_tokens = 0
        completion_tokens = 0

        guard = 0
        while not resp["done"] and resp.get("legal_actions") and guard < 600:
            guard += 1
            legal = resp["legal_actions"]
            dec = bot.choose_action(resp["state"], legal)
            decisions += 1
            if dec.valid:
                valid_decisions += 1
            if dec.parse_error:
                parse_errors += 1
            if dec.illegal_action:
                illegal_actions += 1
            if dec.timed_out:
                timeouts += 1
            if dec.latency_ms > 0:
                latencies.append(dec.latency_ms)
            prompt_tokens += dec.prompt_tokens
            completion_tokens += dec.completion_tokens

            resp = self._rpc({"cmd": "step", "action_id": dec.action_id})

        m = resp["metrics"]
        return ArenaBattleResult(
            scenario_id=scenario["id"],
            seed=seed,
            player_policy=player_policy,
            turns=int(m["turn"]),
            decisions=decisions,
            winner=m["winner"],
            ai_won=bool(m["ai_won"]),
            player_kos=int(m["player_kos"]),
            player_team_size=int(m["player_team_size"]),
            ai_kos=int(m["ai_kos"]),
            ai_team_size=int(m["ai_team_size"]),
            blood_drawn=bool(m["blood_drawn"]),
            multi_ko=bool(m["multi_ko"]),
            wiped=bool(m["wiped"]),
            snowball_reward=float(m["snowball_reward"]),
            valid_decisions=valid_decisions,
            parse_errors=parse_errors,
            illegal_actions=illegal_actions,
            timeouts=timeouts,
            latencies_ms=latencies,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


def _arena_suite_key(
    scenarios: list[dict[str, Any]],
    scenarios_file: Path | None,
    referee_cmd: str | None,
    fog_of_war: bool,
    player_policy: str,
    seeds_per_scenario: int,
) -> str:
    ids = [s["id"] for s in scenarios]
    content_hash = hashlib.sha256(
        json.dumps({"scenarios": scenarios, "referee": referee_cmd or "showdown"}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    if scenarios_file is None and referee_cmd is None and ids == list(ARENA_SCENARIOS.keys()):
        base = f"gen7-official-{len(ids)}scen"
    elif scenarios_file is not None or referee_cmd is not None:
        base = f"custom-{len(ids)}scen-{content_hash}"
    else:
        base = f"gen7-subset({','.join(sorted(ids))})"
    fog_tag = "fog" if fog_of_war else "nofog"
    return f"{base} | {fog_tag} | {player_policy} | s{seeds_per_scenario}"


def run_arena_suite(
    bot: TrainerBot,
    scenario_names: list[str] | None = None,
    scenarios_file: Path | None = None,
    referee_cmd: str | None = None,
    seeds_per_scenario: int = 2,
    base_seed: int = 1001,
    player_policy: str = "conservative_nuzlocke",
    fog_of_war: bool = True,
    track: str = "track1_arena",
    run_id: str | None = None,
) -> Scorecard:
    scenarios = get_scenarios(scenario_names, scenarios_file=scenarios_file)
    runner = ShowdownArenaRunner(referee_cmd=referee_cmd)
    battles: list[ArenaBattleResult] = []
    try:
        for s_idx, scen in enumerate(scenarios):
            for k in range(seeds_per_scenario):
                seed = [
                    (base_seed + s_idx * 97 + k * 13) % 65535 + 1,
                    (base_seed * 3 + s_idx * 31 + k * 29) % 65535 + 1,
                    (base_seed * 7 + s_idx * 53 + k * 41) % 65535 + 1,
                    (base_seed * 11 + s_idx * 71 + k * 59) % 65535 + 1,
                ]
                battles.append(
                    runner.run_battle(
                        bot=bot,
                        scenario=scen,
                        seed=seed,
                        player_policy=player_policy,
                        fog_of_war=fog_of_war,
                    )
                )
    finally:
        runner.close()
        bot.close()

    n = max(1, len(battles))
    total_decisions = sum(b.decisions for b in battles)
    total_valid = sum(b.valid_decisions for b in battles)
    total_parse_err = sum(b.parse_errors for b in battles)
    total_illegal = sum(b.illegal_actions for b in battles)
    total_timeouts = sum(b.timeouts for b in battles)
    all_lat = [x for b in battles for x in b.latencies_ms]
    total_ptok = sum(b.prompt_tokens for b in battles)
    total_ctok = sum(b.completion_tokens for b in battles)

    suite_key = _arena_suite_key(scenarios, scenarios_file, referee_cmd, fog_of_war, player_policy, seeds_per_scenario)
    summary = {
        "suite_key": suite_key,
        "battles": len(battles),
        "scenarios": [s["id"] for s in scenarios],
        "player_policy": player_policy,
        "fog_of_war": fog_of_war,
        "snowball_reward_mean": round(statistics.mean(b.snowball_reward for b in battles), 3),
        "kos_per_fight": round(statistics.mean(b.player_kos for b in battles), 3),
        "blood_drawn_pct": round(100.0 * sum(1 for b in battles if b.blood_drawn) / n, 1),
        "multi_ko_pct": round(100.0 * sum(1 for b in battles if b.multi_ko) / n, 1),
        "wipe_pct": round(100.0 * sum(1 for b in battles if b.wiped) / n, 1),
        "win_rate_pct": round(100.0 * sum(1 for b in battles if b.ai_won) / n, 1),
        "decisions": total_decisions,
        "legality_pct": round(100.0 * total_valid / max(1, total_decisions), 2),
        "parse_error_pct": round(100.0 * total_parse_err / max(1, total_decisions), 2),
        "illegal_action_pct": round(100.0 * total_illegal / max(1, total_decisions), 2),
        "timeout_pct": round(100.0 * total_timeouts / max(1, total_decisions), 2),
        "latency_p50_ms": _percentile(all_lat, 50),
        "latency_p95_ms": _percentile(all_lat, 95),
        "prompt_tokens_total": total_ptok,
        "completion_tokens_total": total_ctok,
    }

    safe_model = bot.name.replace("/", "_").replace(":", "_").replace(" ", "_")
    fog_tag = "fog" if fog_of_war else "nofog"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nonce = hashlib.sha256(f"{time.time_ns()}-{safe_model}-{suite_key}".encode("utf-8")).hexdigest()[:6]
    rid = run_id or f"{track}_{safe_model}_{fog_tag}_{player_policy}_{base_seed}_{ts}_{nonce}"
    return Scorecard(
        run_id=rid,
        track=track,
        model=bot.name,
        mode=bot.mode,
        summary=summary,
        details={"battles": [b.to_dict() for b in battles]},
    )
