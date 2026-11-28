"""Mock Azahar 3DS UDP RPC v2 server, PK7 codec, and live bridge grader for escm-bench."""

from escm_bench.bridge.grader import grade_bridge
from escm_bench.bridge.mock_azahar import (
    BATTLE_AI_PTR,
    BATTLE_CRO_BASE,
    CODE_CAVE_OFF,
    HOOK_SITE_OFF,
    MAILBOX_MAGIC,
    MAILBOX_OFF,
    ORIG_HOOK_INSN,
    PK7_STRIDE,
    PLAYER_PARTY_ADDR,
    AI_PARTY_ADDR,
    USUM_PROCESS_NAME,
    USUM_TITLE_ID_US,
    MockAzaharServer,
)
from escm_bench.bridge.pk7 import decrypt_pk7, encrypt_pk7, make_pk7_slot

__all__ = [
    "AI_PARTY_ADDR",
    "BATTLE_AI_PTR",
    "BATTLE_CRO_BASE",
    "CODE_CAVE_OFF",
    "HOOK_SITE_OFF",
    "MAILBOX_MAGIC",
    "MAILBOX_OFF",
    "MockAzaharServer",
    "ORIG_HOOK_INSN",
    "PK7_STRIDE",
    "PLAYER_PARTY_ADDR",
    "USUM_PROCESS_NAME",
    "USUM_TITLE_ID_US",
    "decrypt_pk7",
    "encrypt_pk7",
    "grade_bridge",
    "make_pk7_slot",
]
