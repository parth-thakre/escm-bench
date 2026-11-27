"""Differential oracle grader (`escm-diff-oracle/1`) with hidden fuzz corpus, harness-timed throughput, and Docker isolation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from escm_bench.contracts import ORACLE_PROTOCOL, ORACLE_TIERS, Scorecard
from escm_bench.oracle.mechanics_cases import get_mechanics_cases

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parent
ORACLE_SCRIPT = PKG_ROOT / "assets" / "referee" / "showdown_oracle.cjs"
PINNED_GRADE_IMAGE_PREFIX = "escm-bench-grade"


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


def _default_uid_gid() -> str:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return "1000:1000"


def run_showdown_oracle(
    cases: list[dict[str, Any]],
    node_bin: str = "node",
    referee_cmd: str | None = None,
) -> dict[str, Any]:
    payload = {"protocol": ORACLE_PROTOCOL, "cases": cases}
    argv = shlex.split(referee_cmd) if referee_cmd else [node_bin, str(ORACLE_SCRIPT)]
    proc = subprocess.run(
        argv,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=_node_env(),
        check=True,
        timeout=300,
    )
    return json.loads(proc.stdout)


def generate_hidden_fuzz_corpus(
    count: int = 25,
    seed: int | None = None,
    max_steps: int = 16,
    node_bin: str = "node",
) -> dict[str, Any]:
    """Generate fresh, unseen seeded Gen 7-legal random battles + untouched Showdown golden traces."""
    actual_seed = seed if seed is not None else (secrets.randbelow(2**31 - 1) + 1)
    proc = subprocess.run(
        [node_bin, str(ORACLE_SCRIPT), "--fuzz", str(count), "--seed", str(actual_seed), "--steps", str(max_steps)],
        text=True,
        capture_output=True,
        env=_node_env(),
        check=True,
        timeout=300,
    )
    bundle = json.loads(proc.stdout)
    bundle["fuzz_seed"] = actual_seed
    return bundle


def build_docker_grade_argv(
    image: str,
    inner_cmd: str,
    binary_host_dir: str | Path | None = None,
    memory: str = "4g",
    cpus: str = "16",
    pids_limit: int = 256,
    user: str | None = None,
    container_name: str | None = None,
) -> list[str]:
    """Build a locked-down `docker run` command with NO network, NO Node in image, and read-only rootfs."""
    argv = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network=none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=128m",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--memory={memory}",
        f"--cpus={cpus}",
        f"--pids-limit={pids_limit}",
        "--user",
        user or _default_uid_gid(),
    ]
    if container_name:
        argv.extend(["--name", container_name])
    if binary_host_dir is not None:
        argv.extend(["-v", f"{Path(binary_host_dir).resolve()}:/candidate:ro"])
    argv.append(image)
    argv.extend(shlex.split(inner_cmd))
    return argv


_PINNED_DIGEST_RE = re.compile(r"\Aescm-bench-grade@sha256:[0-9a-f]{64}\Z")


def _is_pinned_isolated_spec(engine_spec: str) -> bool:
    """Require ONLY local `escm-bench-grade:latest` or exact `escm-bench-grade@sha256:<64-hex>`."""
    if not engine_spec.startswith("docker:"):
        return False
    image = engine_spec[len("docker:") :].split("::", 1)[0]
    return image == f"{PINNED_GRADE_IMAGE_PREFIX}:latest" or bool(_PINNED_DIGEST_RE.fullmatch(image))


def _resolve_engine_argv(
    engine_spec: str,
    *,
    docker_cpus: str = "16",
    container_name: str | None = None,
) -> list[str]:
    if engine_spec.startswith("cmd:"):
        return shlex.split(engine_spec[len("cmd:") :])
    if engine_spec.startswith("docker:"):
        if shutil.which("docker") is None:
            raise RuntimeError("Docker CLI is not installed on this machine; run on a Docker host or use cmd:...")
        parts = engine_spec[len("docker:") :].split("::")
        if len(parts) == 2:
            image, inner_cmd = parts
            return build_docker_grade_argv(image=image, inner_cmd=inner_cmd, cpus=docker_cpus, container_name=container_name)
        if len(parts) == 3:
            image, host_dir, inner_cmd = parts
            return build_docker_grade_argv(
                image=image, inner_cmd=inner_cmd, binary_host_dir=host_dir, cpus=docker_cpus, container_name=container_name
            )
        raise ValueError("Invalid docker engine spec; expected docker:<image>::<inner_cmd> or docker:<image>::<host_dir>::<inner_cmd>")
    raise ValueError(f"Unsupported engine spec '{engine_spec}'. Use 'showdown', 'cmd:...', or 'docker:...'")


def run_candidate_engine(
    engine_spec: str,
    cases: list[dict[str, Any]],
    *,
    timeout_sec: float = 300.0,
    docker_cpus: str = "16",
    referee_cmd: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, float]:
    """Execute a candidate engine safely; returns `(parsed_response, error_message, elapsed_sec)`.

    Never crashes the grader on candidate timeouts, non-zero exits, or malformed JSON; kills timed-out
    Docker containers explicitly so no runaway processes remain.
    """
    t0 = time.perf_counter()
    if engine_spec == "showdown":
        resp = run_showdown_oracle(cases, referee_cmd=referee_cmd)
        return resp, None, max(1e-6, time.perf_counter() - t0)

    container_name = f"escm-grade-{secrets.token_hex(6)}" if engine_spec.startswith("docker:") else None
    argv = _resolve_engine_argv(engine_spec, docker_cpus=docker_cpus, container_name=container_name)
    payload_bytes = json.dumps({"protocol": ORACLE_PROTOCOL, "cases": cases}).encode("utf-8")
    try:
        proc = subprocess.run(
            argv,
            input=payload_bytes,
            capture_output=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        if container_name:
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, check=False, timeout=15)
            except (subprocess.TimeoutExpired, OSError):
                pass
        return None, f"Candidate engine timed out after {timeout_sec}s", max(1e-6, time.perf_counter() - t0)

    elapsed = max(1e-6, time.perf_counter() - t0)
    if proc.returncode != 0:
        err_text = proc.stderr.decode("utf-8", errors="replace")[:400]
        return None, f"Candidate engine exited with code {proc.returncode}: {err_text}", elapsed

    try:
        stdout_text = proc.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return None, f"Candidate output is not valid UTF-8: {exc}", elapsed

    try:
        parsed = json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        return None, f"Candidate output is not valid JSON: {exc}", elapsed

    if not isinstance(parsed, dict):
        return None, f"Candidate output must be a JSON object, got {type(parsed).__name__}", elapsed
    return parsed, None, elapsed


def _validate_reference_traces(cases: list[dict[str, Any]], golden_traces: list[dict[str, Any]]) -> None:
    """Fail loudly if the reference oracle errored, omitted traces, or produced empty/malformed steps."""
    expected_ids = [c["id"] for c in cases]
    by_id: dict[str, dict[str, Any]] = {}
    for t in golden_traces:
        if not isinstance(t, dict) or "id" not in t:
            raise RuntimeError("Reference oracle returned a malformed trace without 'id'")
        by_id[str(t["id"])] = t

    missing = [cid for cid in expected_ids if cid not in by_id]
    if missing:
        raise RuntimeError(f"Reference oracle omitted {len(missing)}/{len(expected_ids)} trace(s): {missing[:5]}")
    errored = [(cid, by_id[cid]["error"]) for cid in expected_ids if by_id[cid].get("error")]
    if errored:
        raise RuntimeError(f"Reference oracle failed on {len(errored)} case(s): {errored[:3]}")
    empty = [cid for cid in expected_ids if not isinstance(by_id[cid].get("steps"), list) or len(by_id[cid]["steps"]) == 0]
    if empty:
        raise RuntimeError(f"Reference oracle returned empty steps for {len(empty)} case(s): {empty[:3]}")
    for cid in expected_ids:
        for idx, step in enumerate(by_id[cid]["steps"]):
            if not isinstance(step, dict):
                raise RuntimeError(f"Reference oracle trace {cid} step {idx} is not an object")
            missing_tiers = [tier for tier in ORACLE_TIERS if tier not in step]
            if missing_tiers:
                raise RuntimeError(f"Reference oracle trace {cid} step {idx} missing tiers: {missing_tiers}")


def compare_traces(
    golden_traces: list[dict[str, Any]],
    cand_response: dict[str, Any] | None,
    required_tiers: tuple[str, ...] = ORACLE_TIERS,
    candidate_error: str | None = None,
) -> dict[str, Any]:
    """Compare candidate traces against reference golden traces across all 5 tiers without crashing on bad candidate shapes."""
    total = len(golden_traces)
    if cand_response is None or candidate_error:
        return {
            "engine": "error",
            "declared_tiers": [],
            "skipped_tiers": list(required_tiers),
            "cases_total": total,
            "cases_exact_passed": 0,
            "exact_pass_pct": 0.0,
            "tier_pass": {t: 0 for t in required_tiers},
            "tier_fail": {t: total for t in required_tiers},
            "failed_cases": [{"id": "<engine>", "passed": False, "error": candidate_error or "no response"}],
        }

    raw_tiers = cand_response.get("tiers")
    declared_tiers = tuple(t for t in raw_tiers if isinstance(t, str)) if isinstance(raw_tiers, list) else ()
    skipped_tiers = [t for t in required_tiers if t not in declared_tiers]

    cand_by_id: dict[str, dict[str, Any]] = {}
    raw_traces = cand_response.get("traces")
    if isinstance(raw_traces, list):
        for t in raw_traces:
            if isinstance(t, dict) and isinstance(t.get("id"), str):
                cand_by_id[t["id"]] = t

    tier_pass = {t: 0 for t in required_tiers}
    tier_fail = {t: 0 for t in required_tiers}
    case_results: list[dict[str, Any]] = []
    exact_pass_count = 0

    for g in golden_traces:
        cid = g["id"]
        if g.get("error") or not g.get("steps"):
            for t in required_tiers:
                tier_fail[t] += 1
            case_results.append({"id": cid, "passed": False, "error": f"reference error: {g.get('error', 'empty steps')}"})
            continue

        c = cand_by_id.get(cid)
        if c is None:
            for t in required_tiers:
                tier_fail[t] += 1
            case_results.append({"id": cid, "passed": False, "error": "missing trace"})
            continue
        if c.get("error"):
            for t in required_tiers:
                tier_fail[t] += 1
            case_results.append({"id": cid, "passed": False, "error": str(c["error"])})
            continue

        g_steps = g.get("steps") or []
        c_steps = c.get("steps")
        if not isinstance(c_steps, list) or any(not isinstance(s, dict) for s in c_steps):
            for t in required_tiers:
                tier_fail[t] += 1
            case_results.append({"id": cid, "passed": False, "error": "malformed candidate steps"})
            continue

        diffs: dict[str, Any] = {}
        case_ok = len(skipped_tiers) == 0

        if len(g_steps) != len(c_steps):
            case_ok = False
            diffs["step_count"] = {"expected": len(g_steps), "actual": len(c_steps)}

        for t in required_tiers:
            if t not in declared_tiers:
                tier_fail[t] += 1
                diffs[t] = "SKIPPED (tier not declared)"
                case_ok = False
                continue
            first_diff = None
            for idx, (gs, cs) in enumerate(zip(g_steps, c_steps)):
                if gs.get(t) != cs.get(t):
                    first_diff = {"step": idx, "expected": gs.get(t), "actual": cs.get(t)}
                    break
            if first_diff is None and len(g_steps) == len(c_steps):
                tier_pass[t] += 1
            else:
                tier_fail[t] += 1
                case_ok = False
                diffs[t] = first_diff or "step count mismatch"

        if case_ok:
            exact_pass_count += 1
        case_results.append({"id": cid, "passed": case_ok, "diffs": diffs if not case_ok else {}})

    return {
        "engine": str(cand_response.get("engine", "unknown")),
        "declared_tiers": list(declared_tiers),
        "skipped_tiers": skipped_tiers,
        "cases_total": total,
        "cases_exact_passed": exact_pass_count,
        "exact_pass_pct": round(100.0 * exact_pass_count / max(1, total), 2),
        "tier_pass": tier_pass,
        "tier_fail": tier_fail,
        "failed_cases": [r for r in case_results if not r["passed"]][:25],
    }


def _measure_docker_noop_floor(image: str, docker_cpus: str, trials: int = 3) -> float:
    """Measure container startup overhead on the trusted grading image with a fixed `/bin/true` entrypoint."""
    times: list[float] = []
    for _ in range(trials):
        argv = build_docker_grade_argv(image=image, inner_cmd="", cpus=docker_cpus)
        # Insert --entrypoint /bin/true immediately before the image name so no custom entrypoint can run
        img_idx = argv.index(image)
        argv[img_idx:img_idx] = ["--entrypoint", "/bin/true"]
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=30)
            if proc.returncode == 0:
                times.append(time.perf_counter() - t0)
        except (subprocess.TimeoutExpired, OSError):
            break
    return min(times) if times else 0.0


def load_external_golden(golden_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw = golden_path.read_text(encoding="utf-8").strip()
    if raw.startswith("{") and "\n" not in raw:
        obj = json.loads(raw)
        return obj["cases"], obj["traces"]
    cases: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_in = {k: row[k] for k in ("id", "format", "seed", "p1", "p2", "choices") if k in row}
        cases.append(case_in)
        traces.append({"id": row["id"], "steps": row["steps"]})
    return cases, traces


def load_external_cases(cases_path: Path) -> list[dict[str, Any]]:
    raw = cases_path.read_text(encoding="utf-8").strip()
    if raw.startswith("["):
        return list(json.loads(raw))
    if raw.startswith("{") and "\n" not in raw:
        obj = json.loads(raw)
        if "cases" in obj:
            return list(obj["cases"])
        return [obj]
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def grade_engine(
    engine_spec: str,
    model_name: str,
    *,
    include_mechanics: bool = True,
    cases_file: Path | None = None,
    referee_cmd: str | None = None,
    fuzz_count: int | None = None,
    fuzz_seed: int | None = None,
    golden_path: Path | None = None,
    perf_repeats: int = 1,
    docker_cpus: str = "16",
    timeout_sec: float = 300.0,
    track: str = "track2_oracle",
    mode: str = "engine-grade",
    run_id: str | None = None,
) -> Scorecard:
    """Grade an engine on exact parity + harness-measured throughput over the exact same verified binary."""
    t0 = time.perf_counter()
    cases: list[dict[str, Any]] = []
    golden_traces: list[dict[str, Any]] = []

    is_custom_suite = cases_file is not None or referee_cmd is not None or golden_path is not None
    effective_fuzz = fuzz_count if fuzz_count is not None else (0 if is_custom_suite else 20)

    if golden_path is not None:
        g_cases, g_traces = load_external_golden(golden_path)
        _validate_reference_traces(g_cases, g_traces)
        cases.extend(g_cases)
        golden_traces.extend(g_traces)
    elif cases_file is not None:
        ext_cases = load_external_cases(cases_file)
        ext_ref = run_showdown_oracle(ext_cases, referee_cmd=referee_cmd)
        _validate_reference_traces(ext_cases, ext_ref.get("traces") or [])
        cases.extend(ext_cases)
        golden_traces.extend(ext_ref["traces"])
    elif include_mechanics:
        m_cases = get_mechanics_cases()
        m_ref = run_showdown_oracle(m_cases, referee_cmd=referee_cmd)
        _validate_reference_traces(m_cases, m_ref.get("traces") or [])
        cases.extend(m_cases)
        golden_traces.extend(m_ref["traces"])

    fuzz_start_idx = len(cases)
    actual_fuzz_seed: int | None = None
    if effective_fuzz > 0:
        f_bundle = generate_hidden_fuzz_corpus(count=effective_fuzz, seed=fuzz_seed)
        _validate_reference_traces(f_bundle["cases"], f_bundle.get("traces") or [])
        actual_fuzz_seed = int(f_bundle["fuzz_seed"])
        cases.extend(f_bundle["cases"])
        golden_traces.extend(f_bundle["traces"])

    if not cases:
        raise ValueError("No oracle cases selected; enable mechanics, provide --cases-file/--golden, or set --fuzz > 0")

    cand_resp, cand_err, cand_elapsed = run_candidate_engine(
        engine_spec, cases, timeout_sec=timeout_sec, docker_cpus=docker_cpus, referee_cmd=referee_cmd
    )
    overall = compare_traces(golden_traces, cand_resp, candidate_error=cand_err)
    mech_summary = compare_traces(golden_traces[:fuzz_start_idx], cand_resp, candidate_error=cand_err) if fuzz_start_idx > 0 else None
    fuzz_summary = compare_traces(golden_traces[fuzz_start_idx:], cand_resp, candidate_error=cand_err) if effective_fuzz > 0 else None

    perfect_parity = overall["cases_exact_passed"] == overall["cases_total"] and overall["cases_total"] > 0

    # Harness-measured, unforgeable throughput using ACTUAL verified trace step counts.
    # For `perf_repeats > 1`, packs all copies into one single process batch (paired by case ID, scaled timeout),
    # verifies every returned trace, and divides whole-batch steps by wall time minus a candidate-independent
    # Docker `/bin/true` startup floor (0.0 for host `cmd:` runs). Candidate can only lower its score, never raise it.
    golden_by_id = {t["id"]: t for t in golden_traces}
    verified_steps_per_pass = sum(len(golden_by_id[c["id"]].get("steps") or []) for c in cases)
    repeats = max(1, int(perf_repeats))
    perf_batch_ok = True
    perf_error: str | None = None
    isolated = _is_pinned_isolated_spec(engine_spec)
    docker_floor = 0.0
    if isolated:
        docker_image = engine_spec[len("docker:") :].split("::", 1)[0]
        docker_floor = _measure_docker_noop_floor(docker_image, docker_cpus)

    if perfect_parity and repeats > 1:
        multi_cases: list[dict[str, Any]] = []
        multi_golden: list[dict[str, Any]] = []
        for rep in range(repeats):
            for c in cases:
                g = golden_by_id[c["id"]]
                rep_id = f"{c['id']}__rep{rep}"
                c_copy = dict(c)
                c_copy["id"] = rep_id
                g_copy = dict(g)
                g_copy["id"] = rep_id
                multi_cases.append(c_copy)
                multi_golden.append(g_copy)
        m_resp, m_err, m_elapsed = run_candidate_engine(
            engine_spec, multi_cases, timeout_sec=timeout_sec * repeats, docker_cpus=docker_cpus, referee_cmd=referee_cmd
        )
        m_cmp = compare_traces(multi_golden, m_resp, candidate_error=m_err)
        if m_cmp["cases_exact_passed"] != m_cmp["cases_total"]:
            perf_batch_ok = False
            perf_error = m_err or f"perf batch matched {m_cmp['cases_exact_passed']}/{m_cmp['cases_total']}"
            measured_sps = 0.0
        else:
            effective_wall = max(m_elapsed - min(docker_floor, m_elapsed * 0.9), 1e-4)
            measured_sps = round((verified_steps_per_pass * repeats) / effective_wall, 2)
    else:
        effective_wall = max(cand_elapsed - min(docker_floor, cand_elapsed * 0.9), 1e-4)
        measured_sps = round(verified_steps_per_pass / effective_wall, 2)

    if not math.isfinite(measured_sps):
        measured_sps = 0.0
    gated_sps = measured_sps if (perfect_parity and perf_batch_ok) else 0.0

    iso_tag = "isolated" if isolated else "host-unisolated"
    cpu_tag = f"cpus{docker_cpus}" if engine_spec.startswith("docker:") else "hostcpu"
    suite_hash = hashlib.sha256(
        json.dumps(
            {
                "fixed_cases": cases[:fuzz_start_idx],
                "fixed_golden_steps": [len(t.get("steps") or []) for t in golden_traces[:fuzz_start_idx]],
                "fixed_golden_hash": hashlib.sha256(
                    json.dumps(golden_traces[:fuzz_start_idx], sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "fuzz_count": effective_fuzz,
                "referee": referee_cmd or "pokemon-showdown-0.11.11",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:8]

    if golden_path is not None:
        base_suite = f"external-golden:{golden_path.name}-{suite_hash}"
    elif cases_file is not None:
        base_suite = f"custom-cases:{cases_file.name}-{suite_hash}"
    elif referee_cmd is not None:
        base_suite = f"custom-referee-{suite_hash}"
    else:
        base_suite = f"gen7-official-{fuzz_start_idx}mech+{effective_fuzz}fuzz-{suite_hash}"
    suite_key = f"{base_suite} | {iso_tag} | {cpu_tag} | r{repeats}"

    elapsed_s = round(time.perf_counter() - t0, 3)
    summary = {
        "suite_key": suite_key,
        "isolated": isolated,
        "engine_spec": engine_spec,
        "cases_total": overall["cases_total"],
        "cases_exact_passed": overall["cases_exact_passed"],
        "exact_pass_pct": overall["exact_pass_pct"],
        "mechanics_passed": mech_summary["cases_exact_passed"] if mech_summary else None,
        "mechanics_total": mech_summary["cases_total"] if mech_summary else None,
        "fuzz_passed": fuzz_summary["cases_exact_passed"] if fuzz_summary else None,
        "fuzz_total": fuzz_summary["cases_total"] if fuzz_summary else None,
        "fuzz_seed": actual_fuzz_seed,
        "skipped_tiers": overall["skipped_tiers"],
        "perfect_parity": perfect_parity,
        "perf_batch_ok": perf_batch_ok,
        "perf_error": perf_error,
        "perf_repeats": repeats,
        "verified_steps_per_pass": verified_steps_per_pass,
        "measured_steps_per_sec_raw": measured_sps,
        "measured_steps_per_sec_gated": gated_sps,
        "elapsed_sec": elapsed_s,
    }

    safe_model = model_name.replace("/", "_").replace(":", "_").replace(" ", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nonce = secrets.token_hex(3)
    rid = run_id or f"{track}_{safe_model}_{iso_tag}_{ts}_{nonce}"
    return Scorecard(
        run_id=rid,
        track=track,
        model=model_name,
        mode=mode,
        summary=summary,
        details={
            "overall": overall,
            "mechanics": mech_summary,
            "fuzz": fuzz_summary,
        },
    )
