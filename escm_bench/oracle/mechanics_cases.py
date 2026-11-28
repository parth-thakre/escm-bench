"""Curated Gen 7 mechanics edge-case suite targeting the exact bugs models hallucinate."""

from __future__ import annotations

from typing import Any

FORMAT = "gen7customgame@@@!Team Preview"


def _set(
    name: str,
    species: str,
    moves: list[str],
    *,
    level: int = 50,
    ability: str = "",
    item: str = "",
    nature: str = "Serious",
    evs: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "species": species,
        "level": level,
        "gender": "M",
        "ability": ability,
        "item": item,
        "nature": nature,
        "moves": moves,
        "evs": evs or {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "ivs": {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        "happiness": 255,
    }


def get_mechanics_cases() -> list[dict[str, Any]]:
    """Return 16 deterministic Gen 7 edge-case battles."""
    return [
        {
            "id": "mech_speed_order_scarf",
            "format": FORMAT,
            "seed": [101, 202, 303, 404],
            "p1": [_set("ScarfBraviary", "Braviary", ["Brave Bird", "Superpower"], ability="Keen Eye", item="Choice Scarf", nature="Jolly", evs={"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252})],
            "p2": [_set("FastPrimarina", "Primarina", ["Ice Beam", "Scald"], ability="Torrent", nature="Timid", evs={"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252})],
            "choices": [["move:bravebird", "move:icebeam"]],
        },
        {
            "id": "mech_trick_room_inversion",
            "format": FORMAT,
            "seed": [111, 222, 333, 444],
            "p1": [_set("Slowbro", "Slowbro", ["Trick Room", "Scald"], ability="Regenerator", nature="Quiet")],
            "p2": [_set("Weavile", "Weavile", ["Night Slash", "Ice Punch"], ability="Pressure", nature="Jolly")],
            "choices": [["move:trickroom", "move:nightslash"], ["move:scald", "move:nightslash"]],
        },
        {
            "id": "mech_tailwind_speed_double",
            "format": FORMAT,
            "seed": [121, 232, 343, 454],
            "p1": [_set("Braviary", "Braviary", ["Tailwind", "Brave Bird"], ability="Keen Eye", nature="Adamant")],
            "p2": [_set("Gengar", "Gengar", ["Shadow Ball", "Sludge Bomb"], ability="Cursed Body", nature="Timid")],
            "choices": [["move:tailwind", "move:shadowball"], ["move:bravebird", "move:shadowball"]],
        },
        {
            "id": "mech_gen7_paralysis_half_speed",
            "format": FORMAT,
            "seed": [131, 242, 353, 464],
            "p1": [_set("Probopass", "Probopass", ["Thunder Wave", "Flash Cannon"], ability="Sturdy", nature="Calm")],
            "p2": [_set("Crobat", "Crobat", ["Acrobatics", "Cross Poison"], ability="Inner Focus", nature="Jolly")],
            "choices": [["move:thunderwave", "move:acrobatics"], ["move:flashcannon", "move:acrobatics"]],
        },
        {
            "id": "mech_prankster_dark_immunity",
            "format": FORMAT,
            "seed": [141, 252, 363, 474],
            "p1": [_set("Sableye", "Sableye", ["Will-O-Wisp", "Knock Off"], ability="Prankster", nature="Bold")],
            "p2": [_set("Krookodile", "Krookodile", ["Crunch", "Earthquake"], ability="Intimidate", nature="Jolly")],
            "choices": [["move:willowisp", "move:crunch"]],
        },
        {
            "id": "mech_gen7_burn_one_sixteenth",
            "format": FORMAT,
            "seed": [151, 262, 373, 484],
            "p1": [_set("Banette", "Banette", ["Will-O-Wisp", "Protect"], ability="Insomnia")],
            "p2": [_set("Snorlax", "Snorlax", ["Return", "Protect"], ability="Thick Fat")],
            "choices": [["move:willowisp", "move:return"], ["move:protect", "move:return"]],
        },
        {
            "id": "mech_explosion_vs_protect_self_faint",
            "format": FORMAT,
            "seed": [161, 272, 383, 494],
            "p1": [
                _set("Golem", "Golem-Alola", ["Explosion", "Rock Blast"], ability="Sturdy"),
                _set("Magnezone", "Magnezone", ["Thunderbolt"], ability="Sturdy"),
            ],
            "p2": [_set("Toxapex", "Toxapex", ["Protect", "Scald"], ability="Regenerator")],
            "choices": [["move:explosion", "move:protect"], ["switch:Magnezone", None]],
        },
        {
            "id": "mech_uturn_midturn_switch",
            "format": FORMAT,
            "seed": [171, 282, 393, 504],
            "p1": [
                _set("Crobat", "Crobat", ["U-turn", "Acrobatics"], ability="Inner Focus", nature="Jolly", evs={"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}),
                _set("Toxapex", "Toxapex", ["Scald", "Recover"], ability="Regenerator", nature="Bold"),
            ],
            "p2": [_set("Snorlax", "Snorlax", ["Body Slam", "Earthquake"], ability="Thick Fat", nature="Adamant")],
            "choices": [["move:uturn", "move:bodyslam"], ["switch:Toxapex", None]],
        },
        {
            "id": "mech_sturdy_and_focus_sash",
            "format": FORMAT,
            "seed": [181, 292, 403, 514],
            "p1": [_set("Gengar", "Gengar", ["Focus Blast", "Shadow Ball"], ability="Cursed Body", item="Focus Sash", nature="Timid", evs={"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 252})],
            "p2": [_set("Magnezone", "Magnezone", ["Flash Cannon", "Thunderbolt"], ability="Sturdy", nature="Modest", evs={"hp": 0, "atk": 0, "def": 0, "spa": 252, "spd": 0, "spe": 0})],
            "choices": [["move:focusblast", "move:flashcannon"]],
        },
        {
            "id": "mech_encore_lock_legal_choices",
            "format": FORMAT,
            "seed": [191, 302, 413, 524],
            "p1": [_set("Alakazam", "Alakazam", ["Encore", "Psychic"], ability="Magic Guard", nature="Timid")],
            "p2": [_set("Toxapex", "Toxapex", ["Recover", "Scald", "Toxic"], ability="Regenerator", nature="Bold")],
            "choices": [["move:psychic", "move:recover"], ["move:encore", "move:scald"], ["move:psychic", "move:recover"]],
        },
        {
            "id": "mech_fake_out_turn1_only",
            "format": FORMAT,
            "seed": [201, 312, 423, 534],
            "p1": [_set("Weavile", "Weavile", ["Fake Out", "Ice Punch"], ability="Pressure", nature="Jolly")],
            "p2": [_set("Primarina", "Primarina", ["Moonblast", "Scald"], ability="Torrent", nature="Modest")],
            "choices": [["move:fakeout", "move:moonblast"], ["move:fakeout", "move:moonblast"]],
        },
        {
            "id": "mech_zmove_pierces_protect",
            "format": FORMAT,
            "seed": [211, 322, 433, 544],
            "p1": [_set("Toucannon", "Toucannon", ["Beak Blast", "Bullet Seed"], ability="Skill Link", item="Flyinium Z", nature="Adamant")],
            "p2": [_set("Primarina", "Primarina", ["Protect", "Scald"], ability="Torrent", nature="Modest")],
            "choices": [["move:beakblast+zmove", "move:protect"]],
        },
        {
            "id": "mech_mega_gen7_instant_speed",
            "format": FORMAT,
            "seed": [221, 332, 443, 554],
            "p1": [_set("Metagross", "Metagross", ["Meteor Mash", "Zen Headbutt"], ability="Clear Body", item="Metagrossite", nature="Jolly", evs={"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252})],
            "p2": [_set("Garchomp", "Garchomp", ["Earthquake", "Dragon Claw"], ability="Rough Skin", nature="Jolly", evs={"hp": 0, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252})],
            "choices": [["move:meteormash+mega", "move:earthquake"]],
        },
        {
            "id": "mech_illusion_zoroark",
            "format": FORMAT,
            "seed": [231, 342, 453, 564],
            "p1": [
                _set("Zoroark", "Zoroark", ["Night Daze", "Flamethrower"], ability="Illusion", nature="Timid"),
                _set("Lucario", "Lucario", ["Aura Sphere", "Flash Cannon"], ability="Inner Focus", nature="Timid"),
            ],
            "p2": [_set("Primarina", "Primarina", ["Moonblast", "Scald"], ability="Torrent", nature="Modest")],
            "choices": [["move:nightdaze", "move:moonblast"]],
        },
        {
            "id": "mech_stealth_rock_rapid_spin",
            "format": FORMAT,
            "seed": [241, 352, 463, 574],
            "p1": [
                _set("Gigalith", "Gigalith", ["Stealth Rock", "Stone Edge"], ability="Sand Stream"),
                _set("Braviary", "Braviary", ["Brave Bird"], ability="Keen Eye"),
            ],
            "p2": [
                _set("Starmie", "Starmie", ["Rapid Spin", "Scald"], ability="Natural Cure", nature="Timid"),
                _set("Charizard", "Charizard", ["Flamethrower"], ability="Blaze"),
            ],
            "choices": [["move:stealthrock", "move:scald"], ["move:stoneedge", "move:rapidspin"]],
        },
        {
            "id": "mech_electric_terrain_boost",
            "format": FORMAT,
            "seed": [251, 362, 473, 584],
            "p1": [_set("TapuKoko", "Tapu Koko", ["Thunderbolt", "Dazzling Gleam"], ability="Electric Surge", nature="Timid")],
            "p2": [_set("Toxapex", "Toxapex", ["Scald", "Recover"], ability="Regenerator", nature="Calm")],
            "choices": [["move:thunderbolt", "move:scald"]],
        },
    ]
