#!/usr/bin/env node
"use strict";

/**
 * Untouched Pokemon Showdown Gen 7 differential oracle runner (`escm-diff-oracle/1`).
 *
 * Modes:
 *   1. Default (stdin -> stdout): reads {"protocol": "escm-diff-oracle/1", "cases": [...]}
 *      and writes {"protocol": "escm-diff-oracle/1", "engine": "...", "tiers": [...], "traces": [...]}
 *   2. --fuzz <count> --seed <int>: generates <count> fresh, seeded, Gen 7-legal random battles
 *      with opaque case IDs (`fuzz_0000`, ...) so the master seed is never leaked to candidates.
 */

const { Battle, Dex, Teams, TeamValidator } = require("pokemon-showdown");

const PROTOCOL = "escm-diff-oracle/1";
const FORMAT = "gen7customgame@@@!Team Preview";
const TIERS = ["legal", "order", "state", "hidden", "events"];
const GEN7_DEX = Dex.forGen(7);
const GEN7_VALIDATOR = TeamValidator.get("gen7anythinggoes");

function toId(x) {
  return String(x || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function makePrng(seed) {
  let s = BigInt(seed || 1) & 0xffffffffffffffffn;
  if (s === 0n) s = 0x9e3779b97f4a7c15n;
  return () => {
    s ^= s >> 12n;
    s ^= (s << 25n) & 0xffffffffffffffffn;
    s ^= s >> 27n;
    const v = (s * 0x2545f4914f6cdd1dn) & 0xffffffffffffffffn;
    return Number(v >> 11n) / 9007199254740992.0;
  };
}

function legalTokens(battle, sideIdx) {
  const side = battle.sides[sideIdx];
  const req = side.activeRequest;
  if (!req || req.wait) return [];

  const tokens = [];
  if (req.forceSwitch && req.forceSwitch[0]) {
    for (const p of side.pokemon) {
      if (!p.isActive && !p.fainted && p.hp > 0) {
        tokens.push(`switch:${p.name}`);
      }
    }
    return tokens.sort();
  }

  const activeReq = req.active && req.active[0];
  const actMon = side.active[0];
  if (activeReq && activeReq.moves) {
    const disabledSet = new Set();
    if (actMon && actMon.moveSlots) {
      for (const ms of actMon.moveSlots) {
        if (ms.disabled) disabledSet.add(ms.id);
      }
    }
    for (let i = 0; i < activeReq.moves.length; i++) {
      const m = activeReq.moves[i];
      const isDisabled = Boolean(m.disabled || disabledSet.has(m.id));
      if (!isDisabled) {
        tokens.push(`move:${m.id}`);
        if (activeReq.canMegaEvo) {
          tokens.push(`move:${m.id}+mega`);
        }
        if (activeReq.canUltraBurst) {
          tokens.push(`move:${m.id}+ultra`);
        }
      }
      // In Gen 7, Z-Move stays legal even when the base move is disabled (e.g., Disable/Taunt)
      if (activeReq.canZMove && activeReq.canZMove[i]) {
        tokens.push(`move:${m.id}+zmove`);
      }
    }
    if (tokens.length === 0) {
      tokens.push("move:struggle");
    }
    const isTrapped = Boolean(activeReq.trapped || (actMon && actMon.trapped));
    if (!isTrapped) {
      for (const p of side.pokemon) {
        if (!p.isActive && !p.fainted && p.hp > 0) {
          tokens.push(`switch:${p.name}`);
        }
      }
    }
  }
  return tokens.sort();
}

function tokenToShowdownChoice(battle, sideIdx, tok) {
  if (!tok || tok === "pass") return "pass";
  const side = battle.sides[sideIdx];
  if (tok.startsWith("switch:")) {
    const nick = tok.slice("switch:".length);
    const idx = side.pokemon.findIndex((p) => p.name === nick);
    if (idx < 0) throw new Error(`Unknown switch target ${nick} on side ${sideIdx}`);
    return `switch ${idx + 1}`;
  }
  if (tok.startsWith("move:")) {
    const rest = tok.slice("move:".length);
    const [moveId, mod] = rest.split("+");
    if (moveId === "struggle") return "move 1";
    const activeReq = side.activeRequest && side.activeRequest.active && side.activeRequest.active[0];
    if (!activeReq) throw new Error(`No active move request for ${tok}`);
    const idx = activeReq.moves.findIndex((m, i) => {
      if (m.id !== moveId) return false;
      if (mod === "zmove") return Boolean(activeReq.canZMove && activeReq.canZMove[i]);
      return !m.disabled;
    });
    if (idx < 0) {
      if (activeReq.moves.length === 1) return "move 1";
      throw new Error(`Move ${moveId} not legal on side ${sideIdx}`);
    }
    return mod ? `move ${idx + 1} ${mod}` : `move ${idx + 1}`;
  }
  throw new Error(`Unsupported token: ${tok}`);
}

function normalizeEvents(lines) {
  const drop = new Set(["", "t:", "upkeep", "-message", "-hint", "-anim", "gametype", "gen", "tier", "rule", "player", "teamsize", "start"]);
  const out = [];
  for (const raw of lines) {
    if (!raw.startsWith("|")) continue;
    const parts = raw.slice(1).split("|");
    const cmd = parts[0];
    if (drop.has(cmd)) continue;
    const cleaned = parts.map((p, idx) => {
      if (idx === 0) return p;
      const m = p.match(/^(p[12])[a-z]:\s*(.+)$/);
      if (m) return `${m[1]}:${m[2]}`;
      return p;
    });
    out.push("|" + cleaned.join("|"));
  }
  return out;
}

function extractOrder(normEvents) {
  const order = [];
  for (const line of normEvents) {
    const parts = line.slice(1).split("|");
    const cmd = parts[0];
    if (cmd === "move") order.push(`move ${parts[1]} ${toId(parts[2])}`);
    else if (cmd === "switch" || cmd === "drag") order.push(`${cmd} ${parts[1]}`);
    else if (cmd === "cant") order.push(`cant ${parts[1]} ${toId(parts[2])}`);
  }
  return order;
}

function snapshotBattle(battle) {
  const mapSideConditions = (sc) => {
    const out = {};
    for (const k of Object.keys(sc || {}).sort()) {
      out[k] = sc[k].layers || 1;
    }
    return out;
  };
  const mapSideConditionDurs = (sc) => {
    const out = {};
    for (const k of Object.keys(sc || {}).sort()) {
      out[k] = sc[k].duration ?? null;
    }
    return out;
  };

  const snapMonState = (p) => ({
    name: p.name,
    species: p.species.id,
    hp: p.hp,
    maxhp: p.maxhp,
    status: p.status || "",
    fainted: Boolean(p.fainted),
    item: p.item || "",
    ability: p.ability || "",
    types: [...p.types],
    boosts: { ...p.boosts },
    pp: p.moveSlots.map((m) => [m.id, m.pp]),
  });

  const snapMonHidden = (p) => {
    const vols = {};
    for (const k of Object.keys(p.volatiles || {}).sort()) {
      vols[k] = p.volatiles[k].duration ?? null;
    }
    return {
      name: p.name,
      volatiles: vols,
      status_time: p.statusState ? (p.statusState.time ?? p.statusState.stage ?? null) : null,
      last_move: p.lastMove ? p.lastMove.id : null,
      stored_stats: { ...p.storedStats },
    };
  };

  const state = {
    turn: battle.turn,
    ended: Boolean(battle.ended),
    winner: battle.winner || null,
    weather: battle.field.weather || "",
    terrain: battle.field.terrain || "",
    pseudo_weather: Object.keys(battle.field.pseudoWeather || {}).sort(),
    sides: battle.sides.map((s) => ({
      side_conditions: mapSideConditions(s.sideConditions),
      z_move_used: Boolean(s.zMoveUsed),
      active: s.active[0] ? s.active[0].name : null,
      pokemon: s.pokemon.map(snapMonState),
    })),
  };

  const hidden = {
    weather_duration: battle.field.weatherState ? (battle.field.weatherState.duration ?? null) : null,
    terrain_duration: battle.field.terrainState ? (battle.field.terrainState.duration ?? null) : null,
    sides: battle.sides.map((s) => ({
      side_condition_durations: mapSideConditionDurs(s.sideConditions),
      pokemon: s.pokemon.map(snapMonHidden),
    })),
  };

  return { state, hidden };
}

function runCase(c) {
  try {
    const battle = new Battle({ formatid: c.format || FORMAT, seed: c.seed });
    battle.setPlayer("p1", { name: "p1", team: Teams.pack(c.p1) });
    battle.setPlayer("p2", { name: "p2", team: Teams.pack(c.p2) });

    let logCursor = battle.log.length;
    const initEvents = normalizeEvents(battle.log);
    const initSnap = snapshotBattle(battle);
    const steps = [
      {
        choices: [null, null],
        legal: { p1: legalTokens(battle, 0), p2: legalTokens(battle, 1) },
        order: extractOrder(initEvents),
        state: initSnap.state,
        hidden: initSnap.hidden,
        events: initEvents,
      },
    ];

    for (const pair of c.choices || []) {
      if (battle.ended) break;
      const [t1, t2] = pair;
      const c1 = t1 ? tokenToShowdownChoice(battle, 0, t1) : "";
      const c2 = t2 ? tokenToShowdownChoice(battle, 1, t2) : "";
      battle.makeChoices(c1, c2);

      const newLines = battle.log.slice(logCursor);
      logCursor = battle.log.length;
      const ev = normalizeEvents(newLines);
      const snap = snapshotBattle(battle);
      steps.push({
        choices: [t1 || null, t2 || null],
        legal: { p1: legalTokens(battle, 0), p2: legalTokens(battle, 1) },
        order: extractOrder(ev),
        state: snap.state,
        hidden: snap.hidden,
        events: ev,
      });
    }

    return { id: c.id, steps };
  } catch (err) {
    return { id: c.id, steps: [], error: String(err && err.message ? err.message : err) };
  }
}

function getGen7LegalMovesForSpecies(sp) {
  const moveNames = [];
  const seen = new Set();
  let cur = sp;
  let depth = 0;
  while (cur && cur.exists && depth++ < 4) {
    const lsData = GEN7_DEX.species.getLearnsetData(cur.id);
    const ls = lsData && lsData.learnset ? lsData.learnset : null;
    if (ls) {
      for (const [mid, sources] of Object.entries(ls)) {
        if (seen.has(mid)) continue;
        // Require a clean Gen 7 level-up / TM / tutor / egg source so TeamValidator accepts the combo
        const okSrc = Array.isArray(sources) && sources.some((s) => /^7[LMTE]/.test(s));
        if (!okSrc) continue;
        const mv = GEN7_DEX.moves.get(mid);
        if (!mv.exists || mv.isNonstandard || mv.isZ || mv.num < 1 || mv.num > 728) continue;
        if (mv.id === "struggle" || mv.id === "metronome" || mv.id === "assist" || mv.id === "sketch" || mv.id === "hiddenpower") continue;
        seen.add(mid);
        moveNames.push(mv.name);
      }
    }
    cur = cur.prevo ? GEN7_DEX.species.get(cur.prevo) : null;
  }
  return moveNames;
}

function generateFuzzCases(count, masterSeed, maxSteps = 20) {
  const rng = makePrng(masterSeed);
  const speciesPool = [];
  for (const sp of GEN7_DEX.species.all()) {
    if (!sp.exists || sp.num < 1 || sp.num > 807 || sp.isNonstandard || sp.forme || sp.baseStats.hp <= 0) continue;
    const legalMoves = getGen7LegalMovesForSpecies(sp);
    if (legalMoves.length >= 4) {
      speciesPool.push({ sp, legalMoves });
    }
  }

  const itemPool = ["", "", "Leftovers", "Life Orb", "Focus Sash", "Choice Scarf", "Choice Band", "Choice Specs", "Sitrus Berry", "Lum Berry", "Rocky Helmet", "Assault Vest", "Air Balloon"];
  const natures = ["Adamant", "Jolly", "Modest", "Timid", "Bold", "Calm", "Impish", "Careful", "Serious"];

  function randTeam(prefix, size) {
    const team = [];
    const used = new Set();
    let guard = 0;
    while (team.length < size && guard++ < 500) {
      const entry = speciesPool[Math.floor(rng() * speciesPool.length)];
      const sp = entry.sp;
      if (used.has(sp.id)) continue;

      const moves = [];
      const usedMv = new Set();
      let mvGuard = 0;
      while (moves.length < 4 && mvGuard++ < 60) {
        const mv = entry.legalMoves[Math.floor(rng() * entry.legalMoves.length)];
        if (usedMv.has(mv)) continue;
        usedMv.add(mv);
        moves.push(mv);
      }
      if (moves.length < 4) continue;

      const ab = sp.abilities && sp.abilities["0"] ? sp.abilities["0"] : "Pressure";
      const candidateSet = {
        name: `${prefix}${team.length + 1}_${sp.id.slice(0, 8)}`,
        species: sp.name,
        level: 50 + Math.floor(rng() * 25),
        gender: sp.gender || (rng() < 0.5 ? "M" : "F"),
        ability: ab,
        item: itemPool[Math.floor(rng() * itemPool.length)],
        nature: natures[Math.floor(rng() * natures.length)],
        moves,
        evs: { hp: 84, atk: 84, def: 84, spa: 84, spd: 84, spe: 84 },
        ivs: { hp: 31, atk: 31, def: 31, spa: 31, spd: 31, spe: 31 },
        happiness: 255,
      };
      const problems = GEN7_VALIDATOR.validateSet(candidateSet, {});
      if (problems && problems.length > 0) continue;

      used.add(sp.id);
      team.push(candidateSet);
    }
    return team;
  }

  const cases = [];
  const traces = [];
  for (let i = 0; i < count; i++) {
    const seed = [
      Math.floor(rng() * 65535) + 1,
      Math.floor(rng() * 65535) + 1,
      Math.floor(rng() * 65535) + 1,
      Math.floor(rng() * 65535) + 1,
    ];
    const p1 = randTeam("A", 3);
    const p2 = randTeam("B", 3);
    const battle = new Battle({ formatid: FORMAT, seed });
    battle.setPlayer("p1", { name: "p1", team: Teams.pack(p1) });
    battle.setPlayer("p2", { name: "p2", team: Teams.pack(p2) });

    const choices = [];
    for (let s = 0; s < maxSteps && !battle.ended; s++) {
      const l1 = legalTokens(battle, 0);
      const l2 = legalTokens(battle, 1);
      if (l1.length === 0 && l2.length === 0) break;

      const pickValid = (sideIdx, pool) => {
        if (pool.length === 0) return null;
        const sideId = sideIdx === 0 ? "p1" : "p2";
        const shuffled = [...pool];
        for (let k = shuffled.length - 1; k > 0; k--) {
          const j = Math.floor(rng() * (k + 1));
          [shuffled[k], shuffled[j]] = [shuffled[j], shuffled[k]];
        }
        for (const tok of shuffled) {
          const ch = tokenToShowdownChoice(battle, sideIdx, tok);
          if (battle.choose(sideId, ch)) return tok;
        }
        return null;
      };

      const t1 = pickValid(0, l1);
      const t2 = pickValid(1, l2);
      if ((l1.length > 0 && !t1) || (l2.length > 0 && !t2)) break;
      choices.push([t1, t2]);
    }

    // Opaque ID: never embed masterSeed in the case ID sent to candidate engines
    const cObj = {
      id: `fuzz_${String(i).padStart(4, "0")}`,
      format: FORMAT,
      seed,
      p1,
      p2,
      choices,
    };
    cases.push(cObj);
    traces.push(runCase(cObj));
  }
  return { protocol: PROTOCOL, engine: "pokemon-showdown-0.11.11", tiers: TIERS, cases, traces };
}

const args = process.argv.slice(2);
if (args.includes("--fuzz")) {
  const fuzzIdx = args.indexOf("--fuzz");
  const seedIdx = args.indexOf("--seed");
  const stepsIdx = args.indexOf("--steps");
  const count = parseInt(args[fuzzIdx + 1] || "20", 10);
  const seed = seedIdx >= 0 ? parseInt(args[seedIdx + 1], 10) : 424242;
  const steps = stepsIdx >= 0 ? parseInt(args[stepsIdx + 1], 10) : 16;
  const out = generateFuzzCases(count, seed, steps);
  process.stdout.write(JSON.stringify(out) + "\n");
} else {
  let raw = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    raw += chunk;
  });
  process.stdin.on("end", () => {
    if (!raw.trim()) return;
    const payload = JSON.parse(raw);
    const cases = payload.cases || [];
    const traces = cases.map(runCase);
    process.stdout.write(
      JSON.stringify({
        protocol: PROTOCOL,
        engine: "pokemon-showdown-0.11.11",
        tiers: TIERS,
        traces,
      }) + "\n"
    );
  });
}
