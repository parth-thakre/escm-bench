#!/usr/bin/env node
"use strict";

/**
 * Untouched Pokemon Showdown Gen 7 battle referee for escm-bench Track 1 / Track 4 / Track 5.
 *
 * Line-delimited JSON-RPC over stdin/stdout:
 *   -> {"cmd": "init", "seed": [1,2,3,4], "p1": [...], "p2": [...], "player_policy": "conservative_nuzlocke", "p1_aura": {...}}
 *   <- {"ok": true, "done": false, "state": {...}, "legal_actions": [...], "metrics": {...}}
 *   -> {"cmd": "step", "action_id": 0}
 *   <- {"ok": true, "done": ..., "state": {...}, "legal_actions": [...], "metrics": {...}, "step_reward": ...}
 *
 * Side 0 (p1) = AI Trainer under evaluation.
 * Side 1 (p2) = Nuzlocke Player (controlled by a deterministic baseline policy).
 */

const readline = require("readline");
const { Battle, Dex, Teams } = require("pokemon-showdown");

const GEN7_DEX = Dex.forGen(7);
const FORMAT = "gen7customgame@@@!Team Preview";

const REWARD_CFG = {
  playerKoWeight: 4.0,
  snowballChainBonus: 1.5,
  wipeBonus: 6.0,
  playerHpDamageWeight: 0.9,
  statusInflictWeight: 0.45,
  itemStripWeight: 0.35,
  aiPreservationWeight: 0.25,
  turnPenalty: 0.01,
  gamma: 0.99,
};

const CONSUMABLE_ITEMS = new Set([
  "focussash", "sitrusberry", "lumberry", "aguavberry", "figyberry", "iapapaberry",
  "magoberry", "wikiberry", "oranberry", "salacberry", "liechiberry", "petayaberry",
  "apicotberry", "ganlonberry", "custapberry", "whiteherb", "powerherb", "mentalherb",
  "airballoon", "weaknesspolicy", "redcard", "ejectbutton", "lifeorb",
]);

function makePrng(seedArr) {
  let s =
    (BigInt(seedArr[0] || 1) << 48n) ^
    (BigInt(seedArr[1] || 2) << 32n) ^
    (BigInt(seedArr[2] || 3) << 16n) ^
    BigInt(seedArr[3] || 4);
  if (s === 0n) s = 0x9e3779b97f4a7c15n;
  return () => {
    s ^= s >> 12n;
    s ^= (s << 25n) & 0xffffffffffffffffn;
    s ^= s >> 27n;
    const v = (s * 0x2545f4914f6cdd1dn) & 0xffffffffffffffffn;
    return Number(v >> 11n) / 9007199254740992.0;
  };
}

function typeEffectiveness(moveType, defTypes) {
  let mult = 1.0;
  for (const dt of defTypes || []) {
    const tdata = GEN7_DEX.types.get(dt);
    if (!tdata || !tdata.damageTaken) continue;
    const code = tdata.damageTaken[moveType];
    if (code === 1) mult *= 2.0;
    else if (code === 2) mult *= 0.5;
    else if (code === 3) mult *= 0.0;
  }
  return mult;
}

function estimateDamage(battle, atkSideIdx, defSideIdx, moveId, isZ = false) {
  const atkMon = battle.sides[atkSideIdx].active[0];
  const defMon = battle.sides[defSideIdx].active[0];
  if (!atkMon || !defMon || atkMon.fainted || defMon.fainted) return 0;
  const move = GEN7_DEX.moves.get(moveId);
  if (!move || !move.exists || move.category === "Status") return 0;

  let bp = move.basePower || 0;
  if (isZ) {
    bp = move.zMove && move.zMove.basePower ? move.zMove.basePower : Math.max(100, Math.min(200, bp * 1.5));
  }
  if (bp <= 0) return 0;

  const eff = typeEffectiveness(move.type, defMon.types);
  if (eff === 0) return 0;
  if (move.type === "Ground" && (defMon.ability === "levitate" || defMon.item === "airballoon")) return 0;

  const isPhys = move.category === "Physical";
  const atkStat = Math.max(1, atkMon.getStat(isPhys ? "atk" : "spa", false, true));
  const defStat = Math.max(1, defMon.getStat(isPhys ? "def" : "spd", false, true));
  const stab = atkMon.types.includes(move.type) ? 1.5 : 1.0;
  const lvl = atkMon.level || 50;
  const base = Math.floor(Math.floor(Math.floor((2 * lvl) / 5 + 2) * bp * atkStat / defStat) / 50) + 2;
  return Math.max(1, Math.floor(base * stab * eff * 0.925));
}

function buildLegalActions(battle, sideIdx) {
  const side = battle.sides[sideIdx];
  const req = side.activeRequest;
  if (!req || req.wait) return [];

  const actions = [];
  if (req.forceSwitch && req.forceSwitch[0]) {
    for (let i = 0; i < side.pokemon.length; i++) {
      const p = side.pokemon[i];
      if (!p.isActive && !p.fainted && p.hp > 0) {
        actions.push({
          id: actions.length,
          token: `switch ${i + 1}`,
          kind: "switch",
          slot: i + 1,
          species: p.species.name,
          hp_pct: Math.round((100 * p.hp) / Math.max(1, p.maxhp)),
          status: p.status || null,
          label: `Switch to ${p.species.name} (${Math.round((100 * p.hp) / Math.max(1, p.maxhp))}% HP${p.status ? ", " + p.status : ""})`,
        });
      }
    }
    return actions;
  }

  const activeReq = req.active && req.active[0];
  const actMon = side.active[0];
  if (activeReq && activeReq.moves) {
    for (let i = 0; i < activeReq.moves.length; i++) {
      const m = activeReq.moves[i];
      const md = GEN7_DEX.moves.get(m.id);
      const baseLabel = `${m.move} (${md.type || "?"}, ${md.category || "?"}, BP ${md.basePower || 0}, PP ${m.pp ?? "?"}/${m.maxpp ?? "?"})`;

      if (!m.disabled) {
        actions.push({
          id: actions.length,
          token: `move ${i + 1}`,
          kind: "move",
          slot: i + 1,
          move: m.id,
          label: baseLabel,
        });
        if (activeReq.canMegaEvo) {
          actions.push({
            id: actions.length,
            token: `move ${i + 1} mega`,
            kind: "mega",
            slot: i + 1,
            move: m.id,
            label: `Mega Evolve + ${baseLabel}`,
          });
        }
        if (activeReq.canUltraBurst) {
          actions.push({
            id: actions.length,
            token: `move ${i + 1} ultra`,
            kind: "ultra",
            slot: i + 1,
            move: m.id,
            label: `Ultra Burst + ${baseLabel}`,
          });
        }
      }

      // In Gen 7, a Z-Move can still be legal even when the base move is disabled (e.g. Disable / Taunt)
      if (activeReq.canZMove && activeReq.canZMove[i]) {
        const zm = activeReq.canZMove[i];
        actions.push({
          id: actions.length,
          token: `move ${i + 1} zmove`,
          kind: "zmove",
          slot: i + 1,
          move: zm.move || m.id,
          label: `Z-Move: ${zm.move || m.move} (from ${m.move})`,
        });
      }
    }

    // Only use activeReq.trapped (NEVER actMon.trapped === "hidden"), so Shadow Tag / Arena Trap / Magnet Pull
    // are not leaked to the AI before it attempts to switch.
    if (!activeReq.trapped) {
      for (let i = 0; i < side.pokemon.length; i++) {
        const p = side.pokemon[i];
        if (!p.isActive && !p.fainted && p.hp > 0) {
          actions.push({
            id: actions.length,
            token: `switch ${i + 1}`,
            kind: "switch",
            slot: i + 1,
            species: p.species.name,
            hp_pct: Math.round((100 * p.hp) / Math.max(1, p.maxhp)),
            status: p.status || null,
            label: `Switch to ${p.species.name} (${Math.round((100 * p.hp) / Math.max(1, p.maxhp))}% HP${p.status ? ", " + p.status : ""})`,
          });
        }
      }
    }
  }
  return actions;
}

function choosePlayerAction(battle, policy, rng) {
  const actions = buildLegalActions(battle, 1);
  if (actions.length === 0) return null;
  if (actions.length === 1) return actions[0].token;

  if (policy === "random") {
    return actions[Math.floor(rng() * actions.length)].token;
  }

  const p2 = battle.sides[1];
  const p1 = battle.sides[0];
  const act = p2.active[0];
  const opp = p1.active[0];

  const switches = actions.filter((a) => a.kind === "switch");
  const moves = actions.filter((a) => a.kind !== "switch");

  function bestSwitch() {
    let best = switches[0];
    let bestScore = -1e9;
    for (const sw of switches) {
      const mon = p2.pokemon[sw.slot - 1];
      let off = 0;
      for (const ms of mon.moveSlots) {
        const md = GEN7_DEX.moves.get(ms.id);
        if (md.category !== "Status" && opp) {
          off = Math.max(off, typeEffectiveness(md.type, opp.types) * ((md.basePower || 60) / 100));
        }
      }
      let defThreat = 1.0;
      if (opp) {
        for (const t of opp.types) {
          defThreat = Math.max(defThreat, typeEffectiveness(t, mon.types));
        }
      }
      const hpFrac = mon.hp / Math.max(1, mon.maxhp);
      const score = off * 1.1 - defThreat * 0.9 + hpFrac * 1.2;
      if (score > bestScore) {
        bestScore = score;
        best = sw;
      }
    }
    return { action: best, score: bestScore };
  }

  if (moves.length === 0) {
    return bestSwitch().action.token;
  }

  if (policy === "noisy_human" && rng() < 0.12) {
    return actions[Math.floor(rng() * actions.length)].token;
  }

  let bestMove = moves[0];
  let bestMoveScore = -1e9;
  const oppHp = opp && !opp.fainted ? Math.max(1, opp.hp) : 1;

  for (const mv of moves) {
    const md = GEN7_DEX.moves.get(mv.move);
    let score = 0;
    if (md.category === "Status") {
      if (md.heal && act.hp / Math.max(1, act.maxhp) < 0.48) score = 0.9;
      else if (md.boosts && (md.target === "self" || md.target === "adjacentAllyOrSelf") && act.hp / Math.max(1, act.maxhp) > 0.65) score = 0.45;
      else if (md.status && opp && !opp.status) score = 0.4;
      else score = 0.08;
    } else {
      const acc = mv.kind === "zmove" || md.accuracy === true || md.accuracy === 0 ? 1.0 : md.accuracy / 100;
      const dmg = estimateDamage(battle, 1, 0, mv.move, mv.kind === "zmove");
      score = Math.min(1.5, dmg / oppHp) * acc + (dmg >= oppHp ? 0.65 : 0) + (md.priority > 0 && dmg >= oppHp ? 0.3 : 0);
      if (mv.kind === "zmove" && dmg < oppHp * 0.75) score -= 0.25;
      if (mv.kind === "mega" || mv.kind === "ultra") score += 0.15;
    }
    if (policy === "noisy_human") score += (rng() - 0.5) * 0.25;
    if (score > bestMoveScore) {
      bestMoveScore = score;
      bestMove = mv;
    }
  }

  if (policy === "conservative_nuzlocke" && switches.length > 0 && act && opp && !act.fainted && !opp.fainted) {
    let maxOppDmg = 0;
    for (const ms of opp.moveSlots) {
      maxOppDmg = Math.max(maxOppDmg, estimateDamage(battle, 0, 1, ms.id, false));
    }
    const slower = act.getStat("spe", false, true) < opp.getStat("spe", false, true);
    const threatened = maxOppDmg >= act.hp * 0.78;
    const canOhko = estimateDamage(battle, 1, 0, bestMove.move, bestMove.kind === "zmove") >= opp.hp;
    if (threatened && (slower || !canOhko)) {
      const sw = bestSwitch();
      if (sw.score > 0.4) return sw.action.token;
    }
  }

  return bestMove.token;
}

/**
 * Filter raw Showdown log to a given side's legal perspective ("p1" or "p2"):
 * - For `|split|<viewerSide>`, keep the private line (next line) and skip the public line.
 * - For `|split|<oppSide>`, skip the private exact-HP line and keep ONLY the public % line.
 */
function maskOppExactHp(line, oppSideTag) {
  // Scrub any exact opponent HP fraction (e.g. `95/135`) down to percentage (`71/100`) under fog of war
  const parts = line.split("|");
  if (parts.length < 4) return line;
  const cmd = parts[1];
  const target = parts[2] || "";
  if (!target.startsWith(oppSideTag)) return line;
  const hpIdx = cmd === "switch" || cmd === "drag" || cmd === "replace" ? 4 : 3;
  if (parts.length <= hpIdx) return line;
  parts[hpIdx] = parts[hpIdx].replace(/^(\d+)\/(\d+)/, (_, curStr, maxStr) => {
    const cur = parseInt(curStr, 10);
    const max = Math.max(1, parseInt(maxStr, 10));
    if (cur === 0) return "0";
    if (max === 100) return `${cur}/100`;
    return `${Math.max(1, Math.ceil((100 * cur) / max))}/100`;
  });
  return parts.join("|");
}

function filterLogForSide(rawLog, viewerSideTag, fogOfWar) {
  const oppSideTag = viewerSideTag === "p1" ? "p2" : "p1";
  const out = [];
  for (let i = 0; i < rawLog.length; i++) {
    const line = rawLog[i];
    if (!line || !line.startsWith("|")) continue;
    if (line.startsWith("|t:")) continue;
    if (line.startsWith("|split|")) {
      const targetSide = line.slice("|split|".length).trim();
      const privLine = rawLog[i + 1] || "";
      const pubLine = rawLog[i + 2] || "";
      if (!fogOfWar || targetSide === viewerSideTag) {
        if (privLine.startsWith("|")) out.push(privLine);
      } else {
        if (pubLine.startsWith("|")) out.push(maskOppExactHp(pubLine, oppSideTag));
      }
      i += 2;
      continue;
    }
    out.push(fogOfWar ? maskOppExactHp(line, oppSideTag) : line);
  }
  return out;
}

class FogTracker {
  constructor(battle, oppSideIdx = 1) {
    this.oppSideIdx = oppSideIdx;
    this.oppSideTag = oppSideIdx === 0 ? "p1" : "p2";
    this.revealedIds = new Set(); // stable _benchId of opponent mons revealed
    this.revealedMoves = new Map(); // _benchId -> Set(moveId)
    this.revealedItems = new Map(); // _benchId -> string
    this.revealedAbilities = new Map(); // _benchId -> string
    this.activeOppBenchId = null;
    this.lastLogIdx = 0;
    for (const p of battle.sides[this.oppSideIdx].pokemon) {
      this.revealedMoves.set(p._benchId, new Set());
    }
  }

  _extractSideAndNick(field) {
    if (!field) return null;
    const m = String(field).match(/^(p[12])[a-z]?:\s*(.+)$/);
    return m ? { side: m[1], nick: m[2].trim() } : null;
  }

  _findOppByNick(battle, nick, preferActiveBenchId = null) {
    if (!nick) return null;
    const opp = battle.sides[this.oppSideIdx];
    const matches = opp.pokemon.filter((p) => p.name === nick);
    if (matches.length === 0) return null;
    if (matches.length === 1) return matches[0];
    if (preferActiveBenchId !== null) {
      const activeMatch = matches.find((p) => p._benchId === preferActiveBenchId);
      if (activeMatch) return activeMatch;
    }
    const curAct = opp.active[0];
    const curVis = curAct ? (curAct.illusion || curAct) : null;
    if (curVis && curVis.name === nick) return curVis;
    return matches.find((p) => !this.revealedIds.has(p._benchId)) || matches[0];
  }

  _resolveActorBenchId(battle, field, isSwitchEvent = false) {
    const parsed = this._extractSideAndNick(field);
    if (!parsed || parsed.side !== this.oppSideTag) return null;
    if (!isSwitchEvent && this.activeOppBenchId !== null) {
      const cur = battle.sides[this.oppSideIdx].pokemon.find((p) => p._benchId === this.activeOppBenchId);
      if (cur && cur.name === parsed.nick) return cur._benchId;
    }
    const mon = this._findOppByNick(battle, parsed.nick, isSwitchEvent ? null : this.activeOppBenchId);
    return mon ? mon._benchId : null;
  }

  sync(battle) {
    const opp = battle.sides[this.oppSideIdx];
    for (let i = this.lastLogIdx; i < battle.log.length; i++) {
      const line = battle.log[i];
      if (!line || !line.startsWith("|")) continue;
      if (line.startsWith("|split|")) {
        // Process only the public line (i + 2) so each event runs once
        i += 1;
        continue;
      }
      const parts = line.split("|");
      const cmd = parts[1];

      if (cmd === "switch" || cmd === "drag" || cmd === "replace") {
        const parsed = this._extractSideAndNick(parts[2]);
        if (parsed && parsed.side === this.oppSideTag) {
          const matches = opp.pokemon.filter((p) => p.name === parsed.nick);
          let chosen = null;
          if (matches.length === 1) {
            chosen = matches[0];
          } else if (matches.length > 1) {
            // Avoid re-picking the mon that just switched out when two opponent mons share a nickname
            chosen = matches.find((p) => p._benchId !== this.activeOppBenchId && !p.fainted)
              || matches.find((p) => p._benchId !== this.activeOppBenchId)
              || matches[0];
          }
          if (chosen) {
            this.activeOppBenchId = chosen._benchId;
            this.revealedIds.add(chosen._benchId);
          }
        }
        continue;
      }

      const targetBenchId = this._resolveActorBenchId(battle, parts[2], false);

      if (cmd === "move" && targetBenchId !== null) {
        this.revealedIds.add(targetBenchId);
        const mvId = GEN7_DEX.moves.get(parts[3]).id;
        if (mvId && this.revealedMoves.has(targetBenchId)) {
          this.revealedMoves.get(targetBenchId).add(mvId);
        }
      } else if (cmd === "-ability" && targetBenchId !== null && parts[3]) {
        const abId = GEN7_DEX.abilities.get(parts[3]).id;
        if (abId) this.revealedAbilities.set(targetBenchId, abId);
      } else if (cmd === "-activate" && targetBenchId !== null && parts[3] && parts[3].startsWith("ability:")) {
        const abId = GEN7_DEX.abilities.get(parts[3].slice("ability:".length).trim()).id;
        if (abId) this.revealedAbilities.set(targetBenchId, abId);
      } else if ((cmd === "-item" || cmd === "-enditem") && targetBenchId !== null) {
        this.revealedItems.set(targetBenchId, parts[3] || "consumed");
      }

      // Attribute `[from] ability: <Name>` to `[of] pXa: Nick` if present (otherwise to parts[2]), ONLY if oppSide
      let fromAbility = null;
      let ofField = null;
      for (let k = 3; k < parts.length; k++) {
        const tag = parts[k] || "";
        if (tag.startsWith("[from] ability:")) {
          fromAbility = GEN7_DEX.abilities.get(tag.slice("[from] ability:".length).trim()).id;
        } else if (tag.startsWith("[of] ")) {
          ofField = tag.slice("[of] ".length).trim();
        }
      }
      if (fromAbility) {
        const sourceBenchId = ofField
          ? this._resolveActorBenchId(battle, ofField, false)
          : targetBenchId;
        if (sourceBenchId !== null) {
          this.revealedAbilities.set(sourceBenchId, fromAbility);
        }
      }
    }
    this.lastLogIdx = battle.log.length;
    const act = opp.active[0];
    if (act) {
      const vis = act.illusion || act;
      this.activeOppBenchId = vis._benchId;
      this.revealedIds.add(vis._benchId);
    }
  }
}

function chainValue(kos) {
  return kos * Math.max(0, kos - 1) * 0.5 * REWARD_CFG.snowballChainBonus;
}

function battlePressure(battle, scorerSideIdx = 0) {
  if (battle.ended) return 0.0;
  const ai = battle.sides[scorerSideIdx];
  const pl = battle.sides[1 - scorerSideIdx];
  let aiHpSum = 0;
  for (const p of ai.pokemon) {
    if (!p.fainted && p.hp > 0) aiHpSum += p.hp / Math.max(1, p.maxhp);
  }
  let pressure = aiHpSum * REWARD_CFG.aiPreservationWeight;
  for (const mon of pl.pokemon) {
    if (mon.fainted || mon.hp <= 0) continue;
    const hpFrac = mon.hp / Math.max(1, mon.maxhp);
    pressure += (1.0 - hpFrac) * REWARD_CFG.playerHpDamageWeight;
    if (mon.status) pressure += REWARD_CFG.statusInflictWeight;
    const orig = mon._origItem || "";
    if (orig && CONSUMABLE_ITEMS.has(orig) && mon.item !== orig) {
      pressure += REWARD_CFG.itemStripWeight;
    }
  }
  return Math.max(-2.0, Math.min(2.0, pressure));
}

function snapReward(battle, scorerSideIdx = 0) {
  const pl = battle.sides[1 - scorerSideIdx];
  const aliveById = {};
  let plKos = 0;
  let plAlive = 0;
  for (const p of pl.pokemon) {
    const alive = !p.fainted && p.hp > 0;
    aliveById[p._benchId] = alive;
    if (alive) plAlive++;
    else plKos++;
  }
  return {
    ended: Boolean(battle.ended),
    plKos,
    plAlive,
    aliveById,
    pressure: battlePressure(battle, scorerSideIdx),
  };
}

function computeStepReward(prev, next) {
  if (prev.ended) return 0.0;
  let eventRew = 0.0;
  let newKos = 0;
  for (const id of Object.keys(prev.aliveById)) {
    if (prev.aliveById[id] && !next.aliveById[id]) {
      newKos++;
      eventRew += REWARD_CFG.playerKoWeight;
    }
  }
  eventRew += chainValue(prev.plKos + newKos) - chainValue(prev.plKos);
  if (next.plAlive === 0 && prev.plAlive > 0) {
    eventRew += REWARD_CFG.wipeBonus;
  }
  const shaping = (next.ended ? 0.0 : REWARD_CFG.gamma * next.pressure) - prev.pressure;
  return eventRew + shaping - REWARD_CFG.turnPenalty;
}

function projectStateForSide(battle, viewerSideIdx, oppFog, fogOfWar) {
  const viewerTag = viewerSideIdx === 0 ? "p1" : "p2";
  const ai = battle.sides[viewerSideIdx];
  const pl = battle.sides[1 - viewerSideIdx];
  const aiAct = ai.active[0];
  const plAct = pl.active[0];

  const nonZeroBoosts = (b) => {
    const out = {};
    for (const [k, v] of Object.entries(b || {})) if (v) out[k] = v;
    return out;
  };

  const formatMonOwn = (p) => ({
    species: p.species.name,
    level: p.level,
    hp: p.hp,
    maxhp: p.maxhp,
    hp_pct: Math.round((100 * p.hp) / Math.max(1, p.maxhp)),
    fainted: p.fainted,
    active: p.isActive,
    types: p.types,
    status: p.status || null,
    item: p.item || null,
    ability: p.ability || null,
    boosts: p.isActive ? nonZeroBoosts(p.boosts) : {},
    moves: p.moveSlots.map((m) => ({ id: m.id, move: m.move, pp: m.pp, maxpp: m.maxpp })),
  });

  const formatPlayerMon = (p) => {
    const disguised = Boolean(fogOfWar && p.isActive && p.illusion);
    const vis = disguised ? p.illusion : p;
    const revealedMoves = Array.from(oppFog.revealedMoves.get(vis._benchId) || []);
    return {
      species: vis.species.name,
      level: vis.level,
      hp_pct: Math.round((100 * p.hp) / Math.max(1, p.maxhp)),
      fainted: p.fainted,
      active: p.isActive,
      types: vis.types,
      status: p.status || null,
      boosts: p.isActive ? nonZeroBoosts(p.boosts) : {},
      revealed_moves: fogOfWar ? revealedMoves : p.moveSlots.map((m) => m.id),
      revealed_item: fogOfWar ? (oppFog.revealedItems.get(vis._benchId) || null) : (p.item || null),
      revealed_ability: fogOfWar ? (disguised ? null : (oppFog.revealedAbilities.get(p._benchId) || null)) : (p.ability || null),
    };
  };

  const plTeam = [];
  const seenBenchIds = new Set();
  for (const p of pl.pokemon) {
    if (fogOfWar) {
      if (p.isActive && p.illusion) {
        plTeam.push(formatPlayerMon(p));
        seenBenchIds.add(p.illusion._benchId);
        continue;
      }
      if (!p.isActive && !oppFog.revealedIds.has(p._benchId)) {
        continue;
      }
      if (seenBenchIds.has(p._benchId)) continue;
      seenBenchIds.add(p._benchId);
      plTeam.push(formatPlayerMon(p));
    } else {
      plTeam.push(formatPlayerMon(p));
    }
  }
  const hiddenBenchCount = fogOfWar ? Math.max(0, pl.pokemon.length - plTeam.length) : 0;

  return {
    turn: battle.turn,
    ended: Boolean(battle.ended),
    fog_of_war: fogOfWar,
    viewer_side: viewerTag,
    field: {
      weather: battle.field.weather || null,
      terrain: battle.field.terrain || null,
      pseudo_weather: Object.keys(battle.field.pseudoWeather || {}),
      ai_side_conditions: Object.keys(ai.sideConditions || {}),
      player_side_conditions: Object.keys(pl.sideConditions || {}),
    },
    ai_active: aiAct ? formatMonOwn(aiAct) : null,
    ai_team: ai.pokemon.map(formatMonOwn),
    player_active: plAct ? plTeam.find((m) => m.active) || null : null,
    player_revealed_team: plTeam,
    player_unrevealed_bench_count: hiddenBenchCount,
    player_alive_count: pl.pokemon.filter((p) => !p.fainted && p.hp > 0).length,
    player_total_count: pl.pokemon.length,
    recent_log: filterLogForSide(battle.log, viewerTag, fogOfWar).slice(-14),
  };
}

function getMetrics(battle, totalReward) {
  const ai = battle.sides[0];
  const pl = battle.sides[1];
  const playerKos = pl.pokemon.filter((p) => p.fainted || p.hp <= 0).length;
  const aiKos = ai.pokemon.filter((p) => p.fainted || p.hp <= 0).length;
  return {
    turn: battle.turn,
    ended: Boolean(battle.ended),
    winner: battle.winner || null,
    ai_won: battle.winner === "AI",
    player_kos: playerKos,
    player_team_size: pl.pokemon.length,
    ai_kos: aiKos,
    ai_team_size: ai.pokemon.length,
    blood_drawn: playerKos >= 1,
    multi_ko: playerKos >= 2,
    wiped: playerKos === pl.pokemon.length,
    snowball_reward: Number(totalReward.toFixed(4)),
  };
}

function getH2HMetrics(battle, totalRewP1, totalRewP2) {
  const s1 = battle.sides[0];
  const s2 = battle.sides[1];
  const p2Kos = s2.pokemon.filter((p) => p.fainted || p.hp <= 0).length; // KOs inflicted BY p1
  const p1Kos = s1.pokemon.filter((p) => p.fainted || p.hp <= 0).length; // KOs inflicted BY p2
  let winnerSide = null;
  if (battle.winner === "P1") winnerSide = "p1";
  else if (battle.winner === "P2") winnerSide = "p2";
  return {
    turn: battle.turn,
    ended: Boolean(battle.ended),
    winner: winnerSide,
    p1_kos_inflicted: p2Kos,
    p2_kos_inflicted: p1Kos,
    p1_team_size: s1.pokemon.length,
    p2_team_size: s2.pokemon.length,
    p1_wiped_opp: p2Kos === s2.pokemon.length,
    p2_wiped_opp: p1Kos === s1.pokemon.length,
    p1_snowball_reward: Number(totalRewP1.toFixed(4)),
    p2_snowball_reward: Number(totalRewP2.toFixed(4)),
  };
}

let battle = null;
let battleMode = "single";
let fog = null;
let fogP2 = null;
let playerPolicy = "conservative_nuzlocke";
let fogOfWar = true;
let rng = null;
let totalReward = 0.0;
let totalRewardP2 = 0.0;
let prevSnap = null;
let prevSnapP2 = null;
let maxTurns = 120;

function pendingH2HSides() {
  if (!battle || battle.ended || battle.turn > maxTurns) return [];
  const pending = [];
  for (const [idx, tag] of [[0, "p1"], [1, "p2"]]) {
    const side = battle.sides[idx];
    const req = side.activeRequest;
    if (req && !req.wait && !side.isChoiceDone()) {
      const legal = buildLegalActions(battle, idx);
      if (legal.length > 0) pending.push(tag);
    }
  }
  return pending;
}

function buildH2HPayload(trappedSides = []) {
  const pending = pendingH2HSides();
  const legalP1 = pending.includes("p1") ? buildLegalActions(battle, 0) : [];
  const legalP2 = pending.includes("p2") ? buildLegalActions(battle, 1) : [];
  return {
    ok: true,
    mode: "h2h",
    done: Boolean(battle.ended || battle.turn > maxTurns || pending.length === 0),
    pending_sides: pending,
    trapped_sides: trappedSides,
    states: {
      p1: projectStateForSide(battle, 0, fog, fogOfWar),
      p2: projectStateForSide(battle, 1, fogP2, fogOfWar),
    },
    legal_actions: {
      p1: legalP1,
      p2: legalP2,
    },
    metrics: getH2HMetrics(battle, totalReward, totalRewardP2),
  };
}

function commitP2Choice(battle, policy, rng) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const p2Req = battle.sides[1].activeRequest;
    if (!p2Req || p2Req.wait) return true;
    const p2Choice = choosePlayerAction(battle, policy, rng);
    if (!p2Choice) return false;
    if (battle.choose("p2", p2Choice)) return true;
  }
  return battle.choose("p2", "default");
}

function advanceUntilAiDecisionOrEnd() {
  let guard = 0;
  while (!battle.ended && battle.turn <= maxTurns && guard++ < 400) {
    const p1Req = battle.sides[0].activeRequest;
    const p2Req = battle.sides[1].activeRequest;
    const p1Needs = p1Req && !p1Req.wait;
    const p2Needs = p2Req && !p2Req.wait;

    if (p1Needs) break;
    if (p2Needs) {
      if (!commitP2Choice(battle, playerPolicy, rng)) break;
      fog.sync(battle);
      const nextSnap = snapReward(battle, 0);
      totalReward += computeStepReward(prevSnap, nextSnap);
      prevSnap = nextSnap;
    } else {
      break;
    }
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch (err) {
    process.stdout.write(JSON.stringify({ ok: false, error: `Invalid JSON: ${err.message}` }) + "\n");
    return;
  }

  try {
    if (req.cmd === "init") {
      const seed = req.seed || [1, 2, 3, 4];
      battleMode = req.mode === "h2h" ? "h2h" : "single";
      playerPolicy = req.player_policy || "conservative_nuzlocke";
      fogOfWar = req.fog_of_war !== false;
      maxTurns = req.max_turns || 120;
      rng = makePrng(seed);
      totalReward = 0.0;
      totalRewardP2 = 0.0;

      battle = new Battle({ formatid: FORMAT, seed });
      battle.setPlayer("p1", { name: battleMode === "h2h" ? "P1" : "AI", team: Teams.pack(req.p1) });
      battle.setPlayer("p2", { name: battleMode === "h2h" ? "P2" : "Player", team: Teams.pack(req.p2) });

      // Assign stable immutable bench IDs before any switches reorder side.pokemon
      for (const side of battle.sides) {
        side.pokemon.forEach((p, idx) => {
          p._benchId = idx;
          p._origItem = p.item || "";
        });
      }

      // Optional USUM Totem / Ultra Necrozma aura boosts on lead p1 (and optional p2_aura)
      if (req.p1_aura && battle.sides[0].active[0]) {
        battle.boost(req.p1_aura, battle.sides[0].active[0], battle.sides[0].active[0], null, true);
      }
      if (req.p2_aura && battle.sides[1].active[0]) {
        battle.boost(req.p2_aura, battle.sides[1].active[0], battle.sides[1].active[0], null, true);
      }

      fog = new FogTracker(battle, 1);
      fog.sync(battle);
      prevSnap = snapReward(battle, 0);

      if (battleMode === "h2h") {
        fogP2 = new FogTracker(battle, 0);
        fogP2.sync(battle);
        prevSnapP2 = snapReward(battle, 1);
        process.stdout.write(JSON.stringify(buildH2HPayload()) + "\n");
        return;
      }

      fogP2 = null;
      prevSnapP2 = null;
      advanceUntilAiDecisionOrEnd();

      const legal = battle.ended || battle.turn > maxTurns ? [] : buildLegalActions(battle, 0);
      process.stdout.write(
        JSON.stringify({
          ok: true,
          done: Boolean(battle.ended || battle.turn > maxTurns || legal.length === 0),
          state: projectStateForSide(battle, 0, fog, fogOfWar),
          legal_actions: legal,
          metrics: getMetrics(battle, totalReward),
        }) + "\n"
      );
      return;
    }

    if (req.cmd === "step") {
      if (!battle) throw new Error("Battle not initialized");

      if (battleMode === "h2h") {
        const actions = req.actions || {};
        const pending = pendingH2HSides();
        const trappedSides = [];
        const beforeTurn = battle.turn;
        const beforeLogLen = battle.log.length;

        for (const tag of pending) {
          const sideIdx = tag === "p1" ? 0 : 1;
          const actId = actions[tag];
          const legal = buildLegalActions(battle, sideIdx);
          const chosen = legal.find((a) => a.id === actId);
          if (!chosen) {
            process.stdout.write(JSON.stringify({ ok: false, error: `Illegal action_id ${actId} for ${tag}` }) + "\n");
            return;
          }
          if (!battle.choose(tag, chosen.token)) {
            trappedSides.push(tag);
          }
        }

        // Sync fog & rewards whenever Showdown actually progressed the battle
        if (battle.turn !== beforeTurn || battle.log.length !== beforeLogLen || battle.ended) {
          fog.sync(battle);
          fogP2.sync(battle);
          const nextSnap1 = snapReward(battle, 0);
          totalReward += computeStepReward(prevSnap, nextSnap1);
          prevSnap = nextSnap1;
          const nextSnap2 = snapReward(battle, 1);
          totalRewardP2 += computeStepReward(prevSnapP2, nextSnap2);
          prevSnapP2 = nextSnap2;
        }

        process.stdout.write(JSON.stringify(buildH2HPayload(trappedSides)) + "\n");
        return;
      }

      const legal = buildLegalActions(battle, 0);
      const chosen = legal.find((a) => a.id === req.action_id);
      if (!chosen) {
        process.stdout.write(JSON.stringify({ ok: false, error: `Illegal action_id ${req.action_id}` }) + "\n");
        return;
      }

      const beforeRew = totalReward;

      // Handle hidden trapping (Shadow Tag / Arena Trap / Magnet Pull) faithfully:
      // Showdown rejects the switch and updates activeRequest with trapped=true, so we return
      // the newly revealed legal actions (without switches) for the AI to pick a move on the same turn.
      if (!battle.choose("p1", chosen.token)) {
        const updatedLegal = battle.ended || battle.turn > maxTurns ? [] : buildLegalActions(battle, 0);
        process.stdout.write(
          JSON.stringify({
            ok: true,
            trapped_retry: true,
            done: Boolean(battle.ended || battle.turn > maxTurns || updatedLegal.length === 0),
            step_reward: 0.0,
            state: projectStateForSide(battle, 0, fog, fogOfWar),
            legal_actions: updatedLegal,
            metrics: getMetrics(battle, totalReward),
          }) + "\n"
        );
        return;
      }
      if (!commitP2Choice(battle, playerPolicy, rng)) {
        process.stdout.write(JSON.stringify({ ok: false, error: "Failed to commit player (p2) choice" }) + "\n");
        return;
      }
      fog.sync(battle);
      const nextSnap = snapReward(battle, 0);
      totalReward += computeStepReward(prevSnap, nextSnap);
      prevSnap = nextSnap;

      advanceUntilAiDecisionOrEnd();
      const nextLegal = battle.ended || battle.turn > maxTurns ? [] : buildLegalActions(battle, 0);
      process.stdout.write(
        JSON.stringify({
          ok: true,
          done: Boolean(battle.ended || battle.turn > maxTurns || nextLegal.length === 0),
          step_reward: Number((totalReward - beforeRew).toFixed(4)),
          state: projectStateForSide(battle, 0, fog, fogOfWar),
          legal_actions: nextLegal,
          metrics: getMetrics(battle, totalReward),
        }) + "\n"
      );
      return;
    }

    process.stdout.write(JSON.stringify({ ok: false, error: `Unknown cmd: ${req.cmd}` }) + "\n");
  } catch (err) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.stack ? err.stack : err) }) + "\n");
  }
});
