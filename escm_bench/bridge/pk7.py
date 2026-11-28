"""Clean Gen 7 USUM live-RAM PK7 encoder & decoder (484-byte battle party slot format).

Implements the standard Gen 7 PK7 binary layout (publicly documented by PKHeX / 3DS community):
  - Bytes 0x00..0x04: Encryption Constant (EC, u32 LE)
  - Bytes 0x04..0x06: Sanity (u16 LE, 0)
  - Bytes 0x06..0x08: 16-bit additive checksum of decrypted bytes 0x08..0xE8 (112 u16 words)
  - Bytes 0x08..0xE8: Four 56-byte blocks shuffled by `sv = ((ec >> 13) & 0x1F) % 24`, then XOR-encrypted
                     with Gen 7 LCG (`seed = (0x41C64E6D * seed + 0x00006073) & 0xFFFFFFFF`, key = seed >> 16)
  - Bytes 0xE8..0x158: Live RAM party extension padding (zeroed in synthetic slots)
  - Bytes 0x158..0x174: Encrypted 28-byte party stats tail (XOR-encrypted with fresh LCG seeded from EC):
      +0x00 u32 status_word (0=none, 1..7=slp, 0x08=psn, 0x10=brn, 0x20=frz, 0x40=par, 0x80=tox)
      +0x04 u8  level
      +0x08 u16 hp
      +0x0A u16 max_hp
      +0x0C u16 atk
      +0x0E u16 def
      +0x10 u16 spe
      +0x12 u16 spa
      +0x14 u16 spd
  - Total live RAM slot stride: 0x1E4 (484 bytes).
"""

from __future__ import annotations

import struct
from typing import Any

PK7_LIVE_STRIDE = 0x1E4  # 484 bytes
PK7_PARTY_TAIL_OFF = 0x158
PK7_PARTY_TAIL_LEN = 28

# Standard Gen 6/7 24-order 4-block shuffle table:
# BLOCK_ORDER[sv][physical_slot] = logical_block_index (0=A, 1=B, 2=C, 3=D)
BLOCK_ORDER: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
    (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
    (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2),
    (2, 3, 0, 1), (3, 2, 0, 1), (1, 2, 3, 0), (1, 3, 2, 0),
    (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0),
)


def _lcg_xor(data: bytearray, seed: int) -> bytearray:
    out = bytearray(data)
    s = seed & 0xFFFFFFFF
    for i in range(0, len(out) - 1, 2):
        s = (0x41C64E6D * s + 0x00006073) & 0xFFFFFFFF
        key = (s >> 16) & 0xFFFF
        w = struct.unpack_from("<H", out, i)[0] ^ key
        struct.pack_into("<H", out, i, w)
    return out


def _checksum_08_e8(plain_08_e8: bytes | bytearray) -> int:
    total = 0
    for i in range(0, 224, 2):
        total = (total + struct.unpack_from("<H", plain_08_e8, i)[0]) & 0xFFFF
    return total


def encrypt_pk7(decrypted: bytes | bytearray) -> bytes:
    """Encrypt a decrypted 484-byte live-RAM PK7 buffer (computing checksum, shuffling blocks, XORing)."""
    if len(decrypted) < PK7_LIVE_STRIDE:
        raise ValueError(f"Expected >= {PK7_LIVE_STRIDE} bytes, got {len(decrypted)}")
    raw = bytearray(decrypted[:PK7_LIVE_STRIDE])
    ec = struct.unpack_from("<I", raw, 0)[0]
    sv = ((ec >> 13) & 0x1F) % 24

    plain_body = bytes(raw[0x08:0xE8])
    chk = _checksum_08_e8(plain_body)
    struct.pack_into("<H", raw, 0x06, chk)

    logical = [plain_body[i * 56 : (i + 1) * 56] for i in range(4)]
    order = BLOCK_ORDER[sv]
    shuffled = bytearray()
    for phys_idx in range(4):
        shuffled.extend(logical[order[phys_idx]])

    raw[0x08:0xE8] = _lcg_xor(shuffled, ec)
    tail = bytearray(raw[PK7_PARTY_TAIL_OFF : PK7_PARTY_TAIL_OFF + PK7_PARTY_TAIL_LEN])
    raw[PK7_PARTY_TAIL_OFF : PK7_PARTY_TAIL_OFF + PK7_PARTY_TAIL_LEN] = _lcg_xor(tail, ec)
    return bytes(raw)


def decrypt_pk7(encrypted: bytes | bytearray) -> dict[str, Any]:
    """Decrypt a 484-byte encrypted live-RAM PK7 buffer and parse core battle fields."""
    if len(encrypted) < PK7_LIVE_STRIDE:
        raise ValueError(f"Expected >= {PK7_LIVE_STRIDE} bytes, got {len(encrypted)}")
    raw = bytearray(encrypted[:PK7_LIVE_STRIDE])
    ec = struct.unpack_from("<I", raw, 0)[0]
    stored_chk = struct.unpack_from("<H", raw, 0x06)[0]
    sv = ((ec >> 13) & 0x1F) % 24

    unxored = _lcg_xor(bytearray(raw[0x08:0xE8]), ec)
    order = BLOCK_ORDER[sv]
    logical: list[bytes] = [b""] * 4
    for phys_idx in range(4):
        logical[order[phys_idx]] = bytes(unxored[phys_idx * 56 : (phys_idx + 1) * 56])
    plain_body = b"".join(logical)
    computed_chk = _checksum_08_e8(plain_body)

    buf = bytearray(PK7_LIVE_STRIDE)
    buf[0x00:0x08] = raw[0x00:0x08]
    buf[0x08:0xE8] = plain_body
    tail = _lcg_xor(bytearray(raw[PK7_PARTY_TAIL_OFF : PK7_PARTY_TAIL_OFF + PK7_PARTY_TAIL_LEN]), ec)
    buf[PK7_PARTY_TAIL_OFF : PK7_PARTY_TAIL_OFF + PK7_PARTY_TAIL_LEN] = tail

    species = struct.unpack_from("<H", buf, 0x08)[0]
    item = struct.unpack_from("<H", buf, 0x0A)[0]
    ability = buf[0x14]
    form = (buf[0x1D] >> 3) & 0x1F
    moves = list(struct.unpack_from("<4H", buf, 0x5A))
    pps = list(buf[0x62:0x66])

    status_word = struct.unpack_from("<I", tail, 0x00)[0]
    level = tail[0x04]
    hp, max_hp, atk, def_, spe, spa, spd = struct.unpack_from("<7H", tail, 0x08)

    return {
        "ec": ec,
        "checksum_valid": stored_chk == computed_chk,
        "species": species,
        "item": item,
        "ability": ability,
        "form": form,
        "moves": moves,
        "pps": pps,
        "status_word": status_word,
        "level": level,
        "hp": hp,
        "max_hp": max_hp,
        "stats": {"atk": atk, "def": def_, "spe": spe, "spa": spa, "spd": spd},
    }


def make_pk7_slot(
    *,
    species: int,
    level: int,
    hp: int,
    max_hp: int,
    moves: list[int],
    pps: list[int],
    item: int = 0,
    ability: int = 0,
    form: int = 0,
    status_word: int = 0,
    stats: tuple[int, int, int, int, int] = (120, 100, 110, 120, 100),
    ec: int = 0x13572468,
) -> bytes:
    """Build an encrypted 484-byte live-RAM USUM PK7 battle party slot."""
    buf = bytearray(PK7_LIVE_STRIDE)
    struct.pack_into("<I", buf, 0x00, ec & 0xFFFFFFFF)
    struct.pack_into("<H", buf, 0x04, 0)  # sanity
    struct.pack_into("<H", buf, 0x08, species & 0xFFFF)
    struct.pack_into("<H", buf, 0x0A, item & 0xFFFF)
    buf[0x14] = ability & 0xFF
    buf[0x1D] = (form & 0x1F) << 3

    mv4 = (list(moves) + [0, 0, 0, 0])[:4]
    pp4 = (list(pps) + [0, 0, 0, 0])[:4]
    struct.pack_into("<4H", buf, 0x5A, *[m & 0xFFFF for m in mv4])
    for i, pp in enumerate(pp4):
        buf[0x62 + i] = pp & 0xFF

    atk, def_, spe, spa, spd = stats
    struct.pack_into("<I", buf, PK7_PARTY_TAIL_OFF + 0x00, status_word & 0xFFFFFFFF)
    buf[PK7_PARTY_TAIL_OFF + 0x04] = level & 0xFF
    struct.pack_into(
        "<7H",
        buf,
        PK7_PARTY_TAIL_OFF + 0x08,
        hp & 0xFFFF,
        max_hp & 0xFFFF,
        atk & 0xFFFF,
        def_ & 0xFFFF,
        spe & 0xFFFF,
        spa & 0xFFFF,
        spd & 0xFFFF,
    )
    return encrypt_pk7(buf)
