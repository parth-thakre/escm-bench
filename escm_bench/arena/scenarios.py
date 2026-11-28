"""Curated Gen 7 USUM Nuzlocke boss gauntlet scenarios for untouched Pokemon Showdown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _mon(
    name: str,
    species: str,
    level: int,
    moves: list[str],
    ability: str = "",
    item: str = "",
    nature: str = "Serious",
    evs: dict[str, int] | None = None,
    ivs: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "species": species,
        "level": level,
        "ability": ability,
        "item": item,
        "nature": nature,
        "moves": moves,
        "evs": evs or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "ivs": ivs or {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        "happiness": 255,
    }


def _standard_nuzlocke_party(level: int) -> list[dict[str, Any]]:
    """Balanced USUM Nuzlocke player party (zero EVs, realistic story progression)."""
    return [
        _mon("Flurry", "Primarina", level, ["Scald", "Moonblast", "Ice Beam", "Protect"], "Torrent", "Mystic Water", "Modest"),
        _mon("Tex", "Toxapex", level, ["Scald", "Toxic", "Recover", "Baneful Bunker"], "Regenerator", "Black Sludge", "Bold"),
        _mon("Sahara", "Krookodile", level, ["Earthquake", "Crunch", "Rock Slide", "Protect"], "Intimidate", "Soft Sand", "Jolly"),
        _mon("Ion", "Magnezone", level, ["Thunderbolt", "Flash Cannon", "Volt Switch", "Protect"], "Sturdy", "Air Balloon", "Timid"),
        _mon("Nacho", "Hawlucha", level, ["Acrobatics", "High Jump Kick", "Swords Dance", "Roost"], "Unburden", "", "Adamant"),
        _mon("TheRock", "Gigalith", level, ["Rock Blast", "Earthquake", "Stealth Rock", "Explosion"], "Sand Stream", "Hard Stone", "Adamant"),
    ]


ARENA_SCENARIOS: dict[str, dict[str, Any]] = {
    "e4_kahili": {
        "id": "e4_kahili",
        "title": "Elite Four Kahili (Flying)",
        "tier": "boss",
        "description": "USUM Elite Four Kahili with Flyinium Z Toucannon and Hawlucha vs 0-EV Lv56 Nuzlocke party.",
        "p1": [
            _mon("Braviary", "Braviary", 56, ["Brave Bird", "Superpower", "Crush Claw", "Tailwind"], "Keen Eye", "", "Adamant", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Hawlucha", "Hawlucha", 56, ["Flying Press", "Throat Chop", "Poison Jab", "Roost"], "Limber", "", "Jolly", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Oricorio", "Oricorio-Baile", 56, ["Revelation Dance", "Air Slash", "Teeter Dance", "Feather Dance"], "Dancer", "", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
            _mon("Mandibuzz", "Mandibuzz", 56, ["Punishment", "Bone Rush", "Brave Bird", "Flatter"], "Overcoat", "", "Impish", {"hp": 252, "atk": 0, "def": 252, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Toucannon", "Toucannon", 57, ["Beak Blast", "Bullet Seed", "Rock Blast", "Screech"], "Skill Link", "Flyinium Z", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
        ],
        "p2": _standard_nuzlocke_party(56),
    },
    "e4_olivia": {
        "id": "e4_olivia",
        "title": "Elite Four Olivia (Rock)",
        "tier": "boss",
        "description": "USUM Elite Four Olivia with Rockium Z Lycanroc-Midnight and Sturdy Probopass.",
        "p1": [
            _mon("Armaldo", "Armaldo", 56, ["X-Scissor", "Rock Blast", "Crush Claw", "Aqua Jet"], "Battle Armor", "", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Cradily", "Cradily", 56, ["Giga Drain", "Ancient Power", "Sludge Bomb", "Confuse Ray"], "Suction Cups", "", "Calm", {"hp": 252, "atk": 0, "def": 0, "spa": 0, "spd": 252, "spe": 0}),
            _mon("Gigalith", "Gigalith", 56, ["Stone Edge", "Earthquake", "Iron Head", "Sandstorm"], "Sturdy", "", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Probopass", "Probopass", 56, ["Earth Power", "Power Gem", "Thunder Wave", "Flash Cannon"], "Sturdy", "", "Modest", {"hp": 252, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 0}),
            _mon("Lycanroc", "Lycanroc-Midnight", 57, ["Stone Edge", "Crunch", "Rock Climb", "Counter"], "Vital Spirit", "Rockium Z", "Jolly", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
        ],
        "p2": _standard_nuzlocke_party(56),
    },
    "e4_acerola": {
        "id": "e4_acerola",
        "title": "Elite Four Acerola (Ghost)",
        "tier": "boss",
        "description": "USUM Elite Four Acerola with Ghostium Z Palossand and Prankster/Status pressure.",
        "p1": [
            _mon("Banette", "Banette", 56, ["Shadow Claw", "Sucker Punch", "Will-O-Wisp", "Screech"], "Insomnia", "", "Adamant", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Drifblim", "Drifblim", 56, ["Ominous Wind", "Focus Energy", "Amnesia", "Baton Pass"], "Aftermath", "", "Timid", {"hp": 252, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Dhelmise", "Dhelmise", 56, ["Anchor Shot", "Power Whip", "Shadow Ball", "Slam"], "Steelworker", "", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Froslass", "Froslass", 56, ["Blizzard", "Shadow Ball", "Crunch", "Confuse Ray"], "Snow Cloak", "", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
            _mon("Palossand", "Palossand", 57, ["Shadow Ball", "Earth Power", "Giga Drain", "Shore Up"], "Water Compaction", "Ghostium Z", "Modest", {"hp": 252, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 0}),
        ],
        "p2": _standard_nuzlocke_party(56),
    },
    "ultra_necrozma": {
        "id": "ultra_necrozma",
        "title": "Ultra Necrozma (Omniboost Boss)",
        "tier": "boss",
        "description": "Lv60 Ultra Necrozma with +1 all stats aura vs Lv58 Nuzlocke party — classic USUM run-killer.",
        "p1_aura": {"atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1},
        "p1": [
            _mon("UltraNecrozma", "Necrozma-Ultra", 60, ["Photon Geyser", "Smart Strike", "Power Gem", "Dragon Pulse"], "Neuroforce", "", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
        ],
        "p2": _standard_nuzlocke_party(58),
    },
    "guzma_aether": {
        "id": "guzma_aether",
        "title": "Team Skull Boss Guzma (Aether Paradise)",
        "tier": "boss",
        "description": "First Impression Golisopod pivot pressure + Pin Missile / Swords Dance threats.",
        "p1": [
            _mon("Golisopod", "Golisopod", 45, ["First Impression", "Razor Shell", "Sucker Punch", "Swords Dance"], "Emergency Exit", "", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Masquerain", "Masquerain", 45, ["Bug Buzz", "Air Slash", "Ice Beam", "Scald"], "Intimidate", "", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
            _mon("Pinsir", "Pinsir", 45, ["X-Scissor", "Brick Break", "Throat Chop", "Storm Throw"], "Mold Breaker", "", "Jolly", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Vikavolt", "Vikavolt", 45, ["Thunderbolt", "Bug Buzz", "Energy Ball", "Flash Cannon"], "Levitate", "", "Modest", {"hp": 252, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 0}),
        ],
        "p2": _standard_nuzlocke_party(45),
    },
    "gladion_lanakila": {
        "id": "gladion_lanakila",
        "title": "Rival Gladion (Mt. Lanakila)",
        "tier": "boss",
        "description": "Fast offensive squad with Zoroark Illusion, Lucario, Weavile, and Silvally.",
        "p1": [
            _mon("Crobat", "Crobat", 53, ["Cross Poison", "Acrobatics", "U-turn", "Confuse Ray"], "Inner Focus", "", "Jolly", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Weavile", "Weavile", 53, ["Night Slash", "Ice Punch", "Low Kick", "Fake Out"], "Pressure", "", "Jolly", {"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
            _mon("Lucario", "Lucario", 53, ["Aura Sphere", "Flash Cannon", "Extreme Speed", "Dragon Pulse"], "Inner Focus", "", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
            _mon("Silvally", "Silvally", 55, ["Multi-Attack", "Crunch", "X-Scissor", "Air Slash"], "RKS System", "", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Zoroark", "Zoroark", 53, ["Night Daze", "Flamethrower", "Focus Blast", "Extrasensory"], "Illusion", "", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
        ],
        "p2": _standard_nuzlocke_party(54),
    },
    "totem_araquanid": {
        "id": "totem_araquanid",
        "title": "Totem Araquanid (Brooklet Hill)",
        "tier": "midgame",
        "description": "Early-game Totem Araquanid with +1 Speed aura & Water Bubble Bubble vs Lv20 party.",
        "p1_aura": {"spe": 1},
        "p1": [
            _mon("Araquanid", "Araquanid", 20, ["Bubble", "Leech Life", "Bite", "Aurora Beam"], "Water Bubble", "Wacan Berry", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Dewpider", "Dewpider", 18, ["Bubble", "Bug Bite", "Spider Web", "Rain Dance"], "Water Bubble", "", "Careful"),
        ],
        "p2": [
            _mon("Brionne", "Brionne", 20, ["Bubble Beam", "Disarming Voice", "Icy Wind", "Aqua Jet"], "Torrent", "", "Modest"),
            _mon("Magnemite", "Magnemite", 20, ["Spark", "Sonic Boom", "Thunder Wave", "Mirror Shot"], "Sturdy", "", "Timid"),
            _mon("Hawlucha", "Hawlucha", 20, ["Wing Attack", "Karate Chop", "Roost", "Encore"], "Unburden", "", "Jolly"),
            _mon("Mudbray", "Mudbray", 20, ["Bulldoze", "Double Kick", "Rock Tomb", "Protect"], "Stamina", "", "Adamant"),
        ],
    },
    "route_ambush_2v6": {
        "id": "route_ambush_2v6",
        "title": "Route Ambush (2v6 Snowball Trap)",
        "tier": "snowball",
        "description": "Underdog 2-mon route trainer with Wobuffet/Dugtrio-style trap + Explosion — cannot win, ONLY scores by claiming a permanent KO!",
        "p1": [
            _mon("Golem", "Golem-Alola", 48, ["Explosion", "Wild Charge", "Rock Blast", "Stealth Rock"], "Sturdy", "Custap Berry", "Adamant", {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}),
            _mon("Gengar", "Gengar", 48, ["Shadow Ball", "Sludge Bomb", "Destiny Bond", "Thunderbolt"], "Cursed Body", "Focus Sash", "Timid", {"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252}),
        ],
        "p2": _standard_nuzlocke_party(48),
    },
}


def load_scenarios_file(path: Path) -> dict[str, dict[str, Any]]:
    """Load external scenario definitions from a JSON file (dict or list of scenario dicts)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {str(s["id"]): s for s in raw}
    if isinstance(raw, dict):
        if "scenarios" in raw and isinstance(raw["scenarios"], list):
            return {str(s["id"]): s for s in raw["scenarios"]}
        return {str(k): v for k, v in raw.items()}
    raise ValueError(f"Unsupported scenarios JSON structure in {path}")


def get_scenarios(
    names: list[str] | None = None,
    scenarios_file: Path | None = None,
) -> list[dict[str, Any]]:
    catalog = load_scenarios_file(scenarios_file) if scenarios_file is not None else ARENA_SCENARIOS
    if not names:
        return list(catalog.values())
    out = []
    for name in names:
        if name not in catalog:
            raise KeyError(f"Unknown scenario '{name}'. Available: {', '.join(catalog)}")
        out.append(catalog[name])
    return out
