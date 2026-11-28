"""Shared protocol constants and scorecard schemas for escm-bench."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

ORACLE_PROTOCOL = "escm-diff-oracle/1"
BOT_PROTOCOL = "decide-bot/1"
ORACLE_TIERS = ("legal", "order", "state", "hidden", "events")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SelfReport:
    """Candidate's self-reported claims, audited against hidden harness measurements."""

    model: str
    track: str
    claimed_oracle_passed: Any = None
    claimed_oracle_total: Any = None
    claimed_fuzz_pass_rate: Any = None
    claimed_steps_per_sec: Any = None
    claimed_snowball_kos: Any = None
    known_gaps: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "SelfReport":
        if not isinstance(data, dict):
            raise ValueError(f"self_report.json must be a JSON object, got {type(data).__name__}")
        raw_gaps = data.get("known_gaps")
        if raw_gaps is None:
            gaps: list[str] = []
        elif isinstance(raw_gaps, list):
            gaps = [str(g) for g in raw_gaps]
        else:
            raise ValueError("known_gaps must be a JSON list")
        sps = data.get("claimed_steps_per_sec")
        if sps is None:
            sps = data.get("claimed_steps_per_sec_16t", data.get("claimed_steps_per_sec_1t"))
        return cls(
            model=str(data.get("model", "unknown")),
            track=str(data.get("track", "unknown")),
            claimed_oracle_passed=data.get("claimed_oracle_passed"),
            claimed_oracle_total=data.get("claimed_oracle_total"),
            claimed_fuzz_pass_rate=data.get("claimed_fuzz_pass_rate"),
            claimed_steps_per_sec=sps,
            claimed_snowball_kos=data.get("claimed_snowball_kos"),
            known_gaps=gaps,
            notes=str(data.get("notes", "")),
        )


@dataclass
class ArenaBattleResult:
    scenario_id: str
    seed: list[int]
    player_policy: str
    turns: int
    decisions: int
    winner: str | None
    ai_won: bool
    player_kos: int
    player_team_size: int
    ai_kos: int
    ai_team_size: int
    blood_drawn: bool
    multi_ko: bool
    wiped: bool
    snowball_reward: float
    valid_decisions: int
    parse_errors: int
    illegal_actions: int
    timeouts: int
    latencies_ms: list[float] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Scorecard:
    """Unified leaderboard scorecard persisted under results/."""

    run_id: str
    track: str
    model: str
    mode: str
    created_at: str = field(default_factory=utc_now_iso)
    summary: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
