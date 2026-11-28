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
    full_build = [c for c in cards if c["track"] == "full_build"]
    h2h = [c for c in cards if c["track"] == "h2h"]
    bridge = [c for c in cards if c["track"] == "bridge"]
    arena = [c for c in cards if c["track"].startswith("track1") or c["track"].startswith("track4")]
    oracle = [c for c in cards if c["track"].startswith("track2") or c["track"].startswith("track3") or c["track"].startswith("track5")]
    audits = [c for c in cards if c["track"] == "audit"]

    lines: list[str] = ["# escm-bench Leaderboard", ""]

    if full_build:
        lines.append("## Full-Build Coding Model Leaderboard (Rust Engine + Trained NN vs `escm` + Azahar Bridge)")
        lines.append("")
        for suite_key, group in _group_by_suite(full_build).items():
            group.sort(key=lambda c: (-_safe_num(c["summary"].get("composite_score")), -_safe_num(c["summary"].get("oracle_exact_pct"))))
            lines.extend([
                f"### Suite: `{_md_esc(suite_key)}`",
                "",
                "| Model | Composite (/100) | Rust Engine | Exact % | Gated sps | NN Weights | H2H Win % | Boss SB Δ | Bridge | p50 ms | Verdict |",
                "|---|---:|---|---:|---:|---|---:|---:|---|---:|---|",
            ])
            for c in group:
                s = c["summary"]
                br_cell = "PASS" if s.get("bridge_pass") else f"{_safe_num(s.get('bridge_score')):.0f}%"
                lines.append(
                    f"| `{_md_esc(c['model'])}` | **{_safe_num(s.get('composite_score')):.1f}** | "
                    f"{'YES' if s.get('rust_deliverable_ok') else 'NO'} | {_safe_num(s.get('oracle_exact_pct')):.1f}% | "
                    f"{_fmt_sps(s.get('gated_steps_per_sec'))} | {'YES' if s.get('nn_weights_verified') else 'NO'} | "
                    f"{_safe_num(s.get('h2h_win_rate_pct')):.1f}% | {_safe_num(s.get('h2h_boss_snowball_delta')):+.2f} | "
                    f"{br_cell} | "
                    f"{_safe_num(s.get('bridge_latency_p50_ms')):.1f} | **{_md_esc(s.get('verdict', '?'))}** |"
                )
            lines.append("")

    if h2h:
        lines.append("## Head-to-Head Mirrored Battles (Candidate Trained NN vs Reference `escm` Model)")
        lines.append("")
        for suite_key, group in _group_by_suite(h2h).items():
            group.sort(key=lambda c: (-_safe_num(c["summary"].get("candidate_win_rate_pct")), -_safe_num(c["summary"].get("boss_snowball_delta"))))
            lines.extend([
                f"### Suite: `{_md_esc(suite_key)}`",
                "",
                "| Candidate | Baseline | Win % | Loss % | Draw % | Boss SB Δ | KOs Δ | Legality % | p50 ms |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|",
            ])
            for c in group:
                s = c["summary"]
                lines.append(
                    f"| `{_md_esc(c['model'])}` | `{_md_esc(s.get('baseline_model', '?'))}` | "
                    f"{_safe_num(s.get('candidate_win_rate_pct')):.1f}% | {_safe_num(s.get('baseline_win_rate_pct')):.1f}% | "
                    f"{_safe_num(s.get('draw_pct')):.1f}% | {_safe_num(s.get('boss_snowball_delta')):+.2f} | "
                    f"{_safe_num(s.get('kos_delta')):+.2f} | {_safe_num(s.get('candidate_legality_pct')):.1f}% | "
                    f"{_safe_num(s.get('candidate_latency_p50_ms')):.1f} |"
                )
            lines.append("")

    if bridge:
        lines.append("## Azahar 3DS UDP RPC v2 <-> ML Bridge Grader")
        lines.append("")
        for suite_key, group in _group_by_suite(bridge).items():
            group.sort(key=lambda c: (-_safe_num(c["summary"].get("bridge_score")), _safe_num(c["summary"].get("latency_p50_ms"), 9999.0)))
            lines.extend([
                f"### Suite: `{_md_esc(suite_key)}`",
                "",
                "| Model | Score | Pass? | Hook | Turns | Mem Safe | Clean Uninstall | p50 ms |",
                "|---|---:|---|---|---:|---|---|---:|",
            ])
            for c in group:
                s = c["summary"]
                lines.append(
                    f"| `{_md_esc(c['model'])}` | {_safe_num(s.get('bridge_score')):.1f} | "
                    f"{'PASS' if s.get('bridge_pass') else 'FAIL'} | {'OK' if s.get('hook_branch_valid') else 'NO'} | "
                    f"{s.get('turns_passed', 0)}/{s.get('turns_total', 0)} | {'YES' if s.get('memory_safe') else 'NO'} | "
                    f"{'YES' if s.get('clean_uninstall') and s.get('mailbox_cleared') else 'NO'} | "
                    f"{_safe_num(s.get('latency_p50_ms')):.1f} |"
                )
            lines.append("")

    if oracle:
        lines.append("## Rust Engine Differential Oracle & Harness-Timed Throughput")
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

    if arena:
        lines.append("## Showdown Snowball Battle Arena (vs Baseline Nuzlocke Player)")
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

    if not (full_build or h2h or bridge or arena or oracle or audits):
        lines.append("_No scorecards found yet. Run `escm-bench grade-submission`, `escm-bench oracle`, `escm-bench h2h`, or `escm-bench bridge`._")

    return "\n".join(lines) + "\n"


def render_dark_html(cards: list[dict[str, Any]]) -> str:
    """Render a self-contained dark-mode HTML dashboard with incomparable suites separated."""
    full_build = [c for c in cards if c["track"] == "full_build"]
    h2h = [c for c in cards if c["track"] == "h2h"]
    bridge = [c for c in cards if c["track"] == "bridge"]
    arena = [c for c in cards if c["track"].startswith("track1") or c["track"].startswith("track4")]
    oracle = [c for c in cards if c["track"].startswith("track2") or c["track"].startswith("track3") or c["track"].startswith("track5")]
    audits = [c for c in cards if c["track"] == "audit"]

    def esc(v: Any) -> str:
        return html.escape(str(v))

    def pill(ok: bool, yes_txt: str = "PASS", no_txt: str = "FAIL") -> str:
        return f'<span class="pill {"ok" if ok else "bad"}">{esc(yes_txt if ok else no_txt)}</span>'

    # Full-build composite sections
    fb_sections: list[str] = []
    for suite_key, group in _group_by_suite(full_build).items():
        group.sort(key=lambda c: (-_safe_num(c["summary"].get("composite_score")), -_safe_num(c["summary"].get("oracle_exact_pct"))))
        rows = "\n".join(
            f"""<tr>
              <td class="mono">{esc(c['model'])}</td>
              <td class="num hi">{_safe_num(c['summary'].get('composite_score')):.1f}</td>
              <td>{pill(bool(c['summary'].get('rust_deliverable_ok')), 'RUST', 'NO')}</td>
              <td class="num">{_safe_num(c['summary'].get('oracle_exact_pct')):.1f}%</td>
              <td class="num">{_fmt_sps(c['summary'].get('gated_steps_per_sec'))}</td>
              <td>{pill(bool(c['summary'].get('nn_weights_verified')), 'NN OK', 'NO NN')}</td>
              <td class="num">{_safe_num(c['summary'].get('h2h_win_rate_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('h2h_boss_snowball_delta')):+.2f}</td>
              <td>{pill(bool(c['summary'].get('bridge_pass')), 'PASS', f"{_safe_num(c['summary'].get('bridge_score')):.0f}%")}</td>
              <td class="num">{_safe_num(c['summary'].get('bridge_latency_p50_ms')):.1f} ms</td>
              <td>{pill(c['summary'].get('verdict') == 'CLEAN', str(c['summary'].get('verdict', '?')), str(c['summary'].get('verdict', '?')))}</td>
            </tr>"""
            for c in group
        )
        fb_sections.append(
            f"""<div class="suite-block">
              <div class="suite-tag">Suite: {esc(suite_key)}</div>
              <table>
                <thead><tr><th>Model</th><th class="num">Score</th><th>Rust</th><th class="num">Oracle %</th><th class="num">Gated sps</th><th>Weights</th><th class="num">H2H Win %</th><th class="num">Boss SB Δ</th><th>Bridge</th><th class="num">Bridge p50</th><th>Verdict</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""
        )
    fb_html = "\n".join(fb_sections) or '<div class="muted">No Full-Build submissions graded yet. Run <code>escm-bench grade-submission</code>.</div>'

    h2h_sections: list[str] = []
    for suite_key, group in _group_by_suite(h2h).items():
        group.sort(key=lambda c: (-_safe_num(c["summary"].get("candidate_win_rate_pct")), -_safe_num(c["summary"].get("boss_snowball_delta"))))
        rows = "\n".join(
            f"""<tr>
              <td class="mono">{esc(c['model'])}</td>
              <td><span class="badge">{esc(c['summary'].get('baseline_model', '?'))}</span></td>
              <td class="num hi">{_safe_num(c['summary'].get('candidate_win_rate_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('baseline_win_rate_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('draw_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('boss_snowball_delta')):+.2f}</td>
              <td class="num">{_safe_num(c['summary'].get('kos_delta')):+.2f}</td>
              <td class="num">{_safe_num(c['summary'].get('candidate_legality_pct')):.1f}%</td>
              <td class="num">{_safe_num(c['summary'].get('candidate_latency_p50_ms')):.1f} ms</td>
            </tr>"""
            for c in group
        )
        h2h_sections.append(
            f"""<div class="suite-block">
              <div class="suite-tag">Suite: {esc(suite_key)}</div>
              <table>
                <thead><tr><th>Candidate</th><th>Baseline</th><th class="num">Win %</th><th class="num">Loss %</th><th class="num">Draw %</th><th class="num">Boss SB Δ</th><th class="num">KOs Δ</th><th class="num">Legality</th><th class="num">p50 Lat</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""
        )
    h2h_html = "\n".join(h2h_sections) or '<div class="muted">No Head-to-Head scorecards yet.</div>'

    bridge_sections: list[str] = []
    for suite_key, group in _group_by_suite(bridge).items():
        group.sort(key=lambda c: (-_safe_num(c["summary"].get("bridge_score")), _safe_num(c["summary"].get("latency_p50_ms"), 9999.0)))
        rows = "\n".join(
            f"""<tr>
              <td class="mono">{esc(c['model'])}</td>
              <td class="num hi">{_safe_num(c['summary'].get('bridge_score')):.1f}</td>
              <td>{pill(bool(c['summary'].get('bridge_pass')))}</td>
              <td>{pill(bool(c['summary'].get('hook_branch_valid')), 'OK', 'NO')}</td>
              <td class="num">{c['summary'].get('turns_passed', 0)}/{c['summary'].get('turns_total', 0)}</td>
              <td>{pill(bool(c['summary'].get('memory_safe')), 'SAFE', 'OOB')}</td>
              <td>{pill(bool(c['summary'].get('clean_uninstall') and c['summary'].get('mailbox_cleared')), 'CLEAN', 'DIRTY')}</td>
              <td class="num">{_safe_num(c['summary'].get('latency_p50_ms')):.1f} ms</td>
            </tr>"""
            for c in group
        )
        bridge_sections.append(
            f"""<div class="suite-block">
              <div class="suite-tag">Suite: {esc(suite_key)}</div>
              <table>
                <thead><tr><th>Model</th><th class="num">Score</th><th>Verdict</th><th>Hook</th><th class="num">Turns</th><th>Mem Safe</th><th>Uninstall</th><th class="num">p50 Lat</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""
        )
    bridge_html = "\n".join(bridge_sections) or '<div class="muted">No Azahar Bridge scorecards yet.</div>'

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
              <td>{pill(bool(c['summary'].get('perfect_parity')))}</td>
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

    audit_rows = "\n".join(
        f"""<tr>
          <td class="mono">{esc(c['model'])}</td>
          <td>{pill(c['summary'].get('verdict') == 'CLEAN', str(c['summary'].get('verdict', '?')), str(c['summary'].get('verdict', '?')))}</td>
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
  .wrap {{ max-width: 1180px; margin: 0 auto; }}
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
  code {{ background: #1e293b; padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>escm-bench Leaderboard</h1>
    <div class="sub">Full-Build Coding-Agent Benchmark · Rust Gen 7 Engine · Trained NN vs escm · Azahar 3DS UDP RPC v2 Bridge · Anti-Sabotage Audits</div>

    <div class="card">
      <h2>Full-Build Coding Model Leaderboard (Rust Engine + Trained NN vs escm + Azahar Bridge)</h2>
      {fb_html}
    </div>

    <div class="card">
      <h2>Head-to-Head Mirrored Battles (Candidate NN vs Reference escm Model)</h2>
      {h2h_html}
    </div>

    <div class="card">
      <h2>Azahar 3DS UDP RPC v2 &harr; ML Bridge Grader</h2>
      {bridge_html}
    </div>

    <div class="card">
      <h2>Rust Engine Differential Oracle &amp; Harness-Timed Throughput</h2>
      {oracle_html}
    </div>

    <div class="card">
      <h2>Showdown Snowball Battle Arena</h2>
      {arena_html}
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
