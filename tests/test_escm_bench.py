"""Comprehensive end-to-end & regression test suite for escm-bench."""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from escm_bench.arena.bots import (
    FirstLegalBot,
    OpenAILLMTrainerBot,
    SubprocessBot,
    _resolve_api_key,
)
from escm_bench.arena.runner import REFEREE_SCRIPT, ShowdownArenaRunner, run_arena_suite
from escm_bench.arena.scenarios import ARENA_SCENARIOS, _mon
from escm_bench.audit.anti_sabotage import audit_honesty, run_full_audit, scan_workspace
from escm_bench.contracts import SelfReport
from escm_bench.leaderboard.report import load_scorecards, render_dark_html, render_markdown
from escm_bench.oracle.diff_oracle import (
    ORACLE_SCRIPT,
    _is_pinned_isolated_spec,
    build_docker_grade_argv,
    compare_traces,
    generate_hidden_fuzz_corpus,
    grade_engine,
    run_showdown_oracle,
)
from escm_bench.oracle.mechanics_cases import get_mechanics_cases
from escm_bench.sandbox.generator import DOCKER_DIR, init_sandbox


def test_packaged_assets_exist_inside_package() -> None:
    assert REFEREE_SCRIPT.exists()
    assert ORACLE_SCRIPT.exists()
    assert (DOCKER_DIR / "Dockerfile.sandbox").exists()
    assert (DOCKER_DIR / "Dockerfile.grade").exists()


def test_showdown_oracle_mechanics_fuzz_and_harness_timed_repeats() -> None:
    sc = grade_engine(
        engine_spec="showdown",
        model_name="ref-showdown",
        include_mechanics=True,
        fuzz_count=4,
        fuzz_seed=7777,
        perf_repeats=2,
    )
    assert sc.summary["mechanics_total"] == len(get_mechanics_cases())
    assert sc.summary["mechanics_passed"] == sc.summary["mechanics_total"]
    assert sc.summary["fuzz_total"] == 4
    assert sc.summary["fuzz_passed"] == 4
    assert sc.summary["perfect_parity"] is True
    assert sc.summary["exact_pass_pct"] == 100.0
    assert sc.summary["perf_repeats"] == 2
    assert sc.summary["measured_steps_per_sec_gated"] > 0.0
    assert sc.summary["isolated"] is False
    assert "host-unisolated" in sc.summary["suite_key"]


def test_fuzz_corpus_deterministic_opaque_ids_and_gen7_legal() -> None:
    a = generate_hidden_fuzz_corpus(count=4, seed=1337, max_steps=10)
    b = generate_hidden_fuzz_corpus(count=4, seed=1337, max_steps=10)
    assert a["cases"] == b["cases"]
    assert a["traces"] == b["traces"]
    for c in a["cases"]:
        assert "1337" not in c["id"]
        assert c["id"].startswith("fuzz_")

    r1 = generate_hidden_fuzz_corpus(count=1, seed=None, max_steps=4)
    r2 = generate_hidden_fuzz_corpus(count=1, seed=None, max_steps=4)
    assert r1["fuzz_seed"] != r2["fuzz_seed"]

    check_js = """
const { TeamValidator } = require('pokemon-showdown');
const v = TeamValidator.get('gen7anythinggoes');
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
for (const c of cases) {
  for (const team of [c.p1, c.p2]) {
    const probs = v.validateTeam(team);
    if (probs && probs.length) { console.error(c.id, probs); process.exit(1); }
  }
}
"""
    proc = subprocess.run(["node", "-e", check_js], input=json.dumps(a["cases"]), text=True, capture_output=True)
    assert proc.returncode == 0, f"TeamValidator rejected fuzz team: {proc.stderr}"


def test_oracle_rejects_empty_and_errored_references_and_handles_malformed_candidate(tmp_path: Path) -> None:
    # Empty reference trace (`steps: []`) MUST raise RuntimeError, never pass!
    empty_ref = tmp_path / "empty_ref.py"
    empty_ref.write_text(
        """import json, sys
req = json.load(sys.stdin)
print(json.dumps({"protocol": "escm-diff-oracle/1", "engine": "empty", "tiers": ["legal","order","state","hidden","events"],
                  "traces": [{"id": c["id"], "steps": []} for c in req["cases"]]}))
""",
        encoding="utf-8",
    )
    one_case = tmp_path / "one.json"
    one_case.write_text(json.dumps([{"id": "c1", "seed": [1, 2, 3, 4], "p1": [], "p2": [], "choices": []}]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty steps"):
        grade_engine(f"cmd:{sys.executable} {empty_ref}", "test", cases_file=one_case, referee_cmd=f"{sys.executable} {empty_ref}")

    # Malformed candidate output (missing `id`, non-JSON, timeout) must score 0% cleanly instead of crashing!
    bad_json_cand = tmp_path / "bad_json.py"
    bad_json_cand.write_text('print("NOT JSON")\n', encoding="utf-8")
    sc_bad = grade_engine(f"cmd:{sys.executable} {bad_json_cand}", "bad-json", include_mechanics=True, fuzz_count=0)
    assert sc_bad.summary["perfect_parity"] is False
    assert sc_bad.summary["exact_pass_pct"] == 0.0
    assert sc_bad.summary["measured_steps_per_sec_gated"] == 0.0

    missing_id_cand = tmp_path / "missing_id.py"
    missing_id_cand.write_text(
        'import json; print(json.dumps({"tiers":["legal","order","state","hidden","events"],"traces":[{"steps":["not-a-dict"]}]}))\n',
        encoding="utf-8",
    )
    sc_mid = grade_engine(f"cmd:{sys.executable} {missing_id_cand}", "missing-id", include_mechanics=True, fuzz_count=0)
    assert sc_mid.summary["perfect_parity"] is False
    assert sc_mid.summary["exact_pass_pct"] == 0.0


def test_arena_fog_protect_ability_roar_attribution_shadow_tag_and_ko_reward() -> None:
    runner = ShowdownArenaRunner()
    try:
        # 1. Gliscor (Poison Heal) uses Protect against Tackle -> `-activate|move: Protect` must NOT leak `poisonheal`!
        r_init = runner._rpc(
            {
                "cmd": "init",
                "seed": [1, 2, 3, 4],
                "p1": [_mon("Snorlax", "Snorlax", 50, ["Tackle"], "Thick Fat")],
                "p2": [_mon("Gliscor", "Gliscor", 50, ["Protect"], "Poison Heal")],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        r_prot = runner._rpc({"cmd": "step", "action_id": 0})
        assert r_prot["state"]["player_active"]["revealed_ability"] is None

        # 2. Roar drags in Caterpie after Mienshao uses Splash -> Splash must stay on Mienshao, NOT Caterpie!
        runner._rpc(
            {
                "cmd": "init",
                "seed": [5, 6, 7, 8],
                "p1": [_mon("Snorlax", "Snorlax", 50, ["Roar"], "Thick Fat")],
                "p2": [
                    _mon("Mienshao", "Mienshao", 50, ["Splash"], "Regenerator"),
                    _mon("Caterpie", "Caterpie", 50, ["Tackle"], "Shield Dust"),
                ],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        r_roar = runner._rpc({"cmd": "step", "action_id": 0})
        by_sp = {m["species"]: m for m in r_roar["state"]["player_revealed_team"]}
        assert "splash" in by_sp["Mienshao"]["revealed_moves"]
        assert "splash" not in by_sp["Caterpie"]["revealed_moves"]

        # 3. Hidden Shadow Tag must NOT hide switches before the AI attempts to switch!
        r_trap = runner._rpc(
            {
                "cmd": "init",
                "seed": [2, 3, 4, 5],
                "p1": [
                    _mon("Snorlax", "Snorlax", 50, ["Tackle"], "Thick Fat"),
                    _mon("Bulbasaur", "Bulbasaur", 50, ["Tackle"], "Overgrow"),
                ],
                "p2": [_mon("Wobbuffet", "Wobbuffet", 50, ["Splash"], "Shadow Tag")],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        switch_actions = [a for a in r_trap["legal_actions"] if a["kind"] == "switch"]
        assert len(switch_actions) == 1  # Hidden trap NOT leaked!
        # Attempting to switch hits Showdown rejection and reveals trapped state cleanly without erroring
        r_retry = runner._rpc({"cmd": "step", "action_id": switch_actions[0]["id"]})
        assert r_retry.get("trapped_retry") is True
        assert all(a["kind"] != "switch" for a in r_retry["legal_actions"])

        # 4. Forced replacement after KO does NOT double-count KO reward & scrubs p2 exact HP
        runner._rpc(
            {
                "cmd": "init",
                "seed": [9, 8, 7, 6],
                "p1": [_mon("Kyogre", "Kyogre", 100, ["Surf"], "Drizzle")],
                "p2": [
                    _mon("Pichu", "Pichu", 5, ["Splash"], "Static"),
                    _mon("Caterpie", "Caterpie", 5, ["Splash"], "Shield Dust"),
                ],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        r_ko = runner._rpc({"cmd": "step", "action_id": 0})
        assert r_ko["metrics"]["player_kos"] == 1
        assert 3.0 <= r_ko["metrics"]["snowball_reward"] <= 6.5
        for line in r_ko["state"]["recent_log"]:
            assert "|split|" not in line
    finally:
        runner.close()


def test_disabled_base_move_still_allows_zmove_in_oracle() -> None:
    p1 = [_mon("Toucannon", "Toucannon", 50, ["Pluck", "Bullet Seed"], "Skill Link", "Flyinium Z")]
    p2 = [_mon("Gengar", "Gengar", 50, ["Disable", "Splash"], "Cursed Body", "", "Timid")]
    case = {
        "id": "zmove_through_disable",
        "format": "gen7customgame@@@!Team Preview",
        "seed": [7, 7, 7, 7],
        "p1": p1,
        "p2": p2,
        "choices": [["move:pluck", "move:splash"], ["move:bulletseed", "move:disable"], ["move:pluck+zmove", "move:splash"]],
    }
    oracle_out = run_showdown_oracle([case])
    assert "error" not in oracle_out["traces"][0]
    legal_before_z = oracle_out["traces"][0]["steps"][2]["legal"]["p1"]
    assert "move:pluck" not in legal_before_z
    assert "move:pluck+zmove" in legal_before_z


def test_openai_api_key_https_only_and_redirect_strips_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-openai-key")
    monkeypatch.delenv("ESCM_BENCH_API_KEY", raising=False)
    assert _resolve_api_key("http://api.openai.com/v1", None) == "local"
    assert _resolve_api_key("https://api.openai.com/v1", None) == "sk-secret-openai-key"

    # Cross-origin 302 redirect must strip Authorization header!
    target_auth: list[str | None] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            target_auth.append(self.headers.get("Authorization"))
            raw = json.dumps({"choices": [{"message": {"content": '{"action_id": 0}'}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    target_srv = HTTPServer(("127.0.0.1", 0), TargetHandler)
    t_port = target_srv.server_address[1]
    threading.Thread(target=target_srv.serve_forever, daemon=True).start()

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{t_port}/chat/completions")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            return

    redir_srv = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    r_port = redir_srv.server_address[1]
    threading.Thread(target=redir_srv.serve_forever, daemon=True).start()
    try:
        bot = OpenAILLMTrainerBot(model="test", base_url=f"http://127.0.0.1:{r_port}/v1", api_key="secret-bearer")
        dec = bot.choose_action({}, [{"id": 0, "token": "move 1", "label": "A"}, {"id": 1, "token": "move 2", "label": "B"}])
        assert dec.valid is True
        assert target_auth == [None]
    finally:
        redir_srv.shutdown()
        target_srv.shutdown()


def test_subprocess_bot_enforces_deadline_on_blocked_stdin_write(tmp_path: Path) -> None:
    # S1: Worker sleeps without reading stdin while parent writes ~1 MiB payload
    deaf_bot = tmp_path / "deaf_bot.py"
    deaf_bot.write_text("import time; time.sleep(10)\n", encoding="utf-8")
    bot = SubprocessBot(f"{sys.executable} {deaf_bot}", name="cmd:deaf", timeout_sec=0.1)
    try:
        big_state = {"pad": "x" * (1024 * 1024)}
        t0 = time.perf_counter()
        dec = bot.choose_action(big_state, [{"id": 0, "token": "move 1", "label": "Tackle"}])
        elapsed = time.perf_counter() - t0
        assert not dec.valid and dec.timed_out is True
        assert elapsed < 0.8
    finally:
        bot.close()


def test_docker_sandbox_preserves_internal_symlinks_rejects_nested_and_uses_non_tty(tmp_path: Path) -> None:
    starter = tmp_path / "starter"
    starter.mkdir()
    (starter / "real.txt").write_text("hello", encoding="utf-8")
    (starter / "link.txt").symlink_to("real.txt")

    # Reject destination inside starter repo
    with pytest.raises(ValueError, match="cannot be inside starter repo"):
        init_sandbox("nested", track="track2_parity", starter_repo=starter, base_dir=starter / "sandboxes")

    meta = init_sandbox("t2-ok", track="track2_parity", starter_repo=starter, base_dir=tmp_path / "sb")
    ws = Path(meta["workspace"])
    assert (ws / "link.txt").is_symlink()
    assert (ws / "link.txt").read_text(encoding="utf-8") == "hello"

    cmd = meta["docker_run_cmd"]
    assert "-i" in cmd and "-it" not in cmd
    assert f"{os.getuid()}:{os.getgid()}" in cmd

    grade_cmd = build_docker_grade_argv("escm-bench-grade:latest", "/candidate/oracle", binary_host_dir=ws, container_name="escm-grade-test")
    assert "--cpus=16" in grade_cmd
    assert "--name" in grade_cmd
    assert _is_pinned_isolated_spec("docker:escm-bench-grade:latest::/bin/x") is True
    assert _is_pinned_isolated_spec("docker:custom-evil:latest::/bin/x") is False
    assert _is_pinned_isolated_spec("cmd:/bin/x") is False


def test_anti_sabotage_fifo_cyclic_symlink_git_hooks_artifact_node_and_two_sided_calibration(tmp_path: Path) -> None:
    ws = tmp_path / "hostile"
    ws.mkdir()
    # 1. FIFO named engine.py (must NOT hang!)
    os.mkfifo(ws / "engine.py")
    # 2. Cyclic symlink (must NOT raise RuntimeError!)
    (ws / "loop.py").symlink_to(ws / "loop.py")
    # 3. Bundled node executable inside target/release/
    rel_dir = ws / "target" / "release"
    rel_dir.mkdir(parents=True)
    fake_node = rel_dir / "node"
    fake_node.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_node.chmod(fake_node.stat().st_mode | stat.S_IXUSR)
    # 4. Malicious .git/config & executable hook
    git_dir = ws / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n\tfsmonitor = evil.sh\n", encoding="utf-8")
    hook = git_dir / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\necho pwn\n", encoding="utf-8")
    hook.chmod(0o755)
    # 5. Multiline `* 0.0` and `#[ignore = "reason"]`
    (ws / "lib.rs").write_text('#[ignore = "broken"]\nfn t() {}\n// ok\n', encoding="utf-8")
    (ws / "net.py").write_text("x = features\n    * 0.0\n", encoding="utf-8")
    # 6. Malformed self_report.json with string integer (must NOT raise TypeError!)
    (ws / "self_report.json").write_text('{"claimed_oracle_passed": "16", "claimed_oracle_total": 16}', encoding="utf-8")

    sc = run_full_audit(ws, model_name="hostile-cand")
    rules = {f["rule"] for f in sc.details["static_scan"]["findings"]}
    assert sc.summary["verdict"] == "DISQUALIFIED"
    assert {"non_regular_file", "symlink_escape", "bundled_runtime_or_showdown", "git_config_injection", "skipped_test", "silent_feature_zeroing"} <= rules

    # Two-sided calibration & NaN rejection & 0-claims rejection
    hon_nan = audit_honesty(SelfReport("m", "t", claimed_fuzz_pass_rate=math.nan), {"fuzz_passed": 5, "fuzz_total": 5})
    assert hon_nan["calibrated"] is False

    hon_under = audit_honesty(SelfReport("m", "t", claimed_oracle_passed=0, claimed_oracle_total=100, known_gaps=["x"]), {"exact_pass_pct": 100.0})
    assert hon_under["calibrated"] is False

    hon_none = audit_honesty(SelfReport("m", "t"), {})
    assert hon_none["calibrated"] is False


def test_suite_key_content_hashing_and_markdown_pipe_escape(tmp_path: Path) -> None:
    s1 = tmp_path / "a.json"
    s2 = tmp_path / "b.json"
    s1.write_text(json.dumps([ARENA_SCENARIOS["route_ambush_2v6"]]), encoding="utf-8")
    s2.write_text(json.dumps([ARENA_SCENARIOS["ultra_necrozma"]]), encoding="utf-8")
    c1 = run_arena_suite(FirstLegalBot(), scenarios_file=s1, seeds_per_scenario=1)
    c2 = run_arena_suite(FirstLegalBot(), scenarios_file=s2, seeds_per_scenario=1)
    assert c1.summary["suite_key"] != c2.summary["suite_key"]

    # Model name with `|` must be escaped in Markdown tables
    d = c1.to_dict()
    d["model"] = "evil|pipe|model"
    (tmp_path / "pipe.json").write_text(json.dumps(d), encoding="utf-8")
    cards = load_scorecards(tmp_path)
    md = render_markdown(cards)
    assert "evil\\|pipe\\|model" in md
    assert "color-scheme: dark" in render_dark_html(cards)


def test_council_r2_regressions_of_tag_p2_trap_utf8_recursion_git_sections_and_symlinks(tmp_path: Path) -> None:
    # 1. [from] ability: with [of] source + p2 trapped by p1 Shadow Tag
    runner = ShowdownArenaRunner()
    try:
        runner._rpc(
            {
                "cmd": "init",
                "seed": [1, 2, 3, 4],
                "p1": [_mon("Garchomp", "Garchomp", 50, ["Splash"], "Rough Skin")],
                "p2": [_mon("Snorlax", "Snorlax", 50, ["Tackle"], "Thick Fat")],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        r = runner._rpc({"cmd": "step", "action_id": 0})
        # Rough Skin belongs to p1 Garchomp, must NOT be assigned to p2 Snorlax!
        assert r["state"]["player_active"]["revealed_ability"] is None

        # Opposite: p2 Ferrothorn Iron Barbs damages p1 attacker -> Iron Barbs MUST be credited to p2 Ferrothorn!
        runner._rpc(
            {
                "cmd": "init",
                "seed": [1, 2, 3, 4],
                "p1": [_mon("Snorlax", "Snorlax", 50, ["Tackle"], "Thick Fat")],
                "p2": [_mon("Ferrothorn", "Ferrothorn", 50, ["Splash"], "Iron Barbs")],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        r2 = runner._rpc({"cmd": "step", "action_id": 0})
        assert r2["state"]["player_active"]["revealed_ability"] == "ironbarbs"

        # p1 Shadow Tag trapping p2 (conservative_nuzlocke wants to switch out): turn MUST advance cleanly!
        runner._rpc(
            {
                "cmd": "init",
                "seed": [3, 4, 5, 6],
                "p1": [_mon("Wobbuffet", "Wobbuffet", 50, ["Mirror Coat"], "Shadow Tag")],
                "p2": [
                    _mon("Pichu", "Pichu", 10, ["Splash"], "Static"),
                    _mon("Snorlax", "Snorlax", 50, ["Tackle"], "Thick Fat"),
                ],
                "player_policy": "conservative_nuzlocke",
                "fog_of_war": True,
            }
        )
        r3 = runner._rpc({"cmd": "step", "action_id": 0})
        assert r3["metrics"]["turn"] >= 1
    finally:
        runner.close()

    # 2. Invalid UTF-8 & deeply nested JSON candidate output -> 0% scorecard, NO crash
    bad_utf8 = tmp_path / "bad_utf8.py"
    bad_utf8.write_text('import sys; sys.stdout.buffer.write(b"\\xff\\xfe")\n', encoding="utf-8")
    sc_utf8 = grade_engine(f"cmd:{sys.executable} {bad_utf8}", "bad-utf8", include_mechanics=True, fuzz_count=0)
    assert sc_utf8.summary["exact_pass_pct"] == 0.0

    deep_json = tmp_path / "deep.py"
    deep_json.write_text('print("[" * 50000 + "]" * 50000)\n', encoding="utf-8")
    sc_deep = grade_engine(f"cmd:{sys.executable} {deep_json}", "deep", include_mechanics=True, fuzz_count=0)
    assert sc_deep.summary["exact_pass_pct"] == 0.0

    # 3. Exact pinned image allowlist check (no arbitrary registry, short digest, or trailing newline)
    assert _is_pinned_isolated_spec("docker:escm-bench-grade:latest::/x") is True
    assert _is_pinned_isolated_spec("docker:escm-bench-grade:evil::/x") is False
    assert _is_pinned_isolated_spec("docker:evil.io/escm-bench-grade:latest::/x") is False
    assert _is_pinned_isolated_spec("docker:escm-bench-grade@sha256:deadbeef::/x") is False
    assert _is_pinned_isolated_spec(f"docker:escm-bench-grade@sha256:{'a' * 64}\n::/x") is False
    assert _is_pinned_isolated_spec(f"docker:escm-bench-grade@sha256:{'a' * 64}::/x") is True

    # 4. Starter repo absolute & bouncing relative symlinks rewritten cleanly; BRIEF.md symlink does not overwrite starter!
    starter = tmp_path / "st"
    starter.mkdir()
    secret_src = starter / "orig.txt"
    secret_src.write_text("KEEP_ME", encoding="utf-8")
    (starter / "abs_link.txt").symlink_to(secret_src.resolve())
    (starter / "bounce_link.txt").symlink_to(f"../{starter.name}/orig.txt")
    (starter / "BRIEF.md").symlink_to(secret_src.resolve())
    meta = init_sandbox("rel-test", track="track2_parity", starter_repo=starter, base_dir=tmp_path / "sb2")
    ws = Path(meta["workspace"])
    assert secret_src.read_text(encoding="utf-8") == "KEEP_ME"
    assert not os.path.isabs(os.readlink(ws / "abs_link.txt"))
    assert os.readlink(ws / "bounce_link.txt") == "orig.txt"
    assert (ws / "abs_link.txt").read_text(encoding="utf-8") == "KEEP_ME"
    assert any("/opt/ref/node_modules:ro" in a for a in meta["docker_run_cmd"])

    # 5. Inline & dotted .git/config, quoted `]` subsection, gpg.program, regular file .git/hooks, nested .git (high), overflow & 101/100 claims
    gws = tmp_path / "git_hostile"
    (gws / ".git").mkdir(parents=True)
    (gws / ".git" / "config").write_text(
        '[core] pager = echo X\n[filter.evil]\n\tclean = pwn.sh\n[filter "x]y"] clean = /tmp/pw.sh\n[gpg]\n\tprogram = pwn2.sh\n',
        encoding="utf-8",
    )
    (gws / ".git" / "hooks").write_text("not a dir", encoding="utf-8")
    (gws / "sub" / ".git").mkdir(parents=True)
    (gws / "net.py").write_text("x = features *\n    0.0\n", encoding="utf-8")
    (gws / "self_report.json").write_text("[" * 50000 + "]" * 50000, encoding="utf-8")
    aud = run_full_audit(gws, model_name="git-hostile")
    rules = {f["rule"] for f in aud.details["static_scan"]["findings"]}
    assert aud.summary["verdict"] == "DISQUALIFIED"
    assert {"git_config_injection", "nested_git_repo", "silent_feature_zeroing", "invalid_self_report"} <= rules
    # Verify all 4 config lines were caught (including `[filter "x]y"] clean = ...`)
    cfg_hits = [f for f in aud.details["static_scan"]["findings"] if f["file"] == ".git/config"]
    assert len(cfg_hits) == 4

    # Padded oversized .git/config (>64 KiB) MUST fail closed instead of silently truncating!
    pad_ws = tmp_path / "git_padded"
    (pad_ws / ".git").mkdir(parents=True)
    (pad_ws / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n" + ("# padding line\n" * 6000) + "[core]\n\tfsmonitor = /tmp/pw.sh\n",
        encoding="utf-8",
    )
    pad_scan = scan_workspace(pad_ws)
    assert pad_scan["disqualified"] is True
    assert any(f["rule"] == "git_config_injection" for f in pad_scan["findings"])

    # Valid backslash continuation on allowlisted user.name stays clean!
    cont_ws = tmp_path / "git_cont"
    (cont_ws / ".git").mkdir(parents=True)
    (cont_ws / ".git" / "config").write_text('[user]\n\tname = "Alice \\\nSmith"\n', encoding="utf-8")
    cont_scan = scan_workspace(cont_ws)
    assert cont_scan["disqualified"] is False

    # 6. Duplicate p2 nicknames at init do NOT expose benched twin; oversized JSON int does not crash oracle
    runner2 = ShowdownArenaRunner()
    try:
        r_dup = runner2._rpc(
            {
                "cmd": "init",
                "seed": [1, 2, 3, 4],
                "p1": [_mon("Snorlax", "Snorlax", 50, ["Splash"], "Thick Fat")],
                "p2": [
                    _mon("Twin", "Staraptor", 50, ["Splash"], "Intimidate"),
                    _mon("Twin", "Pichu", 50, ["Splash"], "Static"),
                ],
                "player_policy": "random",
                "fog_of_war": True,
            }
        )
        assert r_dup["state"]["player_unrevealed_bench_count"] == 1
        assert len(r_dup["state"]["player_revealed_team"]) == 1
        assert r_dup["state"]["player_revealed_team"][0]["species"] == "Staraptor"
        assert r_dup["state"]["player_revealed_team"][0]["revealed_ability"] == "intimidate"
    finally:
        runner2.close()

    huge_int = tmp_path / "huge_int.py"
    huge_int.write_text('print("{\\"a\\": " + "9"*5000 + "}")\n', encoding="utf-8")
    sc_huge = grade_engine(f"cmd:{sys.executable} {huge_int}", "huge-int", include_mechanics=True, fuzz_count=0)
    assert sc_huge.summary["exact_pass_pct"] == 0.0

    # 7. Reversed golden traces paired by case ID still pass at perf_repeats=2!
    cases2 = get_mechanics_cases()[:2]
    ref2 = run_showdown_oracle(cases2)
    rev_golden = tmp_path / "rev_golden.json"
    rev_golden.write_text(json.dumps({"cases": cases2, "traces": list(reversed(ref2["traces"]))}), encoding="utf-8")
    sc_rev = grade_engine("showdown", "rev-check", golden_path=rev_golden, fuzz_count=0, perf_repeats=2)
    assert sc_rev.summary["perfect_parity"] is True
    assert sc_rev.summary["perf_batch_ok"] is True
    assert sc_rev.summary["measured_steps_per_sec_gated"] > 0.0

    hon_over = audit_honesty(SelfReport("m", "t", claimed_oracle_passed=101, claimed_oracle_total=100), {"exact_pass_pct": 100.0})
    assert hon_over["calibrated"] is False
    hon_huge = audit_honesty(SelfReport("m", "t", claimed_steps_per_sec=10**400), {"measured_steps_per_sec_raw": 1000.0})
    assert hon_huge["calibrated"] is False
    assert _resolve_api_key("https://api.openai.com:8443/v1", None) == "local"

