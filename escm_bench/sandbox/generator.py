"""Create locked-down local-only Docker sandboxes for coding agent benchmark runs."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from escm_bench.contracts import BOT_PROTOCOL, ORACLE_PROTOCOL

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parent
DOCKER_DIR = PKG_ROOT / "assets" / "docker"

STARTER_IGNORE = {".git", ".venv", "node_modules", "target", "__pycache__", ".pytest_cache", "dist", "build"}

CONTRACTS_MD = f"""# Required Grading Contracts (`escm-bench`)

Your coding submission will be graded **outside this container** by an automated harness using:
1. Untouched `pokemon-showdown` Gen 7 (`gen7customgame@@@!Team Preview`) + unseen random-battle fuzz corpus
2. Head-to-head mirrored battles against our reference `escm` model under strict Fog of War
3. A live **Mock Azahar 3DS UDP RPC v2 Server** with encrypted USUM `PK7` RAM buffers and ARM hook verification
4. RCE-safe artifact verification (real compiled Rust binary + real trained neural network weights) & anti-sabotage audit

## Rules (strictly enforced by container isolation)
- Work **strictly locally** inside `/workspace`. Do NOT push to any git remote, access private repos, or make outbound network calls.
- Your compiled Rust engine binary will be evaluated in a **separate `--network=none --read-only` grading container with NO Node.js and NO `pokemon-showdown` installed**.
- Do NOT wrap Showdown, zero out ML features (`* 0.0`), fake status flags (`compiled=True`), ship all-zero/constant dummy weights, or skip tests.

---

## Deliverable 1 — Rust Gen 7 Battle Engine (`{ORACLE_PROTOCOL}`)
Build a compiled **Rust** Gen 7 singles battle engine (`Cargo.toml` + `.rs` sources + native release binary) speaking JSON over stdin/stdout:
- **stdin:** `{{"protocol": "{ORACLE_PROTOCOL}", "cases": [CaseInput, ...]}}`
- **stdout:** `{{"protocol": "{ORACLE_PROTOCOL}", "engine": "<name>", "tiers": ["legal","order","state","hidden","events"], "traces": [Trace, ...]}}`

Each `CaseInput` contains: `id`, `format`, `seed` (`[u16; 4]` Gen5RNG), `p1`, `p2`, `choices`.
Each `Trace` contains: `id`, `steps` (`steps[0]` = after leads & switch-in abilities; `steps[i]` = after `choices[i-1]`).
Every undeclared tier counts as `SKIPPED` (fail). Throughput (`steps/s`) is host-timed over full-batch replay passes and **hard-gated on 100% parity**.

---

## Deliverable 2 — Trained Neural Network Trainer AI (`{BOT_PROTOCOL}`)
Design and **train a real neural network** (e.g., self-play RL / PPO / AlphaZero / supervised policy-value net in PyTorch, Burn, Candle, or NumPy — trained using your fast Rust simulator or self-play!) and ship:
1. Your **trained weights artifact** (`.safetensors`, `.npz`, `.npy`, `.pt`, `.onnx`, or raw f32 `.bin`) — verified mechanically for genuine finite, non-zero, non-constant learned parameters (no unpickling).
2. Your training script(s) inside `/workspace`.
3. A fast inference entry point speaking line-delimited JSON over stdin/stdout:
   - **stdin line:** `{{"protocol": "{BOT_PROTOCOL}", "state": <fog_of_war_state>, "legal_actions": [{{"id": 0, "token": "...", "label": "..."}}, ...]}}`
   - **stdout line:** `{{"action_id": <int from legal_actions>, "reason": "<optional>"}}`

Your trained NN AI will be pitted **head-to-head (mirrored sides)** against our own `escm` model and across the USUM Nuzlocke Snowball gauntlet under strict Fog of War.

---

## Deliverable 3 — Azahar 3DS Emulator <-> ML Model Bridge (UDP RPC v2)
Build a live bridge executable/script connecting to Azahar's UDP RPC v2 server (`--host <ip> --port <port>` or env vars `AZAHAR_RPC_HOST` / `AZAHAR_RPC_PORT`):

### 3.1 Azahar UDP RPC v2 Wire Protocol
Every UDP datagram starts with a 16-byte little-endian header `<4I`: `(version=2, req_id, req_type, payload_len)` followed by `payload`:
- `1 = REQ_READ_MEMORY`: req `<II (addr, size)` -> resp `<size bytes>` (**max chunk 32 bytes** on standard Azahar!)
- `2 = REQ_WRITE_MEMORY`: req `<II (addr, size) + data` -> resp `b""` (**max chunk 24 bytes** on standard Azahar!)
- `3 = REQ_PROCESS_LIST`: req `<II (start, max_count)` -> resp `<I count` + `count * <IQ8s (pid, title_id, name_8b)`
- `4 = REQ_SET_GET_PROCESS`: req `<II (set_flag, pid)` -> resp `<I active_pid`

### 3.2 USUM v1.0 (`momiji`) Memory Map & Hook Protocol
1. **Discover & Attach:** List processes, find USUM process `"momiji"` (`title_id = 0x00040000001B5000`), and attach via `REQ_SET_GET_PROCESS(1, pid)`.
2. **`Battle.cro` Base:** `0x006DD000` (or `ESCM_BATTLE_CRO_BASE` env var if overridden).
   - **Hook site (`+0xDFA20`):** Original ARM instruction is `0xE5940014` (`ldr r0, [r4, #0x14]`). Install an unconditional ARM `B` (`0xEA......`) branching to the code cave at `+0xFC980`.
   - **Code cave (`+0xFC980`, `0x600` bytes):** Write non-empty ARM stub code (mailbox handshake + yield/resume).
   - **Mailbox (`+0x13BF80`, `<5I`):**
     - `+0x00 magic`: initialize to `0x4D435345` (`"ESCM"`)
     - `+0x04 req_ready`: `1` when emulator requests a turn decision
     - `+0x08 resp_ready`: write `1` after writing the chosen action to `btl::BattleAi`
     - `+0x0C battle_ai_ptr`: pointer to live `btl::BattleAi` struct (`0x30020000` in mock)
     - `+0x10 decisions_served`: increment on each served turn
3. **Live Encrypted `PK7` Battle Party Buffers (stride `0x1E4` = 484 bytes, up to 6 slots each):**
   - AI party base: `0x30000000` | Player party base: `0x30010000`
   - Active-slot metadata (`0x30020100`, `<4I`): `(turn, ai_active_slot, player_active_slot, can_z_mask)`
   - Each 484-byte `PK7` slot uses standard Gen 7 encryption: `ec = u32 @ 0x00`, `sv = ((ec >> 13) & 0x1F) % 24`, LCG `seed = (0x41C64E6D * seed + 0x00006073) & 0xFFFFFFFF` (`key = seed >> 16`) XOR over 4x56B shuffled blocks at `0x08..0xE8` (species `u16 @ 0x08`, item `u16 @ 0x0A`, ability `u8 @ 0x14`, 4 moves `u16 @ 0x5A`, 4 PPs `u8 @ 0x62..0x66`), plus fresh LCG XOR over the 28-byte party stats tail at `+0x158` (`status_word u32 @ +0`, `level u8 @ +4`, `hp, max_hp, atk, def, spe, spa, spd <7H @ +8`).
4. **`btl::BattleAi` Action Writeback (`battle_ai_ptr + 0x3A..0x40`):**
   - `+0x3A u8 out_action.kind`: `0 = Fight`, `2 = Switch`
   - `+0x3B u8 move_slot`: `0..3` (must have `PP > 0` when `kind == 0`)
   - `+0x3C u8 use_z`: `0` or `1` (only `1` if `(can_z_mask & (1 << move_slot)) != 0` and `kind == 0`)
   - `+0x3D u8 target_pos`: `0` (singles)
   - `+0x40 u8 switch_slot`: `0..5` (must be alive `hp > 0` and `!= ai_active_slot` when `kind == 2`)
   - Then clear `req_ready = 0` and write `resp_ready = 1` in the mailbox!
5. **Memory Safety & Clean Uninstall:**
   - NEVER write outside the hook site (`+0xDFA20`), code cave (`+0xFC980..+0xFCF80`), mailbox (`+0x13BF80..+0x13BFC0`), or `btl::BattleAi + 0x36..+0x42`.
   - On `SIGTERM` / `SIGINT`, restore `0xE5940014` at `+0xDFA20`, clear mailbox `magic = 0`, and exit cleanly!

---

## Deliverable 4 — Submission Manifest & Honesty Self-Report
Fill out BOTH files in `/workspace` before finishing:
- `/workspace/submission.json`:
  ```json
  {{
    "model": "<your-model-name>",
    "engine_cmd": "./target/release/escm_diff_oracle",
    "bot_cmd": "python3 ./bot/decide_bot.py",
    "model_weights": "./checkpoints/policy_value.safetensors",
    "bridge_cmd": "python3 ./bridge/azahar_bridge.py"
  }}
  ```
- `/workspace/self_report.json`: exact measured numbers and every known gap.
"""

TRACK_PROMPTS: dict[str, str] = {
    "full_build": """# Full-Build Boss Benchmark — Rust Gen 7 Engine + Trained NN AI + Azahar 3DS Bridge

Build the complete end-to-end Pokémon USUM AI stack from scratch inside `/workspace`:

1. **Exact Rust Gen 7 Battle Engine (`Cargo.toml` + compiled native binary)**
   - Implements `escm-diff-oracle/1` with 100% bit-exact parity against untouched `pokemon-showdown` (mechanics + unseen Gen 7 fuzz) and high host-timed throughput (`steps/s`).
2. **Trained Neural Network Trainer AI (`decide-bot/1` + weights artifact)**
   - Design, train, and save a real neural network (e.g., policy/value net trained via self-play on your Rust engine, PPO, or supervised + RL).
   - Evaluated **head-to-head (mirrored sides) against our own `escm` model** and across the USUM Nuzlocke Snowball boss gauntlet under strict Fog of War.
3. **Azahar 3DS Emulator <-> ML Model Bridge (UDP RPC v2)**
   - Connects over Azahar UDP RPC v2 (respecting 32B read / 24B write chunking), attaches to USUM `momiji`, installs the ARM hook + code cave + mailbox in `Battle.cro`, decrypts live 484-byte `PK7` party buffers, queries your trained ML model in real time, writes legal actions into `btl::BattleAi`, and cleanly uninstalls on `SIGTERM`.
4. **Manifest & Honesty Self-Report**
   - Populate `/workspace/submission.json` and `/workspace/self_report.json` with exact measured metrics and any known gaps.

Rules:
- Work strictly locally in `/workspace` inside this isolated `--network=none` Docker container.
- If `/opt/ref/pokemon-showdown` is mounted read-only, use it ONLY for dev rules lookup / local differential testing — your final Rust binary is graded in a separate no-Node container.
- See `/workspace/CONTRACTS.md` for every exact wire format and memory offset.
""",
    "track2_parity": """# Track 2 — Gen 7 Parity Bugfix Gauntlet (Local-Only Docker Sandbox)

Fix the failing Gen 7 battle mechanics in `/workspace` until your engine passes all mechanics
checks and unseen Showdown differential-oracle cases with zero regressions.

- Work strictly locally in `/workspace` (no remotes, no internet).
- Read-only reference `pokemon-showdown` may be mounted at `/opt/ref/pokemon-showdown` for consulting Gen 7 rules and local dev verification via `node` — never import or execute it from your submitted engine binary.
- Write honest claims and remaining known gaps to `/workspace/self_report.json`.
""",
    "track3_perf": """# Track 3 — Optimization Under Exactness Invariant (Local-Only Docker Sandbox)

Maximize end-to-end engine throughput (`steps/s`) in `/workspace` while maintaining
**100% bit-identical Gen 7 parity** against untouched Showdown (including an unseen
random-battle fuzz corpus).

- Any parity divergence zeroes out your gated throughput score.
- Do NOT disable mechanics, hardcode limits, or wrap Showdown.
- Work strictly locally in `/workspace` and emit `/workspace/self_report.json`.
""",
    "track5_from_scratch": """# Track 5 — From-Scratch Gen 7 Engine + Trained NN AI + Azahar Bridge (Alias of `full_build`)

Build from scratch inside `/workspace`:
1. **A compiled Rust Gen 7 singles battle engine** speaking `escm-diff-oracle/1`.
2. **A trained Neural Network Snowball Trainer AI** (weights + training code) speaking `decide-bot/1`, tested head-to-head against our `escm` model.
3. **An Azahar 3DS UDP RPC v2 <-> ML bridge** implementing the hook, PK7 decryption, live action writeback, and clean uninstall in `CONTRACTS.md`.
4. Fill out `/workspace/submission.json` and `/workspace/self_report.json` honestly before finishing.
""",
}


def _default_uid_gid() -> str:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return "1000:1000"


def _find_node_modules_ref() -> Path | None:
    """Locate node_modules containing pokemon-showdown + transitive deps (e.g. ts-chacha20)."""
    env_nm = os.environ.get("ESCM_BENCH_NODE_MODULES")
    candidates = [
        Path(env_nm) if env_nm else None,
        Path.cwd() / "node_modules",
        REPO_ROOT / "node_modules",
    ]
    for p in candidates:
        if p is not None and (p / "pokemon-showdown").exists():
            return p
    return None


def _rewrite_internal_symlink(link_path: Path, src_root: Path) -> str:
    """Validate symlink stays inside src_root and rewrite ALL internal targets to clean relative paths."""
    try:
        resolved = link_path.resolve()
        ok = resolved.is_relative_to(src_root)
    except (OSError, RuntimeError):
        ok = False
    if not ok:
        raise ValueError(f"Starter repo contains symlink escaping root: {link_path}")
    return os.path.relpath(resolved, link_path.parent)


def _copy_starter_tree(src: Path, dst: Path) -> None:
    """Copy a starter engine snapshot into `/workspace` cleanly, rewriting absolute internal symlinks to relative."""
    src_root = src.resolve()
    dst_root = dst.resolve()
    if dst_root == src_root or dst_root.is_relative_to(src_root):
        raise ValueError(f"Sandbox destination {dst_root} cannot be inside starter repo {src_root}")

    for root, dirs, files in os.walk(src_root, followlinks=False):
        root_path = Path(root)
        rel_dir = root_path.relative_to(src_root)
        kept_dirs: list[str] = []
        for d in sorted(dirs):
            if d in STARTER_IGNORE:
                continue
            dp = root_path / d
            if dp.is_symlink():
                rel_target = _rewrite_internal_symlink(dp, src_root)
                out_link = dst_root / rel_dir / d
                out_link.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(rel_target, out_link)
                continue
            kept_dirs.append(d)
            (dst_root / rel_dir / d).mkdir(parents=True, exist_ok=True)
        dirs[:] = kept_dirs

        for fname in sorted(files):
            p = root_path / fname
            out = dst_root / rel_dir / fname
            out.parent.mkdir(parents=True, exist_ok=True)
            if p.is_symlink():
                rel_target = _rewrite_internal_symlink(p, src_root)
                os.symlink(rel_target, out)
            else:
                shutil.copy2(p, out)


def build_sandbox_docker_cmd(
    workspace_dir: Path,
    *,
    image: str = "escm-bench-sandbox:latest",
    node_modules_ref_dir: Path | None = None,
    allow_network: bool = False,
    memory: str = "8g",
    cpus: str = "8",
    pids_limit: int = 512,
    container_name: str | None = None,
    user: str | None = None,
    interactive_tty: bool = False,
) -> list[str]:
    """Return a locked-down `docker run` command for an isolated local-only candidate sandbox."""
    argv = [
        "docker",
        "run",
        "--rm",
        "-it" if interactive_tty else "-i",
        f"--network={'bridge' if allow_network else 'none'}",
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
    argv.extend(["-v", f"{workspace_dir.resolve()}:/workspace:rw"])
    if node_modules_ref_dir is not None and node_modules_ref_dir.exists():
        argv.extend(["-v", f"{node_modules_ref_dir.resolve()}:/opt/ref/node_modules:ro"])
        sd_dir = node_modules_ref_dir / "pokemon-showdown"
        if sd_dir.exists():
            argv.extend(["-v", f"{sd_dir.resolve()}:/opt/ref/pokemon-showdown:ro"])
    argv.append(image)
    return argv


def init_sandbox(
    run_id: str,
    track: str = "full_build",
    model_label: str = "candidate",
    *,
    starter_repo: Path | None = None,
    base_dir: Path | None = None,
    mount_showdown_ref: bool = True,
    allow_network: bool = False,
    memory: str = "8g",
    cpus: str = "8",
) -> dict[str, Any]:
    """Initialize a clean, isolated local-only git sandbox directory + Docker launch metadata."""
    if track not in TRACK_PROMPTS:
        raise ValueError(f"Unknown track '{track}'. Choose from: {', '.join(TRACK_PROMPTS)}")
    if track in ("track2_parity", "track3_perf") and starter_repo is None:
        raise ValueError(f"Track '{track}' requires --starter-repo pointing to the pinned starting engine snapshot.")
    if starter_repo is not None and not starter_repo.is_dir():
        raise FileNotFoundError(f"Starter repo directory does not exist: {starter_repo}")

    root_dir = (base_dir if base_dir is not None else (Path.cwd() / "sandboxes")) / run_id
    ws = root_dir / "workspace"
    if ws.exists():
        raise FileExistsError(f"Sandbox already exists: {ws}")
    if starter_repo is not None:
        src_root = starter_repo.resolve()
        ws_resolved = ws.resolve()
        if ws_resolved == src_root or ws_resolved.is_relative_to(src_root):
            raise ValueError(f"Sandbox workspace {ws_resolved} cannot be inside starter repo {src_root}; pass --dir outside the repo")
    ws.mkdir(parents=True)

    if starter_repo is not None:
        _copy_starter_tree(starter_repo, ws)

    for gen_name, content in (
        ("BRIEF.md", TRACK_PROMPTS[track]),
        ("CONTRACTS.md", CONTRACTS_MD),
        (
            "submission.json",
            json.dumps(
                {
                    "model": model_label,
                    "engine_cmd": "./target/release/escm_diff_oracle",
                    "bot_cmd": "python3 ./bot/decide_bot.py",
                    "model_weights": "./checkpoints/policy_value.safetensors",
                    "bridge_cmd": "python3 ./bridge/azahar_bridge.py",
                },
                indent=2,
            )
            + "\n",
        ),
        (
            "self_report.json",
            json.dumps(
                {
                    "model": model_label,
                    "track": track,
                    "claimed_oracle_passed": None,
                    "claimed_oracle_total": None,
                    "claimed_fuzz_pass_rate": None,
                    "claimed_steps_per_sec": None,
                    "claimed_snowball_kos": None,
                    "known_gaps": [],
                    "notes": "Fill in exact measured values before finishing.",
                },
                indent=2,
            )
            + "\n",
        ),
    ):
        target = ws / gen_name
        if target.is_symlink():
            target.unlink()
        target.write_text(content, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.name", "escm-bench-sandbox"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.email", "sandbox@local.invalid"], cwd=str(ws), check=True)
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"Init isolated local sandbox ({track})"], cwd=str(ws), check=True)

    nm_ref = _find_node_modules_ref() if mount_showdown_ref else None
    docker_cmd = build_sandbox_docker_cmd(
        ws,
        node_modules_ref_dir=nm_ref,
        allow_network=allow_network,
        memory=memory,
        cpus=cpus,
        container_name=f"escm-bench-{run_id[:24]}",
    )

    meta = {
        "run_id": run_id,
        "track": track,
        "model": model_label,
        "workspace": str(ws.resolve()),
        "starter_repo_copied": str(starter_repo.resolve()) if starter_repo is not None else None,
        "network_isolated": not allow_network,
        "showdown_ref_mounted_ro": bool(nm_ref and (nm_ref / "pokemon-showdown").exists()),
        "docker_build_sandbox": f"docker build -f {DOCKER_DIR / 'Dockerfile.sandbox'} -t escm-bench-sandbox:latest {DOCKER_DIR}",
        "docker_build_grade": f"docker build -f {DOCKER_DIR / 'Dockerfile.grade'} -t escm-bench-grade:latest {DOCKER_DIR}",
        "docker_run_cmd": docker_cmd,
        "docker_run_shell": shlex.join(docker_cmd),
    }
    (root_dir / "sandbox_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta
