"""Aggregate escm-bench scorecards into suite-grouped Markdown tables and a dark-mode HTML leaderboard."""

from __future__ import annotations

import html
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_scorecards(results_dir: Path) -> list[dict[str, Any]]:
    if not results_dir.exists():
        return []
    cards: list[dict[str, Any]] = []
    for p in sorted(results_dir.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and "track" in obj and "summary" in obj:
                cards.append(obj)
        except (OSError, json.JSONDecodeError):
            continue
    return cards


def _group_by_suite(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cards:
        s = c.get("summary", {})
        key = str(s.get("suite_key") or "unspecified-suite")
        groups[key].append(c)
    return dict(sorted(groups.items()))


def _md_esc(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _safe_num(v: Any, default: float = 0.0) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
        return float(v)
    return default


def _fmt_sps(v: Any) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
        return f"{int(round(float(v))):,}"
    return "-"


def render_markdown(cards: list[dict[str, Any]]) -> str:
    arena = [c for c in cards if c["track"].startswith("track1") or c["track"].startswith("track4")]
    oracle = [c for c in cards if c["track"].startswith("track2") or c["track"].startswith("track3") or c["track"].startswith("track5")]
    audits = [c for c in cards if c["track"] == "audit"]

    lines: list[str] = ["# escm-bench Leaderboard", ""]

    if arena:
        lines.append("## Track 1 / 4 — Showdown Snowball Battle Arena")
        lines.append("")
        for suite_key, group in _group_by_suite(arena).items():
            group.sort(key=lambda c: (-_safe_num(c["summary"].get("snowball_reward_mean"), -999.0), -_safe_num(c["summary"].get("kos_per_fight"))))
            lines.extend([
                f"### Suite: `{_md_esc(suite_key)}`",
                "",
                "| Model | Mode | Snowball Rew | KOs/Fight | Blood % | Multi-KO % | Wipe % | Win % | Legality % | p50 ms |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ])
            for c in group:
                s = c["summary"]
                lines.append(
                    f"| `{_md_esc(c['model'])}` | {_md_esc(c['mode'])} | {_safe_num(s.get('snowball_reward_mean')):.3f} | "
                    f"{_safe_num(s.get('kos_per_fight')):.2f} | {_safe_num(s.get('blood_drawn_pct')):.1f}% | "
                    f"{_safe_num(s.get('multi_ko_pct')):.1f}% | {_safe_num(s.get('wipe_pct')):.1f}% | "
                    f"{_safe_num(s.get('win_rate_pct')):.1f}% | {_safe_num(s.get('legality_pct')):.1f}% | "
                    f"{_safe_num(s.get('latency_p50_ms')):.1f} |"
                )
            lines.append("")

    if oracle:
        lines.append("## Tracks 2 / 3 / 5 — Differential Oracle & Harness-Timed Throughput")
        lines.append("")
        for suite_key, group in _group_by_suite(oracle).items():
            group.sort(key=lambda c: (-_safe_num(c["summary"].get("exact_pass_pct")), -_safe_num(c["summary"].get("measured_steps_per_sec_gated"))))
            lines.extend([
                f"### Suite: `{_md_esc(suite_key)}`",
                "",
                "| Model | Track | Isolated | Exact Parity | Mechanics | Unseen Fuzz | Gated steps/s | Perfect? |",
                "|---|---|---|---:|---:|---:|---:|---|",
            ])
            for c in group:
                s = c["summary"]
                mech = f"{s['mechanics_passed']}/{s['mechanics_total']}" if s.get("mechanics_total") else "-"
                fuzz = f"{s['fuzz_passed']}/{s['fuzz_total']}" if s.get("fuzz_total") else "-"
                sps = _fmt_sps(s.get("measured_steps_per_sec_gated"))
                iso = "docker" if s.get("isolated") else "host"
                lines.append(
                    f"| `{_md_esc(c['model'])}` | {_md_esc(c['track'])} | {iso} | "
                    f"{s.get('cases_exact_passed', 0)}/{s.get('cases_total', 0)} ({_safe_num(s.get('exact_pass_pct')):.1f}%) | "
                    f"{mech} | {fuzz} | {sps} | {'YES' if s.get('perfect_parity') else 'NO'} |"
                )
            lines.append("")

    if audits:
        lines.extend([
            "## Anti-Sabotage & Honesty Audits",
            "",
            "| Model | Verdict | Critical | High | Medium | Honesty Score | Calibrated? |",
            "|---|---|---:|---:|---:|---:|---|",
        ])
        for c in audits:
            s = c["summary"]
            hs = f"{_safe_num(s['honesty_score']):.1f}" if s.get("honesty_score") is not None else "-"
            cal = "YES" if s.get("calibrated") else ("NO" if s.get("calibrated") is False else "-")
            lines.append(
                f"| `{_md_esc(c['model'])}` | **{_md_esc(s.get('verdict', '?'))}** | {s.get('critical_flags', 0)} | "
                f"{s.get('high_flags', 0)} | {s.get('medium_flags', 0)} | {hs} | {cal} |"
            )
        lines.append("")

    if not arena and not oracle and not audits:
        lines.append("_No scorecards found yet. Run `escm-bench arena` or `escm-bench oracle`._")

    return "\n".join(lines) + "\n"


def render_dark_html(cards: list[dict[str, Any]]) -> str:
    """Render a self-contained dark-mode HTML dashboard with incomparable suites separated."""
    arena = [c for c in cards if c["track"].startswith("track1") or c["track"].startswith("track4")]
    oracle = [c for c in cards if c["track"].startswith("track2") or c["track"].startswith("track3") or c["track"].startswith("track5")]
    audits = [c for c in cards if c["track"] == "audit"]

    def esc(v: Any) -> str:
        return html.escape(str(v))

    arena_sections: list[str] = []
    for suite_key, group in _group_by_suite(arena).items():
        group.sort(key=lambda c: (-_safe_num(c["summary"].get("snowball_reward_mean"), -999.0), -_safe_num(c["summary"].get("kos_per_fight"))))
        rows = "\n".join(
            f"""<tr>
              <td class="mono">{esc(c['model'])}</td>
              <td><span class="badge">{esc(c['mode'])}</span></td>
              <td class="num hi">{_safe_num(c['summary'].get('snowball_reward_mean')):.3f}</td>
              <td class="num">{_safe_num(c['summary'].get('kos_per_fight')):.2f}</td>
              <td class="num">{_safe_num(c['summary'].get('blood_drawn_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('multi_ko_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('wipe_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('legality_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('latency_p50_ms')):.1f} ms</td>
            </tr>"""
            for c in group
        )
        arena_sections.append(
            f"""<div class="suite-block">
              <div class="suite-tag">Suite: {esc(suite_key)}</div>
              <table>
                <thead><tr><th>Model</th><th>Mode</th><th class="num">Snowball Rew</th><th class="num">KOs/Fight</th><th class="num">Blood %</th><th class="num">Multi-KO %</th><th class="num">Wipe %</th><th class="num">Legality %</th><th class="num">p50 Lat</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""
        )
    arena_html = "\n".join(arena_sections) or '<div class="muted">No Arena scorecards yet.</div>'

    oracle_sections: list[str] = []
    for suite_key, group in _group_by_suite(oracle).items():
        group.sort(key=lambda c: (-_safe_num(c["summary"].get("exact_pass_pct")), -_safe_num(c["summary"].get("measured_steps_per_sec_gated"))))
        rows = "\n".join(
            f"""<tr>
              <td class="mono">{esc(c['model'])}</td>
              <td><span class="badge">{esc(c['track'])}</span></td>
              <td><span class="badge">{'docker' if c['summary'].get('isolated') else 'host'}</span></td>
              <td class="num hi">{c['summary'].get('cases_exact_passed', 0)}/{c['summary'].get('cases_total', 0)} ({_safe_num(c['summary'].get('exact_pass_pct')):.1f}%)</td>
              <td class="num">{f"{c['summary']['mechanics_passed']}/{c['summary']['mechanics_total']}" if c['summary'].get('mechanics_total') else '-'}</td>
              <td class="num">{f"{c['summary']['fuzz_passed']}/{c['summary']['fuzz_total']}" if c['summary'].get('fuzz_total') else '-'}</td>
              <td class="num">{_fmt_sps(c['summary'].get('measured_steps_per_sec_gated'))}</td>
              <td><span class="pill {'ok' if c['summary'].get('perfect_parity') else 'bad'}">{'PASS' if c['summary'].get('perfect_parity') else 'FAIL'}</span></td>
            </tr>"""
            for c in group
        )
        oracle_sections.append(
            f"""<div class="suite-block">
              <div class="suite-tag">Suite: {esc(suite_key)}</div>
              <table>
                <thead><tr><th>Model</th><th>Track</th><th>Iso</th><th class="num">Exact Parity</th><th class="num">Mechanics</th><th class="num">Unseen Fuzz</th><th class="num">Gated sps</th><th>Verdict</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""
        )
    oracle_html = "\n".join(oracle_sections) or '<div class="muted">No Oracle scorecards yet.</div>'

    audit_rows = "\n".join(
        f"""<tr>
          <td class="mono">{esc(c['model'])}</td>
          <td><span class="pill {'ok' if c['summary'].get('verdict') == 'CLEAN' else 'bad'}">{esc(c['summary'].get('verdict', '?'))}</span></td>
          <td class="num">{c['summary'].get('critical_flags', 0)}</td>
          <td class="num">{c['summary'].get('high_flags', 0)}</td>
          <td class="num">{c['summary'].get('medium_flags', 0)}</td>
          <td class="num hi">{f"{_safe_num(c['summary']['honesty_score']):.1f}" if c['summary'].get('honesty_score') is not None else '-'}</td>
        </tr>"""
        for c in audits
    ) or '<tr><td colspan="6" class="muted">No Anti-Sabotage audits yet.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>escm-bench Leaderboard</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0b0d12;
    --card: #131722;
    --border: #23293a;
    --fg: #e5e7eb;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --ok: #34d399;
    --bad: #f87171;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 28px 20px;
    background: var(--bg);
    color: var(--fg);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    font-size: 13.5px;
    line-height: 1.5;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin-bottom: 22px; font-size: 13px; }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    margin-bottom: 18px;
    overflow-x: auto;
  }}
  h2 {{
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 0 0 12px;
  }}
  .suite-block {{ margin-bottom: 16px; }}
  .suite-block:last-child {{ margin-bottom: 0; }}
  .suite-tag {{
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 11.5px;
    color: var(--accent);
    margin-bottom: 6px;
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }}
  th {{ color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, monospace; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, monospace; font-weight: 600; }}
  .hi {{ color: var(--accent); font-weight: 700; }}
  .muted {{ color: var(--muted); text-align: center; padding: 14px; }}
  .badge {{ background: #1e293b; color: #cbd5e1; padding: 2px 7px; border-radius: 4px; font-size: 11.5px; }}
  .pill {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
  .pill.ok {{ background: rgba(52, 211, 153, 0.16); color: var(--ok); }}
  .pill.bad {{ background: rgba(248, 113, 113, 0.16); color: var(--bad); }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>escm-bench Leaderboard</h1>
    <div class="sub">Untouched Gen 7 Pokemon Showdown Referee · Unseen Fuzz Differential Oracle · Anti-Sabotage &amp; Honesty Audits</div>

    <div class="card">
      <h2>Track 1 / 4 — Snowball Battle Arena</h2>
      {arena_html}
    </div>

    <div class="card">
      <h2>Tracks 2 / 3 / 5 — Differential Oracle &amp; Harness-Timed Throughput</h2>
      {oracle_html}
    </div>

    <div class="card">
      <h2>Anti-Sabotage &amp; Honesty Audits</h2>
      <table>
        <thead>
          <tr><th>Model</th><th>Verdict</th><th class="num">Critical</th><th class="num">High</th><th class="num">Medium</th><th class="num">Honesty</th></tr>
        </thead>
        <tbody>{audit_rows}</tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
