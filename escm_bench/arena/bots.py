"""Trainer AI bots for Track 1 (Direct LLM Play) and Track 4/5 (External `decide-bot/1` binaries)."""

from __future__ import annotations

import abc
import json
import os
import queue
import random
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from escm_bench.contracts import BOT_PROTOCOL

SYSTEM_PROMPT = """You are a Pokémon Ultra Sun / Ultra Moon trainer AI inside a Nuzlocke challenge.
Your objective is NOT to stall or protect your own HP — your objective is the NUZLOCKE SNOWBALL:
- Claim permanent KOs on the player's Pokémon (especially high-value carries or low-HP targets).
- Trading/sacrificing a Pokémon or spending a Z-Move to guarantee a KO is GOOD.
- Punish Nuzlocke switches (when the player is threatened and slower, they often pivot).
- You only know what is shown under fog_of_war.

Respond ONLY with strict JSON matching:
{"action_id": <integer id from legal_actions>, "reason": "<one concise sentence>"}"""


@dataclass
class BotDecision:
    action_id: int
    valid: bool = True
    parse_error: bool = False
    illegal_action: bool = False
    timed_out: bool = False
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reason: str = ""


class TrainerBot(abc.ABC):
    name: str = "bot"
    mode: str = "baseline"

    @abc.abstractmethod
    def choose_action(self, state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> BotDecision:
        ...

    def close(self) -> None:
        pass


class FirstLegalBot(TrainerBot):
    name = "baseline:first-legal"
    mode = "baseline"

    def choose_action(self, state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> BotDecision:
        return BotDecision(action_id=int(legal_actions[0]["id"]), reason="first legal action")


class RandomBot(TrainerBot):
    mode = "baseline"

    def __init__(self, seed: int = 0) -> None:
        self.name = f"baseline:random-{seed}"
        self._rng = random.Random(seed)

    def choose_action(self, state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> BotDecision:
        pick = self._rng.choice(legal_actions)
        return BotDecision(action_id=int(pick["id"]), reason="uniform random legal")


_BP_RE = re.compile(r"BP\s+(\d+)")
_TYPE_RE = re.compile(r"\(([A-Za-z]+),\s*(Physical|Special|Status)")

_TYPE_CHART: dict[str, dict[str, float]] = {
    "Normal": {"Rock": 0.5, "Ghost": 0.0, "Steel": 0.5},
    "Fire": {"Fire": 0.5, "Water": 0.5, "Grass": 2.0, "Ice": 2.0, "Bug": 2.0, "Rock": 0.5, "Dragon": 0.5, "Steel": 2.0},
    "Water": {"Fire": 2.0, "Water": 0.5, "Grass": 0.5, "Ground": 2.0, "Rock": 2.0, "Dragon": 0.5},
    "Electric": {"Water": 2.0, "Electric": 0.5, "Grass": 0.5, "Ground": 0.0, "Flying": 2.0, "Dragon": 0.5},
    "Grass": {"Fire": 0.5, "Water": 2.0, "Grass": 0.5, "Poison": 0.5, "Ground": 2.0, "Flying": 0.5, "Bug": 0.5, "Rock": 2.0, "Dragon": 0.5, "Steel": 0.5},
    "Ice": {"Fire": 0.5, "Water": 0.5, "Grass": 2.0, "Ice": 0.5, "Ground": 2.0, "Flying": 2.0, "Dragon": 2.0, "Steel": 0.5},
    "Fighting": {"Normal": 2.0, "Ice": 2.0, "Poison": 0.5, "Flying": 0.5, "Psychic": 0.5, "Bug": 0.5, "Rock": 2.0, "Ghost": 0.0, "Dark": 2.0, "Steel": 2.0, "Fairy": 0.5},
    "Poison": {"Grass": 2.0, "Poison": 0.5, "Ground": 0.5, "Rock": 0.5, "Ghost": 0.5, "Steel": 0.0, "Fairy": 2.0},
    "Ground": {"Fire": 2.0, "Electric": 2.0, "Grass": 0.5, "Poison": 2.0, "Flying": 0.0, "Bug": 0.5, "Rock": 2.0, "Steel": 2.0},
    "Flying": {"Electric": 0.5, "Grass": 2.0, "Fighting": 2.0, "Bug": 2.0, "Rock": 0.5, "Steel": 0.5},
    "Psychic": {"Fighting": 2.0, "Poison": 2.0, "Psychic": 0.5, "Dark": 0.0, "Steel": 0.5},
    "Bug": {"Fire": 0.5, "Grass": 2.0, "Fighting": 0.5, "Poison": 0.5, "Flying": 0.5, "Psychic": 2.0, "Ghost": 0.5, "Dark": 2.0, "Steel": 0.5, "Fairy": 0.5},
    "Rock": {"Fire": 2.0, "Ice": 2.0, "Fighting": 0.5, "Ground": 0.5, "Flying": 2.0, "Bug": 2.0, "Steel": 0.5},
    "Ghost": {"Normal": 0.0, "Psychic": 2.0, "Ghost": 2.0, "Dark": 0.5},
    "Dragon": {"Dragon": 2.0, "Steel": 0.5, "Fairy": 0.0},
    "Dark": {"Fighting": 0.5, "Psychic": 2.0, "Ghost": 2.0, "Dark": 0.5, "Fairy": 0.5},
    "Steel": {"Fire": 0.5, "Water": 0.5, "Electric": 0.5, "Ice": 2.0, "Rock": 2.0, "Steel": 0.5, "Fairy": 2.0},
    "Fairy": {"Fire": 0.5, "Fighting": 2.0, "Poison": 0.5, "Dragon": 2.0, "Dark": 2.0, "Steel": 0.5},
}


def _eff(mtype: str, dtypes: list[str]) -> float:
    chart = _TYPE_CHART.get(mtype, {})
    m = 1.0
    for dt in dtypes:
        m *= chart.get(dt, 1.0)
    return m


class HeuristicSnowballBot(TrainerBot):
    """Fast, zero-cheat rule-based Gen 7 Snowball baseline (STAB + type chart + Z-Move sniping)."""

    name = "baseline:heuristic-snowball"
    mode = "baseline"

    def choose_action(self, state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> BotDecision:
        t0 = time.perf_counter()
        if len(legal_actions) == 1:
            return BotDecision(action_id=int(legal_actions[0]["id"]), latency_ms=(time.perf_counter() - t0) * 1000)

        ai_act = state.get("ai_active") or {}
        pl_act = state.get("player_active") or {}
        ai_types = ai_act.get("types") or []
        pl_types = pl_act.get("types") or []
        pl_hp = float(pl_act.get("hp_pct", 100))
        ai_hp = float(ai_act.get("hp_pct", 100))

        best = legal_actions[0]
        best_score = -1e9

        for act in legal_actions:
            kind = act.get("kind", "")
            label = act.get("label", "")
            score = 0.0
            if kind == "switch":
                hp_pct = float(act.get("hp_pct", 50))
                score = 15.0 + hp_pct * 0.2
            else:
                mtype_match = _TYPE_RE.search(label)
                bp_match = _BP_RE.search(label)
                mtype = mtype_match.group(1) if mtype_match else "Normal"
                cat = mtype_match.group(2) if mtype_match else "Physical"
                bp = int(bp_match.group(1)) if bp_match else (160 if kind == "zmove" else 0)
                if kind == "zmove":
                    score = 165.0 if pl_hp <= 85 else 110.0
                elif cat == "Status":
                    mv = act.get("move", "")
                    if mv in ("destinybond",) and ai_hp <= 45:
                        score = 190.0
                    elif mv in ("stealthrock", "tailwind", "swordsdance") and ai_hp >= 70:
                        score = 55.0
                    elif mv in ("willowisp", "thunderwave", "toxic") and not pl_act.get("status"):
                        score = 60.0
                    else:
                        score = 10.0
                else:
                    eff = _eff(mtype, pl_types)
                    stab = 1.5 if mtype in ai_types else 1.0
                    score = bp * stab * eff
                    if act.get("move") == "explosion" and ai_hp <= 45:
                        score *= 1.35
                    if kind in ("mega", "ultra"):
                        score += 25.0
            if score > best_score:
                best_score = score
                best = act

        return BotDecision(
            action_id=int(best["id"]),
            latency_ms=(time.perf_counter() - t0) * 1000,
            reason=f"heuristic score={best_score:.1f} ({best['token']})",
        )


def _extract_json_obj(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
    if m:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    raise ValueError("No valid JSON object found in model output")


def _parse_strict_action_id(parsed: dict[str, Any]) -> int:
    """Require an exact JSON integer (reject floats like 1.9, bools, and strings)."""
    if "action_id" not in parsed:
        raise ValueError("Missing action_id")
    raw_id = parsed["action_id"]
    if type(raw_id) is not int:
        raise ValueError(f"action_id must be a strict JSON integer, got {type(raw_id).__name__}: {raw_id!r}")
    return raw_id


def _origin(url: str) -> tuple[str, str, int | None]:
    p = urllib.parse.urlparse(url)
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port or (443 if scheme == "https" else (80 if scheme == "http" else None))
    return (scheme, host, port)


def _resolve_api_key(base_url: str, explicit_key: str | None) -> str:
    """Never send an ambient OPENAI_API_KEY to HTTP, non-default ports, or non-OpenAI endpoints."""
    if explicit_key:
        return explicit_key
    bench_key = os.environ.get("ESCM_BENCH_API_KEY")
    if bench_key:
        return bench_key
    p = urllib.parse.urlparse(base_url)
    if p.scheme.lower() == "https" and (p.hostname or "").lower() == "api.openai.com" and p.port in (None, 443):
        return os.environ.get("OPENAI_API_KEY", "")
    return "local"


class _SameOriginAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip Authorization headers whenever an HTTP redirect changes scheme, host, or port."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and _origin(req.full_url) != _origin(newurl):
            new_req.remove_header("Authorization")
            new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req


_SAFE_URL_OPENER = urllib.request.build_opener(_SameOriginAuthRedirectHandler())


class OpenAILLMTrainerBot(TrainerBot):
    """OpenAI-compatible / Ollama / vLLM direct LLM trainer bot with STRICT no-cheat fallback."""

    mode = "direct-llm"

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key: str | None = None,
        temperature: float = 0.1,
        timeout_sec: float = 30.0,
        max_tokens: int = 256,
    ) -> None:
        self.model = model
        self.name = f"llm:{model}"
        self.base_url = base_url.rstrip("/")
        self.api_key = _resolve_api_key(self.base_url, api_key)
        self.temperature = temperature
        self.timeout_sec = timeout_sec
        self.max_tokens = max_tokens

    def choose_action(self, state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> BotDecision:
        fallback_id = int(legal_actions[0]["id"])
        legal_ids = {int(a["id"]) for a in legal_actions}
        if len(legal_ids) <= 1:
            return BotDecision(action_id=fallback_id, valid=True, reason="forced single legal action")

        user_payload = {
            "state": state,
            "legal_actions": [{"id": a["id"], "token": a["token"], "label": a["label"]} for a in legal_actions],
        }
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, separators=(",", ":"))},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with _SAFE_URL_OPENER.open(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                timed_out=True,
                latency_ms=(time.perf_counter() - t0) * 1000,
                reason=f"timeout: {exc}",
            )
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            is_to = "timed out" in str(exc).lower()
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                timed_out=is_to,
                parse_error=not is_to,
                latency_ms=(time.perf_counter() - t0) * 1000,
                reason=f"request error: {exc}",
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        usage = data.get("usage") or {}
        ptok = int(usage.get("prompt_tokens") or 0)
        ctok = int(usage.get("completion_tokens") or 0)

        try:
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json_obj(str(content))
            aid = _parse_strict_action_id(parsed)
            reason = str(parsed.get("reason", ""))[:200]
        except Exception as exc:
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                parse_error=True,
                latency_ms=elapsed_ms,
                prompt_tokens=ptok,
                completion_tokens=ctok,
                reason=f"parse_error: {exc}",
            )

        if aid not in legal_ids:
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                illegal_action=True,
                latency_ms=elapsed_ms,
                prompt_tokens=ptok,
                completion_tokens=ctok,
                reason=f"illegal action_id={aid} (legal={sorted(legal_ids)})",
            )

        return BotDecision(
            action_id=aid,
            valid=True,
            latency_ms=elapsed_ms,
            prompt_tokens=ptok,
            completion_tokens=ctok,
            reason=reason,
        )


def timed_write_line(proc: subprocess.Popen[str], line: str, timeout_sec: float) -> bool:
    """Write a line to proc.stdin within `timeout_sec`; return False if the write times out or fails."""
    assert proc.stdin is not None
    done = threading.Event()
    err_box: list[BaseException] = []

    def _writer() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(line)
            proc.stdin.flush()
        except BaseException as exc:
            err_box.append(exc)
        finally:
            done.set()

    threading.Thread(target=_writer, daemon=True).start()
    if not done.wait(timeout=max(0.001, timeout_sec)):
        return False
    if err_box:
        raise err_box[0]
    return True


class SubprocessBot(TrainerBot):
    """Runs an external bot command speaking `decide-bot/1` over JSON lines.

    Enforces a single end-to-end `timeout_sec` deadline across both stdin write and stdout read,
    and drains stderr so a hung or noisy worker cannot deadlock the suite.
    """

    mode = "bot-cmd"

    def __init__(self, command: str, name: str | None = None, timeout_sec: float = 15.0) -> None:
        self.argv = shlex.split(command)
        self.name = name or f"cmd:{self.argv[0]}"
        self.timeout_sec = timeout_sec
        self._proc: subprocess.Popen[str] | None = None
        self._out_q: queue.Queue[str | None] = queue.Queue()
        self._start_proc()

    def _start_proc(self) -> None:
        self._out_q = queue.Queue()
        self._proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        proc = self._proc
        q = self._out_q

        def _reader() -> None:
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put(None)

        threading.Thread(target=_reader, daemon=True).start()

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None

    def choose_action(self, state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> BotDecision:
        fallback_id = int(legal_actions[0]["id"])
        legal_ids = {int(a["id"]) for a in legal_actions}
        t0 = time.perf_counter()
        deadline = t0 + self.timeout_sec

        if self._proc is None or self._proc.poll() is not None:
            self._start_proc()

        assert self._proc is not None
        req_line = json.dumps({"protocol": BOT_PROTOCOL, "state": state, "legal_actions": legal_actions}, separators=(",", ":")) + "\n"
        try:
            wrote = timed_write_line(self._proc, req_line, max(0.001, deadline - time.perf_counter()))
        except OSError as exc:
            self._kill_proc()
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                parse_error=True,
                latency_ms=(time.perf_counter() - t0) * 1000,
                reason=f"stdin write error: {exc}",
            )

        if not wrote:
            self._kill_proc()
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                timed_out=True,
                latency_ms=(time.perf_counter() - t0) * 1000,
                reason=f"stdin write timeout after {self.timeout_sec}s",
            )

        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            self._kill_proc()
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                timed_out=True,
                latency_ms=(time.perf_counter() - t0) * 1000,
                reason=f"timeout after {self.timeout_sec}s",
            )

        try:
            line = self._out_q.get(timeout=remaining)
        except queue.Empty:
            self._kill_proc()
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                timed_out=True,
                latency_ms=(time.perf_counter() - t0) * 1000,
                reason=f"timeout after {self.timeout_sec}s",
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        if line is None:
            self._kill_proc()
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                parse_error=True,
                latency_ms=elapsed_ms,
                reason="bot process closed stdout",
            )

        try:
            resp = json.loads(line)
            if not isinstance(resp, dict):
                raise ValueError("Response must be a JSON object")
            aid = _parse_strict_action_id(resp)
            reason = str(resp.get("reason", ""))[:200]
        except Exception as exc:
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                parse_error=True,
                latency_ms=elapsed_ms,
                reason=f"parse_error: {exc}",
            )

        if aid not in legal_ids:
            return BotDecision(
                action_id=fallback_id,
                valid=False,
                illegal_action=True,
                latency_ms=elapsed_ms,
                reason=f"illegal action_id={aid}",
            )
        return BotDecision(action_id=aid, valid=True, latency_ms=elapsed_ms, reason=reason)

    def close(self) -> None:
        self._kill_proc()
