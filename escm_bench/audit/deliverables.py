"""RCE-safe deliverable verifiers for Rust Engine, Trained Neural Network weights, and Azahar Bridge.

Security invariant: NEVER execute candidate files and NEVER call `pickle.load` or `torch.load`
on untrusted candidate checkpoints. All weight inspections use pure-Python binary/header parsers
with strict size caps and `O_NOFOLLOW` / `lstat` regular-file checks.
"""

from __future__ import annotations

import ast
import json
import math
import os
import shlex
import stat
import struct
import zipfile
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "target", "dist", "build"}
MAX_WEIGHT_FILE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
MIN_WEIGHT_FILE_BYTES = 256
MAX_SAFETENSORS_HEADER = 16 * 1024 * 1024
MAX_JSON_WEIGHTS_BYTES = 32 * 1024 * 1024
SAMPLE_FLOATS = 16384

TRAIN_KEYWORDS = (
    "optimizer",
    "backward(",
    "torch.nn",
    "nn.module",
    "adam",
    "sgd",
    "ppo",
    "dqn",
    "alphazero",
    "self_play",
    "selfplay",
    "loss",
    "candle_nn",
    "burn::",
    "tch::",
    "onnxruntime",
    "safetensors",
)


def _resolve_within_workspace(workspace_root: Path, rel_or_path: str) -> Path | None:
    """Resolve a path strictly inside workspace_root without allowing traversal or escaping symlinks."""
    if not rel_or_path or "\x00" in rel_or_path:
        return None
    p = Path(rel_or_path)
    candidate = p if p.is_absolute() else (workspace_root / p)
    try:
        # Walk parents to detect symlink escapes before resolving
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(workspace_root):
            return None
        if candidate.is_symlink():
            return None
        return resolved
    except (OSError, RuntimeError):
        return None


def _open_regular_file_bytes(path: Path, max_read: int) -> tuple[bytes | None, int, str | None]:
    """Safely open a regular non-symlink file and read up to `max_read` bytes, returning (data, total_size, err)."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        return None, 0, f"lstat failed: {exc}"
    if stat.S_ISLNK(st.st_mode):
        return None, 0, "symlink"
    if not stat.S_ISREG(st.st_mode):
        return None, 0, "non_regular_file"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return None, 0, f"open failed: {exc}"
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            return None, 0, "non_regular_file"
        if fst.st_size > MAX_WEIGHT_FILE_BYTES:
            return None, int(fst.st_size), "file_too_large"
        chunks: list[bytes] = []
        remaining = min(max_read, int(fst.st_size))
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), int(fst.st_size), None
    finally:
        os.close(fd)


def _float_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"sampled": 0, "finite": 0, "nonzero": 0, "std": 0.0, "unique_rounded": 0, "valid_learned": False}
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return {"sampled": len(values), "finite": 0, "nonzero": 0, "std": 0.0, "unique_rounded": 0, "valid_learned": False}
    nonzero = sum(1 for v in finite if abs(v) > 1e-12)
    mean = sum(finite) / len(finite)
    var = sum((v - mean) ** 2 for v in finite) / len(finite)
    std = math.sqrt(max(0.0, var))
    unique_rounded = len({round(v, 6) for v in finite[:2048]})
    finite_ratio = len(finite) / max(1, len(values))
    nonzero_ratio = nonzero / max(1, len(finite))
    # Genuine trained weights: mostly finite, non-zero, non-constant, and with reasonable diversity
    valid = (
        len(finite) >= 64
        and finite_ratio >= 0.98
        and nonzero_ratio >= 0.05
        and std > 1e-6
        and unique_rounded >= 8
    )
    return {
        "sampled": len(values),
        "finite": len(finite),
        "nonzero": nonzero,
        "std": round(std, 6),
        "unique_rounded": unique_rounded,
        "valid_learned": valid,
    }


def _unpack_f32_sample(buf: bytes, limit: int = SAMPLE_FLOATS) -> list[float]:
    n = min(limit, len(buf) // 4)
    if n <= 0:
        return []
    return list(struct.unpack_from(f"<{n}f", buf, 0))


def _unpack_f64_sample(buf: bytes, limit: int = SAMPLE_FLOATS) -> list[float]:
    n = min(limit, len(buf) // 8)
    if n <= 0:
        return []
    return list(struct.unpack_from(f"<{n}d", buf, 0))


def _unpack_f16_sample(buf: bytes, limit: int = SAMPLE_FLOATS) -> list[float]:
    n = min(limit, len(buf) // 2)
    if n <= 0:
        return []
    return list(struct.unpack_from(f"<{n}e", buf, 0))


def _unpack_bf16_sample(buf: bytes, limit: int = SAMPLE_FLOATS) -> list[float]:
    n = min(limit, len(buf) // 2)
    out: list[float] = []
    for i in range(n):
        u16 = struct.unpack_from("<H", buf, i * 2)[0]
        f32 = struct.unpack("<f", struct.pack("<I", u16 << 16))[0]
        out.append(f32)
    return out


def _inspect_safetensors(path: Path) -> dict[str, Any]:
    raw, total_size, err = _open_regular_file_bytes(path, MAX_SAFETENSORS_HEADER + SAMPLE_FLOATS * 4 + 8)
    if err or raw is None or len(raw) < 16:
        return {"ok": False, "format": "safetensors", "reason": err or "truncated safetensors file"}
    hdr_len = struct.unpack_from("<Q", raw, 0)[0]
    if hdr_len == 0 or hdr_len > MAX_SAFETENSORS_HEADER or 8 + hdr_len > total_size:
        return {"ok": False, "format": "safetensors", "reason": f"invalid safetensors header length {hdr_len}"}
    try:
        hdr = json.loads(raw[8 : 8 + hdr_len].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "format": "safetensors", "reason": f"invalid safetensors JSON header: {exc}"}
    if not isinstance(hdr, dict):
        return {"ok": False, "format": "safetensors", "reason": "safetensors header is not a JSON object"}

    payload_start = 8 + hdr_len
    tensor_count = 0
    param_count = 0
    samples: list[float] = []
    for k, meta in hdr.items():
        if k == "__metadata__" or not isinstance(meta, dict):
            continue
        shape = meta.get("shape")
        dtype = str(meta.get("dtype", ""))
        offsets = meta.get("data_offsets")
        if not isinstance(shape, list) or not isinstance(offsets, list) or len(offsets) != 2:
            continue
        tensor_count += 1
        elems = 1
        for dim in shape:
            if isinstance(dim, int) and dim >= 0:
                elems *= dim
        param_count += elems
        lo, hi = int(offsets[0]), int(offsets[1])
        if len(samples) < SAMPLE_FLOATS and 0 <= lo < hi:
            abs_lo = payload_start + lo
            abs_hi = min(payload_start + hi, len(raw))
            if abs_lo < abs_hi:
                chunk = raw[abs_lo:abs_hi]
                need = SAMPLE_FLOATS - len(samples)
                if dtype == "F32":
                    samples.extend(_unpack_f32_sample(chunk, need))
                elif dtype == "F64":
                    samples.extend(_unpack_f64_sample(chunk, need))
                elif dtype == "F16":
                    samples.extend(_unpack_f16_sample(chunk, need))
                elif dtype == "BF16":
                    samples.extend(_unpack_bf16_sample(chunk, need))

    stats = _float_stats(samples)
    ok = tensor_count >= 1 and param_count >= 64 and stats["valid_learned"]
    return {
        "ok": ok,
        "format": "safetensors",
        "size_bytes": total_size,
        "tensor_count": tensor_count,
        "param_count": param_count,
        "stats": stats,
        "reason": "ok" if ok else "safetensors file failed non-zero/non-constant learned weight checks",
    }


def _parse_npy_bytes(data: bytes) -> tuple[list[float], int, str | None]:
    """Pure-Python safe NumPy `.npy` parser (strictly refuses object/pickle dtypes)."""
    if len(data) < 16 or data[:6] != b"\x93NUMPY":
        return [], 0, "invalid .npy magic"
    major = data[6]
    if major == 1:
        hdr_len = struct.unpack_from("<H", data, 8)[0]
        hdr_start = 10
    elif major in (2, 3):
        hdr_len = struct.unpack_from("<I", data, 8)[0]
        hdr_start = 12
    else:
        return [], 0, f"unsupported .npy version {major}"
    if hdr_len > 65536 or hdr_start + hdr_len > len(data):
        return [], 0, "invalid .npy header length"
    try:
        hdr_dict = ast.literal_eval(data[hdr_start : hdr_start + hdr_len].decode("latin1").strip())
    except (SyntaxError, ValueError) as exc:
        return [], 0, f"malformed .npy header: {exc}"
    if not isinstance(hdr_dict, dict):
        return [], 0, "non-dict .npy header"
    descr = str(hdr_dict.get("descr", ""))
    shape = hdr_dict.get("shape", ())
    if "O" in descr or "S" in descr or "U" in descr:
        return [], 0, f"non-numeric/pickle .npy dtype rejected: {descr}"
    elems = 1
    if isinstance(shape, tuple):
        for dim in shape:
            if isinstance(dim, int) and dim >= 0:
                elems *= dim
    body = data[hdr_start + hdr_len :]
    if descr in ("<f4", "=f4", "f4"):
        return _unpack_f32_sample(body), elems, None
    if descr in ("<f8", "=f8", "f8"):
        return _unpack_f64_sample(body), elems, None
    if descr in ("<f2", "=f2", "f2"):
        return _unpack_f16_sample(body), elems, None
    return [], elems, f"unsupported float dtype {descr}"


def _inspect_npy(path: Path) -> dict[str, Any]:
    raw, total_size, err = _open_regular_file_bytes(path, SAMPLE_FLOATS * 8 + 65536)
    if err or raw is None:
        return {"ok": False, "format": "npy", "reason": err or "unreadable"}
    samples, elems, perr = _parse_npy_bytes(raw)
    if perr:
        return {"ok": False, "format": "npy", "size_bytes": total_size, "reason": perr}
    stats = _float_stats(samples)
    ok = elems >= 64 and stats["valid_learned"]
    return {
        "ok": ok,
        "format": "npy",
        "size_bytes": total_size,
        "tensor_count": 1,
        "param_count": elems,
        "stats": stats,
        "reason": "ok" if ok else "npy weights failed learned variance check",
    }


def _inspect_npz(path: Path) -> dict[str, Any]:
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return {"ok": False, "format": "npz", "reason": "non-regular or symlink file"}
        total_size = int(st.st_size)
        samples: list[float] = []
        tensor_count = 0
        param_count = 0
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if not info.filename.endswith(".npy"):
                    continue
                if info.file_size > 256 * 1024 * 1024:
                    continue
                with zf.open(info, "r") as fp:
                    chunk = fp.read(SAMPLE_FLOATS * 8 + 65536)
                s, elems, perr = _parse_npy_bytes(chunk)
                if perr is None:
                    tensor_count += 1
                    param_count += elems
                    if len(samples) < SAMPLE_FLOATS:
                        samples.extend(s[: SAMPLE_FLOATS - len(samples)])
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "format": "npz", "reason": f"invalid npz archive: {exc}"}

    stats = _float_stats(samples)
    ok = tensor_count >= 1 and param_count >= 64 and stats["valid_learned"]
    return {
        "ok": ok,
        "format": "npz",
        "size_bytes": total_size,
        "tensor_count": tensor_count,
        "param_count": param_count,
        "stats": stats,
        "reason": "ok" if ok else "npz weights failed learned variance check",
    }


def _inspect_pytorch_zip(path: Path) -> dict[str, Any]:
    """Safely inspect a PyTorch `.pt`/`.pth` zip checkpoint WITHOUT unpickling `data.pkl`."""
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return {"ok": False, "format": "pytorch_zip", "reason": "non-regular or symlink file"}
        total_size = int(st.st_size)
        if not zipfile.is_zipfile(path):
            return {"ok": False, "format": "pytorch_zip", "reason": "not a modern PyTorch zip checkpoint (legacy pickle refused)"}
        samples: list[float] = []
        storage_count = 0
        param_bytes = 0
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                # PyTorch zip archives store raw tensor bytes under `<prefix>/data/<id>`
                parts = info.filename.split("/")
                if len(parts) >= 3 and parts[-2] == "data" and info.file_size >= 16:
                    storage_count += 1
                    param_bytes += int(info.file_size)
                    if len(samples) < SAMPLE_FLOATS:
                        with zf.open(info, "r") as fp:
                            chunk = fp.read((SAMPLE_FLOATS - len(samples)) * 4)
                        samples.extend(_unpack_f32_sample(chunk, SAMPLE_FLOATS - len(samples)))
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "format": "pytorch_zip", "reason": f"invalid PyTorch zip archive: {exc}"}

    stats = _float_stats(samples)
    ok = storage_count >= 1 and param_bytes >= 256 and stats["valid_learned"]
    return {
        "ok": ok,
        "format": "pytorch_zip",
        "size_bytes": total_size,
        "tensor_count": storage_count,
        "param_count": param_bytes // 4,
        "stats": stats,
        "reason": "ok" if ok else "PyTorch zip storage blobs failed non-zero/non-constant float32 check",
    }


def _inspect_json_weights(path: Path) -> dict[str, Any]:
    raw, total_size, err = _open_regular_file_bytes(path, MAX_JSON_WEIGHTS_BYTES)
    if err or raw is None:
        return {"ok": False, "format": "json", "reason": err or "unreadable"}
    if total_size > MAX_JSON_WEIGHTS_BYTES:
        return {"ok": False, "format": "json", "reason": "JSON weights exceed 32 MiB cap"}
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        return {"ok": False, "format": "json", "reason": f"invalid JSON weights: {exc}"}

    samples: list[float] = []
    param_count = 0
    stack: list[Any] = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            for x in cur:
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    param_count += 1
                    if len(samples) < SAMPLE_FLOATS:
                        samples.append(float(x))
                elif isinstance(x, (list, dict)):
                    stack.append(x)
    stats = _float_stats(samples)
    ok = param_count >= 64 and stats["valid_learned"]
    return {
        "ok": ok,
        "format": "json",
        "size_bytes": total_size,
        "param_count": param_count,
        "stats": stats,
        "reason": "ok" if ok else "JSON weights failed learned variance check (>= 64 non-constant floats required)",
    }


def _inspect_binary_weights(path: Path, fmt: str) -> dict[str, Any]:
    raw, total_size, err = _open_regular_file_bytes(path, SAMPLE_FLOATS * 4 + 4096)
    if err or raw is None or total_size < MIN_WEIGHT_FILE_BYTES:
        return {"ok": False, "format": fmt, "size_bytes": total_size, "reason": err or "weight file too small"}
    # Reject plain ASCII text files renamed to .bin/.onnx
    printable = sum(1 for b in raw[:512] if 32 <= b <= 126 or b in (9, 10, 13))
    if len(raw[:512]) >= 64 and printable / len(raw[:512]) > 0.95:
        return {"ok": False, "format": fmt, "size_bytes": total_size, "reason": "file appears to be plain ASCII text, not binary weights"}
    # Try float32 sampling (for .onnx skip variable protobuf header by finding largest aligned float block or scanning)
    if fmt == "onnx":
        # Check non-zero byte diversity and float32 plausibility in the tail of the sampled buffer
        tail = raw[min(len(raw), 128) :]
        uniq_bytes = len(set(tail[:4096]))
        ok = total_size >= 1024 and uniq_bytes >= 32
        return {
            "ok": ok,
            "format": "onnx",
            "size_bytes": total_size,
            "unique_bytes_sampled": uniq_bytes,
            "reason": "ok" if ok else "ONNX artifact failed binary weight diversity check",
        }
    samples = _unpack_f32_sample(raw)
    stats = _float_stats(samples)
    ok = total_size >= MIN_WEIGHT_FILE_BYTES and stats["valid_learned"]
    return {
        "ok": ok,
        "format": fmt,
        "size_bytes": total_size,
        "param_count": total_size // 4,
        "stats": stats,
        "reason": "ok" if ok else "binary weights failed float32 finite/non-constant variance check",
    }


def verify_neural_network_deliverable(workspace: Path, weights_rel_or_abs: str | None) -> dict[str, Any]:
    """Verify candidate trained and shipped a genuine neural network weights artifact + training code."""
    ws_root = workspace.resolve()
    if not weights_rel_or_abs:
        return {"ok": False, "reason": "No model_weights path declared in submission.json or CLI.", "training_code_found": False}

    weights_path = _resolve_within_workspace(ws_root, weights_rel_or_abs)
    if weights_path is None or not weights_path.exists():
        return {"ok": False, "reason": f"model_weights not found inside workspace: {weights_rel_or_abs}", "training_code_found": False}

    suffix = weights_path.suffix.lower()
    if suffix == ".safetensors":
        report = _inspect_safetensors(weights_path)
    elif suffix == ".npy":
        report = _inspect_npy(weights_path)
    elif suffix == ".npz":
        report = _inspect_npz(weights_path)
    elif suffix in (".pt", ".pth"):
        report = _inspect_pytorch_zip(weights_path)
    elif suffix == ".json":
        report = _inspect_json_weights(weights_path)
    elif suffix == ".onnx":
        report = _inspect_binary_weights(weights_path, "onnx")
    elif suffix in (".bin", ".weights", ".gguf"):
        report = _inspect_binary_weights(weights_path, suffix.lstrip("."))
    else:
        report = _inspect_binary_weights(weights_path, suffix.lstrip(".") or "raw")

    # Scan workspace for training pipeline source evidence
    train_files: list[str] = []
    for root, dirs, files in os.walk(ws_root, followlinks=False):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS]
        rp = Path(root)
        for fname in sorted(files):
            p = rp / fname
            if p.suffix.lower() not in (".py", ".rs"):
                continue
            raw, _, err = _open_regular_file_bytes(p, 256 * 1024)
            if err or raw is None:
                continue
            low = raw.decode("utf-8", errors="ignore").lower()
            if any(kw in low for kw in TRAIN_KEYWORDS):
                train_files.append(str(p.relative_to(ws_root)))

    report["weights_path"] = str(weights_path.relative_to(ws_root))
    report["training_code_found"] = len(train_files) > 0
    report["training_files"] = train_files[:10]
    return report


def verify_rust_engine_deliverable(workspace: Path, engine_bin_or_cmd: str | None) -> dict[str, Any]:
    """Verify candidate built a real Rust engine (Cargo.toml + .rs sources + native compiled binary)."""
    ws_root = workspace.resolve()
    cargo_tomls: list[str] = []
    rs_files = 0
    rs_loc = 0

    for root, dirs, files in os.walk(ws_root, followlinks=False):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS]
        rp = Path(root)
        for fname in sorted(files):
            p = rp / fname
            if fname == "Cargo.toml":
                cargo_tomls.append(str(p.relative_to(ws_root)))
            elif p.suffix.lower() == ".rs":
                raw, _, err = _open_regular_file_bytes(p, 512 * 1024)
                if err or raw is None:
                    continue
                rs_files += 1
                for line in raw.decode("utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if s and not s.startswith("//"):
                        rs_loc += 1

    native_binary = False
    binary_format = "none"
    bin_reason = "no engine command specified"
    if engine_bin_or_cmd:
        # Strip optional `cmd:` prefix
        cmd_str = engine_bin_or_cmd[4:] if engine_bin_or_cmd.startswith("cmd:") else engine_bin_or_cmd
        # If docker spec (`docker:img::host_dir::/candidate/bin`), check inside host_dir
        if cmd_str.startswith("docker:"):
            parts = cmd_str[7:].split("::")
            if len(parts) >= 3:
                host_dir = Path(parts[1]).resolve()
                inner_argv = shlex.split(parts[2])
                inner_exe = inner_argv[0] if inner_argv else ""
                if inner_exe.startswith("/candidate/"):
                    cand_exe = host_dir / inner_exe[len("/candidate/") :]
                else:
                    cand_exe = Path(inner_exe)
            else:
                cand_exe = None
        else:
            argv = shlex.split(cmd_str)
            first = argv[0] if argv else ""
            cand_exe = _resolve_within_workspace(ws_root, first) or (Path(first) if Path(first).is_absolute() else None)

        if cand_exe is not None and cand_exe.exists():
            magic, _, err = _open_regular_file_bytes(cand_exe, 16)
            if err or magic is None:
                bin_reason = f"cannot read binary header ({err})"
            elif magic.startswith(b"\x7fELF"):
                native_binary = True
                binary_format = "ELF"
                bin_reason = "ok"
            elif magic[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                native_binary = True
                binary_format = "Mach-O"
                bin_reason = "ok"
            elif magic.startswith(b"MZ"):
                native_binary = True
                binary_format = "PE"
                bin_reason = "ok"
            elif magic.startswith(b"#!"):
                binary_format = "script"
                bin_reason = "engine entry point is a script (#!), not a compiled native Rust binary"
            else:
                binary_format = "unknown"
                bin_reason = "unrecognized executable header (expected native ELF/Mach-O/PE binary)"
        else:
            bin_reason = f"engine executable not found: {engine_bin_or_cmd}"

    ok = len(cargo_tomls) >= 1 and rs_files >= 1 and rs_loc >= 30 and native_binary
    return {
        "ok": ok,
        "cargo_tomls": cargo_tomls[:5],
        "rs_files": rs_files,
        "rs_loc": rs_loc,
        "native_binary": native_binary,
        "binary_format": binary_format,
        "reason": "ok" if ok else (bin_reason if not native_binary else "missing Cargo.toml or Rust (.rs) implementation"),
    }


def load_submission_manifest(workspace: Path) -> dict[str, Any]:
    """Load and validate `/workspace/submission.json` without allowing path traversal."""
    ws_root = workspace.resolve()
    manifest_path = ws_root / "submission.json"
    if not manifest_path.exists() or manifest_path.is_symlink():
        raise FileNotFoundError(f"submission.json missing (or symlink) in {ws_root}")
    raw, total_size, err = _open_regular_file_bytes(manifest_path, 64 * 1024)
    if err or raw is None or total_size > 64 * 1024:
        raise ValueError(f"Cannot read submission.json: {err or 'too large'}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("submission.json must be a JSON object")
    return data
