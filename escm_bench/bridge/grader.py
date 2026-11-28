"""Mechanical end-to-end grader for candidate Azahar 3DS UDP RPC v2 <-> ML bridges."""

from __future__ import annotations

import hashlib
import os
import shlex
import signal
import statistics
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from escm_bench.bridge.mock_azahar import (
    BATTLE_CRO_BASE,
    CODE_CAVE_OFF,
    HOOK_SITE_OFF,
    MAILBOX_MAGIC,
    ORIG_HOOK_INSN,
    MockAzaharServer,
)
from escm_bench.bridge.pk7 import make_pk7_slot
from escm_bench.contracts import Scorecard


def _is_valid_arm_branch_to_cave(insn: int) -> bool:
    """Verify instruction at HOOK_SITE_OFF is an unconditional ARM `B` targeting the code cave."""
    if (insn & 0xFF000000) != 0xEA000000:
        return False
    imm24 = insn & 0x00FFFFFF
    if imm24 & 0x00800000:
        imm24 -= 0x01000000
    target_off = HOOK_SITE_OFF + 8 + (imm24 << 2)
    return CODE_CAVE_OFF <= target_off < (CODE_CAVE_OFF + 0x600)


def _build_bridge_test_turns() -> list[dict[str, Any]]:
    """Six realistic USUM battle RAM snapshots testing move choice, PP-0 avoidance, forced switch, Z-moves, and status."""
    # Species IDs (National Dex): 727 Incineroar, 778 Mimikyu, 445 Garchomp, 800 Necrozma, 376 Metagross, 658 Greninja, 143 Snorlax, 130 Gyarados
    # Move IDs: 663 Darkest Lariat, 394 Flare Blitz, 252 Fake Out, 369 U-turn, 583 Play Rough, 425 Shadow Sneak, 14 Swords Dance, 89 Earthquake, 407 Dragon Claw, 722 Photon Geyser
    return [
        {
            "id": "turn1_normal_lead",
            "turn": 1,
            "ai_active_slot": 0,
            "player_active_slot": 0,
            "can_z_mask": 0b0001,
            "ai_party": [
                {"species": 727, "level": 60, "hp": 210, "max_hp": 210, "moves": [663, 394, 252, 369], "pps": [10, 15, 10, 20], "item": 780, "ability": 22},
                {"species": 778, "level": 60, "hp": 160, "max_hp": 160, "moves": [583, 425, 14, 261], "pps": [10, 30, 20, 15], "item": 275, "ability": 209},
            ],
            "player_party": [
                {"species": 376, "level": 58, "hp": 185, "max_hp": 185, "moves": [309, 418, 89, 428], "pps": [10, 30, 10, 15], "item": 234, "ability": 29},
                {"species": 658, "level": 58, "hp": 170, "max_hp": 170, "moves": [57, 399, 58, 369], "pps": [15, 15, 10, 20], "item": 270, "ability": 168},
            ],
            "legal_move_slots": {0, 1, 2, 3},
            "legal_switch_slots": {1},
        },
        {
            "id": "turn2_pp_exhausted_slot0",
            "turn": 2,
            "ai_active_slot": 0,
            "player_active_slot": 0,
            "can_z_mask": 0,
            "ai_party": [
                # Move slots 0 and 1 have 0 PP -> bridge MUST NOT pick slot 0 or 1!
                {"species": 445, "level": 62, "hp": 140, "max_hp": 225, "moves": [89, 407, 157, 14], "pps": [0, 0, 10, 20], "item": 270, "ability": 24},
                {"species": 778, "level": 60, "hp": 160, "max_hp": 160, "moves": [583, 425, 14, 261], "pps": [10, 30, 20, 15], "item": 275, "ability": 209},
            ],
            "player_party": [
                {"species": 130, "level": 58, "hp": 90, "max_hp": 195, "moves": [127, 242, 423, 349], "pps": [15, 15, 15, 20], "item": 234, "ability": 22},
            ],
            "legal_move_slots": {2, 3},
            "legal_switch_slots": {1},
        },
        {
            "id": "turn3_single_move_only_last_mon",
            "turn": 5,
            "ai_active_slot": 1,
            "player_active_slot": 0,
            "can_z_mask": 0,
            "ai_party": [
                # Slot 0 fainted! Slot 1 active with only move slot 2 having PP > 0 and NO alive bench!
                {"species": 727, "level": 60, "hp": 0, "max_hp": 210, "moves": [663, 394, 252, 369], "pps": [5, 10, 5, 10], "item": 0, "ability": 22},
                {"species": 800, "level": 60, "hp": 195, "max_hp": 205, "moves": [722, 406, 408, 430], "pps": [0, 0, 15, 0], "item": 923, "ability": 233},
            ],
            "player_party": [
                {"species": 143, "level": 60, "hp": 120, "max_hp": 270, "moves": [34, 242, 89, 156], "pps": [15, 15, 10, 10], "item": 234, "ability": 47},
            ],
            "legal_move_slots": {2},
            "legal_switch_slots": set(),
        },
        {
            "id": "turn4_burned_active_two_bench",
            "turn": 7,
            "ai_active_slot": 0,
            "player_active_slot": 1,
            "can_z_mask": 0b0010,
            "ai_party": [
                {"species": 727, "level": 60, "hp": 45, "max_hp": 210, "moves": [663, 394, 0, 0], "pps": [8, 12, 0, 0], "status_word": 0x10, "item": 780, "ability": 22},
                {"species": 778, "level": 60, "hp": 0, "max_hp": 160, "moves": [583, 425, 14, 261], "pps": [10, 30, 20, 15], "item": 0, "ability": 209},
                {"species": 445, "level": 62, "hp": 225, "max_hp": 225, "moves": [89, 407, 157, 14], "pps": [10, 15, 10, 20], "item": 270, "ability": 24},
            ],
            "player_party": [
                {"species": 376, "level": 58, "hp": 0, "max_hp": 185, "moves": [309, 418, 89, 428], "pps": [10, 30, 10, 15], "item": 0, "ability": 29},
                {"species": 658, "level": 58, "hp": 140, "max_hp": 170, "moves": [57, 399, 58, 369], "pps": [12, 14, 9, 19], "item": 270, "ability": 168},
            ],
            "legal_move_slots": {0, 1},
            "legal_switch_slots": {2},
        },
        {
            "id": "turn5_zmove_available",
            "turn": 9,
            "ai_active_slot": 0,
            "player_active_slot": 0,
            "can_z_mask": 0b0001,
            "ai_party": [
                {"species": 800, "level": 60, "hp": 205, "max_hp": 205, "moves": [722, 406, 408, 430], "pps": [5, 10, 20, 10], "item": 923, "ability": 233},
            ],
            "player_party": [
                {"species": 143, "level": 60, "hp": 160, "max_hp": 270, "moves": [34, 242, 89, 156], "pps": [15, 15, 10, 10], "item": 234, "ability": 47},
            ],
            "legal_move_slots": {0, 1, 2, 3},
            "legal_switch_slots": set(),
        },
        {
            "id": "turn6_paralyzed_endgame",
            "turn": 12,
            "ai_active_slot": 2,
            "player_active_slot": 0,
            "can_z_mask": 0,
            "ai_party": [
                {"species": 727, "level": 60, "hp": 0, "max_hp": 210, "moves": [663, 394, 0, 0], "pps": [0, 0, 0, 0], "item": 0, "ability": 22},
                {"species": 778, "level": 60, "hp": 35, "max_hp": 160, "moves": [583, 425, 0, 0], "pps": [5, 10, 0, 0], "item": 0, "ability": 209},
                {"species": 445, "level": 62, "hp": 180, "max_hp": 225, "moves": [89, 407, 157, 14], "pps": [0, 8, 0, 12], "status_word": 0x40, "item": 270, "ability": 24},
            ],
            "player_party": [
                {"species": 658, "level": 58, "hp": 55, "max_hp": 170, "moves": [57, 399, 58, 369], "pps": [10, 10, 5, 15], "item": 270, "ability": 168},
            ],
            "legal_move_slots": {1, 3},
            "legal_switch_slots": {1},
        },
    ]


def _validate_written_action(action: dict[str, int], turn_spec: dict[str, Any]) -> tuple[bool, str]:
    kind = action["kind"]
    move_slot = action["move_slot"]
    use_z = action["use_z"]
    switch_slot = action["switch_slot"]
    can_z_mask = int(turn_spec.get("can_z_mask", 0))

    if kind == 0:  # Fight
        if move_slot not in turn_spec["legal_move_slots"]:
            return False, f"illegal move_slot={move_slot} (legal={sorted(turn_spec['legal_move_slots'])})"
        if use_z not in (0, 1):
            return False, f"invalid use_z={use_z}"
        if use_z == 1 and not (can_z_mask & (1 << move_slot)):
            return False, f"use_z=1 requested on move_slot={move_slot} with can_z_mask=0b{can_z_mask:04b}"
        return True, "ok"
    if kind == 2:  # Switch
        if switch_slot not in turn_spec["legal_switch_slots"]:
            return False, f"illegal switch_slot={switch_slot} (legal={sorted(turn_spec['legal_switch_slots'])})"
        if use_z != 0:
            return False, f"switch action cannot set use_z={use_z}"
        return True, "ok"
    return False, f"unsupported out_action.kind={kind} (expected 0=Fight or 2=Switch)"


def grade_bridge(
    bridge_cmd: str,
    model_name: str,
    *,
    startup_timeout_sec: float = 10.0,
    turn_timeout_sec: float = 2.0,
    teardown_timeout_sec: float = 5.0,
    max_read_chunk: int = 32,
    max_write_chunk: int = 24,
    track: str = "bridge",
    run_id: str | None = None,
) -> Scorecard:
    """Mechanically grade an Azahar UDP RPC v2 <-> ML bridge against the Mock Azahar Server."""
    server = MockAzaharServer(
        host="127.0.0.1",
        port=0,
        max_read_chunk=max_read_chunk,
        max_write_chunk=max_write_chunk,
    )
    server.start()

    argv = shlex.split(bridge_cmd) + ["--host", "127.0.0.1", "--port", str(server.port)]
    env = dict(os.environ)
    env["AZAHAR_RPC_HOST"] = "127.0.0.1"
    env["AZAHAR_RPC_PORT"] = str(server.port)
    env["ESCM_BATTLE_CRO_BASE"] = f"0x{BATTLE_CRO_BASE:08X}"

    proc = subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_tail: deque[str] = deque(maxlen=40)
    stderr_tail: deque[str] = deque(maxlen=40)

    def _drain(pipe: Any, target: deque[str]) -> None:
        if pipe is None:
            return
        for line in pipe:
            target.append(line.rstrip())

    threading.Thread(target=_drain, args=(proc.stdout, stdout_tail), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr, stderr_tail), daemon=True).start()

    hook_installed = False
    hook_branch_valid = False
    code_cave_written = False
    mailbox_initialized = False
    turn_results: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    clean_uninstall = False
    mailbox_cleared = False

    try:
        # Phase 1: Wait for process discovery, attach, ARM hook + code cave + mailbox init
        deadline = time.perf_counter() + startup_timeout_sec
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                break
            insn = server.read_hook_insn()
            mb = server.read_mailbox()
            cave = server.read_code_cave()
            if insn != ORIG_HOOK_INSN and mb["magic"] == MAILBOX_MAGIC:
                hook_installed = True
                hook_branch_valid = _is_valid_arm_branch_to_cave(insn)
                code_cave_written = any(b != 0 for b in cave)
                mailbox_initialized = True
                break
            time.sleep(0.02)

        # Phase 2: Drive multi-turn synthetic encrypted-PK7 battle requests through the mailbox
        turns = _build_bridge_test_turns()
        if hook_installed and proc.poll() is None:
            for idx, spec in enumerate(turns):
                ai_slots = [make_pk7_slot(ec=0x10000100 + idx * 17 + i, **m) for i, m in enumerate(spec["ai_party"])]
                pl_slots = [make_pk7_slot(ec=0x20000100 + idx * 17 + i, **m) for i, m in enumerate(spec["player_party"])]
                server.load_parties(
                    ai_slots=ai_slots,
                    player_slots=pl_slots,
                    turn=spec["turn"],
                    ai_active_slot=spec["ai_active_slot"],
                    player_active_slot=spec["player_active_slot"],
                    can_z_mask=spec["can_z_mask"],
                )
                t0 = time.perf_counter()
                server.post_turn_request()

                responded = False
                t_deadline = t0 + turn_timeout_sec
                while time.perf_counter() < t_deadline:
                    if proc.poll() is not None:
                        break
                    mb = server.read_mailbox()
                    if mb["resp_ready"] == 1:
                        responded = True
                        break
                    time.sleep(0.005)
                lat_ms = (time.perf_counter() - t0) * 1000.0

                if not responded:
                    turn_results.append(
                        {
                            "id": spec["id"],
                            "ok": False,
                            "responded": False,
                            "latency_ms": round(lat_ms, 2),
                            "reason": "timeout waiting for mailbox resp_ready=1",
                        }
                    )
                    continue

                latencies_ms.append(lat_ms)
                act = server.read_battle_ai_action()
                legal_ok, detail = _validate_written_action(act, spec)
                turn_results.append(
                    {
                        "id": spec["id"],
                        "ok": legal_ok,
                        "responded": True,
                        "latency_ms": round(lat_ms, 2),
                        "action": act,
                        "reason": detail,
                    }
                )
        else:
            for spec in turns:
                turn_results.append(
                    {
                        "id": spec["id"],
                        "ok": False,
                        "responded": False,
                        "latency_ms": 0.0,
                        "reason": "bridge did not install hook & initialize mailbox before startup timeout",
                    }
                )

        # Phase 3: Send SIGTERM and verify clean hook uninstall (restores ORIG_HOOK_INSN & clears magic)
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=teardown_timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass

        if hook_installed:
            clean_uninstall = server.read_hook_insn() == ORIG_HOOK_INSN
            mailbox_cleared = server.read_mailbox()["magic"] == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        server.stop()

    turns_total = len(turn_results)
    turns_passed = sum(1 for r in turn_results if r["ok"])
    illegal_writes = server.illegal_writes()
    memory_safe = len(illegal_writes) == 0 and server.unmapped_memory_errors == 0
    discovered_and_attached = server.process_list_calls >= 1 and server.set_process_calls >= 1 and server.active_pid != 0

    lat_sorted = sorted(latencies_ms)
    p50_ms = round(statistics.median(lat_sorted), 2) if lat_sorted else 0.0
    p95_ms = round(lat_sorted[min(len(lat_sorted) - 1, int(round(0.95 * (len(lat_sorted) - 1))))], 2) if lat_sorted else 0.0

    # Composite bridge score (0..100)
    score = 0.0
    if discovered_and_attached:
        score += 15.0
    if hook_installed and hook_branch_valid and code_cave_written and mailbox_initialized:
        score += 20.0
    score += 45.0 * (turns_passed / max(1, turns_total))
    if memory_safe and hook_installed:
        score += 10.0
    if clean_uninstall and mailbox_cleared:
        score += 10.0
    if not memory_safe:
        score = min(score, 25.0)  # severe penalty for corrupting guest RAM outside hook/mailbox/BattleAi

    bridge_pass = (
        discovered_and_attached
        and hook_installed
        and hook_branch_valid
        and code_cave_written
        and mailbox_initialized
        and turns_passed == turns_total
        and memory_safe
        and clean_uninstall
        and mailbox_cleared
    )

    suite_key = f"azahar-rpc-v2-usum-v1.0 | chunk={max_read_chunk}r/{max_write_chunk}w | {turns_total}turns"
    summary = {
        "suite_key": suite_key,
        "bridge_pass": bridge_pass,
        "bridge_score": round(score, 1),
        "discovered_and_attached": discovered_and_attached,
        "hook_installed": hook_installed,
        "hook_branch_valid": hook_branch_valid,
        "code_cave_written": code_cave_written,
        "mailbox_initialized": mailbox_initialized,
        "turns_passed": turns_passed,
        "turns_total": turns_total,
        "memory_safe": memory_safe,
        "illegal_write_count": len(illegal_writes),
        "oversized_chunk_errors": server.oversized_chunk_errors,
        "clean_uninstall": clean_uninstall,
        "mailbox_cleared": mailbox_cleared,
        "latency_p50_ms": p50_ms,
        "latency_p95_ms": p95_ms,
        "rpc_reads": server.read_calls,
        "rpc_writes": server.write_calls,
    }

    safe_model = model_name.replace("/", "_").replace(":", "_").replace(" ", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nonce = hashlib.sha256(f"{time.time_ns()}-{safe_model}".encode("utf-8")).hexdigest()[:6]
    rid = run_id or f"{track}_{safe_model}_{ts}_{nonce}"
    return Scorecard(
        run_id=rid,
        track=track,
        model=model_name,
        mode="azahar-mock-udp-v2",
        summary=summary,
        details={
            "turns": turn_results,
            "illegal_writes": [{"addr": f"0x{w.addr:08X}", "size": w.size} for w in illegal_writes[:20]],
            "stdout_tail": list(stdout_tail),
            "stderr_tail": list(stderr_tail),
        },
    )
