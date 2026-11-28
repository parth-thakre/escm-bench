"""Mock Azahar 3DS UDP RPC v2 Server for mechanical grading of candidate emulator bridges.

Implements the exact Citra / Azahar UDP RPC v2 protocol:
  Header (16 bytes, `<IIII`): (version=2, req_id, req_type, payload_len)
  Request types:
    1 = REQ_READ_MEMORY      req: <II (addr, size)         resp: <bytes>
    2 = REQ_WRITE_MEMORY     req: <II (addr, len) + data   resp: b""
    3 = REQ_PROCESS_LIST     req: <II (start, max_count)   resp: <I count + N * (<IQ8s pid, tid, name)
    4 = REQ_SET_GET_PROCESS  req: <II (set_flag, pid)      resp: <I active_pid

Enforces realistic Azahar chunk size limits (32B read / 24B write by default) and tracks
every guest memory write so the grader can verify exact ARM hook installation, mailbox
handshaking, legal `btl::BattleAi` action writeback, memory safety, and clean uninstall.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from escm_bench.bridge.pk7 import PK7_LIVE_STRIDE

RPC_VERSION = 2
REQ_READ_MEMORY = 1
REQ_WRITE_MEMORY = 2
REQ_PROCESS_LIST = 3
REQ_SET_GET_PROCESS = 4

# Public USUM v1.0 (CTR-P-A2AA) process & memory layout constants
USUM_PROCESS_NAME = b"momiji\x00\x00"
USUM_TITLE_ID_US = 0x00040000001B5000
USUM_PID = 0x28

BATTLE_CRO_BASE = 0x006DD000
BATTLE_CRO_SIZE = 0x150000  # covers .text, code cave, and .bss mailbox
HOOK_SITE_OFF = 0xDFA20
ORIG_HOOK_INSN = 0xE5940014  # ARM: ldr r0, [r4, #0x14]
CODE_CAVE_OFF = 0xFC980
CODE_CAVE_SIZE = 0x600
MAILBOX_OFF = 0x13BF80
MAILBOX_SIZE = 0x40
MAILBOX_MAGIC = 0x4D435345  # "ESCM" little-endian

# Simulated live RAM regions
BATTLE_AI_PTR = 0x30020000
BATTLE_AI_REGION_SIZE = 0x200
AI_PARTY_ADDR = 0x30000000
PLAYER_PARTY_ADDR = 0x30010000
PK7_STRIDE = PK7_LIVE_STRIDE  # 0x1E4 (484 bytes)
PARTY_REGION_SIZE = PK7_STRIDE * 6  # 2904 bytes

# Active-slot metadata block in RAM (so bridges can read which party slot is active on each side)
BATTLE_STATE_META_ADDR = 0x30020100  # <4I: turn, ai_active_slot, player_active_slot, can_z_mask


@dataclass
class WriteRecord:
    timestamp: float
    addr: int
    size: int
    allowed: bool


@dataclass
class MemoryRegion:
    base: int
    buf: bytearray

    @property
    def end(self) -> int:
        return self.base + len(self.buf)


class MockAzaharServer:
    """In-process UDP RPC v2 mock emulator server with memory-write auditing."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_read_chunk: int = 32,
        max_write_chunk: int = 24,
    ) -> None:
        self.host = host
        self.max_read_chunk = max_read_chunk
        self.max_write_chunk = max_write_chunk

        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._sock.settimeout(0.1)
        self.port: int = int(self._sock.getsockname()[1])

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Protocol audit counters
        self.active_pid: int = 0
        self.process_list_calls: int = 0
        self.set_process_calls: int = 0
        self.read_calls: int = 0
        self.write_calls: int = 0
        self.oversized_chunk_errors: int = 0
        self.unattached_memory_errors: int = 0
        self.unmapped_memory_errors: int = 0
        self.write_log: list[WriteRecord] = []

        # Simulated guest memory regions
        cro = bytearray(BATTLE_CRO_SIZE)
        # Write realistic CRO0 magic header at +0x80 (standard CTR CRO header signature)
        cro[0x80:0x84] = b"CRO0"
        # Write original instruction at hook site
        struct.pack_into("<I", cro, HOOK_SITE_OFF, ORIG_HOOK_INSN)

        self._regions: list[MemoryRegion] = [
            MemoryRegion(BATTLE_CRO_BASE, cro),
            MemoryRegion(AI_PARTY_ADDR, bytearray(PARTY_REGION_SIZE)),
            MemoryRegion(PLAYER_PARTY_ADDR, bytearray(PARTY_REGION_SIZE)),
            MemoryRegion(BATTLE_AI_PTR, bytearray(BATTLE_AI_REGION_SIZE)),
        ]

        # Allowed guest write windows: hook site (4B), code cave, mailbox, and btl::BattleAi output (+0x36..+0x42)
        self._allowed_write_ranges: tuple[tuple[int, int], ...] = (
            (BATTLE_CRO_BASE + HOOK_SITE_OFF, BATTLE_CRO_BASE + HOOK_SITE_OFF + 4),
            (BATTLE_CRO_BASE + CODE_CAVE_OFF, BATTLE_CRO_BASE + CODE_CAVE_OFF + CODE_CAVE_SIZE),
            (BATTLE_CRO_BASE + MAILBOX_OFF, BATTLE_CRO_BASE + MAILBOX_OFF + MAILBOX_SIZE),
            (BATTLE_AI_PTR + 0x36, BATTLE_AI_PTR + 0x42),
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._serve_loop, daemon=True, name=f"mock-azahar-{self.port}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self._sock.close()
        except OSError:
            pass

    def _find_region(self, addr: int, size: int) -> MemoryRegion | None:
        end = addr + size
        for reg in self._regions:
            if addr >= reg.base and end <= reg.end:
                return reg
        return None

    def _is_allowed_write(self, addr: int, size: int) -> bool:
        end = addr + size
        for lo, hi in self._allowed_write_ranges:
            if addr >= lo and end <= hi:
                return True
        return False

    # Host-side helpers (called by the grader directly, bypassing RPC)
    def host_write(self, addr: int, data: bytes) -> None:
        with self._lock:
            reg = self._find_region(addr, len(data))
            if reg is None:
                raise ValueError(f"Unmapped host_write addr=0x{addr:08X} size={len(data)}")
            off = addr - reg.base
            reg.buf[off : off + len(data)] = data

    def host_read(self, addr: int, size: int) -> bytes:
        with self._lock:
            reg = self._find_region(addr, size)
            if reg is None:
                raise ValueError(f"Unmapped host_read addr=0x{addr:08X} size={size}")
            off = addr - reg.base
            return bytes(reg.buf[off : off + size])

    def load_parties(
        self,
        *,
        ai_slots: list[bytes],
        player_slots: list[bytes],
        turn: int,
        ai_active_slot: int = 0,
        player_active_slot: int = 0,
        can_z_mask: int = 0,
        vanilla_move_slot: int = 0,
        vanilla_switch_slot: int = 0,
        vanilla_target_pos: int = 0,
    ) -> None:
        """Load encrypted PK7 party buffers and vanilla btl::BattleAi state into mock 3DS RAM."""
        ai_blob = bytearray(PARTY_REGION_SIZE)
        for i, slot in enumerate(ai_slots[:6]):
            ai_blob[i * PK7_STRIDE : (i + 1) * PK7_STRIDE] = slot[:PK7_STRIDE]
        self.host_write(AI_PARTY_ADDR, bytes(ai_blob))

        pl_blob = bytearray(PARTY_REGION_SIZE)
        for i, slot in enumerate(player_slots[:6]):
            pl_blob[i * PK7_STRIDE : (i + 1) * PK7_STRIDE] = slot[:PK7_STRIDE]
        self.host_write(PLAYER_PARTY_ADDR, bytes(pl_blob))

        # Populate btl::BattleAi vanilla defaults (+0x36..+0x41)
        ai_struct = bytearray(0x44)
        ai_struct[0x36] = vanilla_switch_slot & 0xFF
        ai_struct[0x37] = vanilla_move_slot & 0xFF
        ai_struct[0x38] = vanilla_target_pos & 0xFF
        ai_struct[0x3A] = 0xFF  # sentinel: unwritten out_action.kind
        ai_struct[0x3B] = 0xFF
        ai_struct[0x3C] = 0x00
        ai_struct[0x3D] = 0x00
        ai_struct[0x40] = 0xFF
        self.host_write(BATTLE_AI_PTR, bytes(ai_struct))

        meta = struct.pack("<4I", turn, ai_active_slot, player_active_slot, can_z_mask)
        self.host_write(BATTLE_STATE_META_ADDR, meta)

    def read_mailbox(self) -> dict[str, int]:
        raw = self.host_read(BATTLE_CRO_BASE + MAILBOX_OFF, 20)
        magic, req_ready, resp_ready, ai_ptr, served = struct.unpack("<5I", raw)
        return {
            "magic": magic,
            "req_ready": req_ready,
            "resp_ready": resp_ready,
            "battle_ai_ptr": ai_ptr,
            "decisions_served": served,
        }

    def post_turn_request(self) -> None:
        """Simulate the ARM hook firing in State 9: write battle_ai_ptr, clear resp_ready, set req_ready=1."""
        with self._lock:
            reg = self._find_region(BATTLE_CRO_BASE + MAILBOX_OFF, 20)
            assert reg is not None
            off = MAILBOX_OFF
            # Keep magic (+0) and decisions_served (+16); update req_ready=1, resp_ready=0, battle_ai_ptr
            struct.pack_into("<III", reg.buf, off + 4, 1, 0, BATTLE_AI_PTR)

    def read_hook_insn(self) -> int:
        return struct.unpack("<I", self.host_read(BATTLE_CRO_BASE + HOOK_SITE_OFF, 4))[0]

    def read_code_cave(self) -> bytes:
        return self.host_read(BATTLE_CRO_BASE + CODE_CAVE_OFF, CODE_CAVE_SIZE)

    def read_battle_ai_action(self) -> dict[str, int]:
        raw = self.host_read(BATTLE_AI_PTR + 0x3A, 8)
        return {
            "kind": raw[0],
            "move_slot": raw[1],
            "use_z": raw[2],
            "target_pos": raw[3],
            "switch_slot": raw[6],  # +0x40
        }

    def illegal_writes(self) -> list[WriteRecord]:
        with self._lock:
            return [w for w in self.write_log if not w.allowed]

    def _serve_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pkt, peer = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            resp = self._handle_packet(pkt)
            if resp is not None:
                try:
                    self._sock.sendto(resp, peer)
                except OSError:
                    pass

    def _handle_packet(self, pkt: bytes) -> bytes | None:
        if len(pkt) < 16:
            return None
        ver, req_id, req_type, payload_len = struct.unpack_from("<4I", pkt, 0)
        if ver != RPC_VERSION or len(pkt) < 16 + payload_len:
            return None
        payload = pkt[16 : 16 + payload_len]

        with self._lock:
            if req_type == REQ_PROCESS_LIST:
                self.process_list_calls += 1
                if len(payload) < 8:
                    return None
                start, max_count = struct.unpack_from("<II", payload, 0)
                entries = [
                    (0x10, 0x0004013000001A02, b"dsp\x00\x00\x00\x00\x00"),
                    (USUM_PID, USUM_TITLE_ID_US, USUM_PROCESS_NAME),
                ]
                sliced = entries[start : start + max_count]
                out = bytearray(struct.pack("<I", len(sliced)))
                for pid, tid, name in sliced:
                    out.extend(struct.pack("<IQ8s", pid, tid, name))
                return struct.pack("<4I", RPC_VERSION, req_id, req_type, len(out)) + bytes(out)

            if req_type == REQ_SET_GET_PROCESS:
                self.set_process_calls += 1
                if len(payload) < 8:
                    return None
                set_flag, pid = struct.unpack_from("<II", payload, 0)
                if set_flag != 0:
                    if pid == USUM_PID:
                        self.active_pid = pid
                    else:
                        return None
                out = struct.pack("<I", self.active_pid)
                return struct.pack("<4I", RPC_VERSION, req_id, req_type, len(out)) + out

            if req_type == REQ_READ_MEMORY:
                self.read_calls += 1
                if self.active_pid != USUM_PID:
                    self.unattached_memory_errors += 1
                    return None
                if len(payload) < 8:
                    return None
                addr, size = struct.unpack_from("<II", payload, 0)
                if size == 0 or size > self.max_read_chunk:
                    self.oversized_chunk_errors += 1
                    return None
                reg = self._find_region(addr, size)
                if reg is None:
                    self.unmapped_memory_errors += 1
                    return None
                off = addr - reg.base
                data = bytes(reg.buf[off : off + size])
                return struct.pack("<4I", RPC_VERSION, req_id, req_type, len(data)) + data

            if req_type == REQ_WRITE_MEMORY:
                self.write_calls += 1
                if self.active_pid != USUM_PID:
                    self.unattached_memory_errors += 1
                    return None
                if len(payload) < 8:
                    return None
                addr, size = struct.unpack_from("<II", payload, 0)
                data = payload[8 : 8 + size]
                if len(data) != size or size == 0 or size > self.max_write_chunk:
                    self.oversized_chunk_errors += 1
                    return None
                reg = self._find_region(addr, size)
                if reg is None:
                    self.unmapped_memory_errors += 1
                    return None
                allowed = self._is_allowed_write(addr, size)
                self.write_log.append(WriteRecord(time.perf_counter(), addr, size, allowed))
                off = addr - reg.base
                reg.buf[off : off + size] = data
                return struct.pack("<4I", RPC_VERSION, req_id, req_type, 0)

        return None
