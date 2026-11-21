#!/usr/bin/env python3
"""Rebuild stage1/stage2 bound to the standalone vibe-player payload.

The baseline stage binaries were pre-built against a different supervisor;
stage2 embeds B11_SUPERVISOR_SIZE/CRC as compile-time constants, so swapping
the payload without recompiling yields rc=-313 on device. This reproduces the
Canopus build_band11_installer.py stage chain (sizes.h -> stage2 -> backfill
stage2 CRC -> stage1) without requiring the Rust canopus CLI:

  clang --target=arm-none-eabi -mcpu=cortex-m33 -mthumb ... -fPIC

Outputs to --output-dir:
  canopus_stage1-<target>.bin / canopus_stage2-<target>.bin
  canopus_loader_profile-<target>.bin (Lua profile with matching size/CRC)

SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
TARGET = "xiaomi-band-11-4.100.179"

CC = "clang"
FLAGS = ["--target=arm-none-eabi", "-mcpu=cortex-m33", "-mthumb", "-mfloat-abi=soft",
         "-ffreestanding", "-fPIC", "-fno-common", "-fno-builtin", "-fno-jump-tables",
         "-fno-stack-protector", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
         "-fdata-sections", "-ffunction-sections", "-fstack-usage", "-Os", "-Wall", "-Wextra", "-Werror"]

ADDRESS_MACROS = {
    "mpu_bitmap": "B11_MPU_BITMAP", "umem_slot": "B11_UMEM_SLOT",
    "umem_descriptor": "B11_UMEM_DESCRIPTOR", "cache_d_clean": "B11_CACHE_D_CLEAN",
    "cache_barrier": "B11_CACHE_BARRIER", "cache_i_invalidate": "B11_CACHE_I_INVALIDATE",
}


def elf_sections(path: Path):
    data = path.read_bytes()
    if data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("not little-endian ELF32")
    offset = struct.unpack_from("<I", data, 32)[0]
    stride, count, names = struct.unpack_from("<HHH", data, 46)
    rows = [struct.unpack_from("<10I", data, offset + i * stride) for i in range(count)]
    strings = data[rows[names][4]:rows[names][4] + rows[names][5]]
    return data, [(strings[r[0]:].split(b"\0", 1)[0].decode(), r) for r in rows]


def linked_binary(path: Path) -> bytes:
    """PIC invariants + section flattening, verbatim from Canopus."""
    data, sections = elf_sections(path)
    alloc = [(name, r) for name, r in sections if r[2] & 2 and r[5]]
    if any(r[1] == 8 or r[2] & 1 for _, r in alloc):
        raise ValueError("staged PIC image must have no GOT, writable data, or BSS")
    for name, r in sections:
        if r[1] == 9:
            for pos in range(r[4], r[4] + r[5], 8):
                _, info = struct.unpack_from("<II", data, pos)
                if info & 255 not in (3, 10, 23, 30, 42):
                    raise ValueError(f"non-PIC relocation {info & 255} in {name}")
    end = max(r[3] + r[5] for _, r in alloc)
    result = bytearray(end)
    for _, r in alloc:
        result[r[3]:r[3] + r[5]] = data[r[4]:r[4] + r[5]]
    if struct.unpack_from("<I", data, 24)[0] & ~1:
        raise ValueError("stage entry must be offset zero")
    return bytes(result)


def parse_addresses(header: Path) -> dict[str, int]:
    macros = {}
    for line in header.read_text().splitlines():
        m = re.match(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)u?\s*$", line)
        if m:
            macros[m.group(1)] = int(m.group(2), 0)
    return macros


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canopus", type=Path, required=True,
                    help="Canopus checkout providing sdk/c, runtime/loader, manager/target/band11")
    ap.add_argument("--payload", type=Path, required=True, help="vibe_player.elf (ET_REL)")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    canopus = args.canopus.resolve()
    payload = args.payload.resolve().read_bytes()
    if payload[:4] != b"\x7fELF":
        raise SystemExit("payload is not ELF")

    out = args.output_dir.resolve()
    work = out / "build" / TARGET
    work.mkdir(parents=True, exist_ok=True)

    inc = [f"-I{canopus / p}" for p in ("sdk/c", "runtime/loader", "manager/target/band11")] + \
          [f"-I{work}", f"-I{canopus / 'targets' / TARGET / 'generated'}"]

    addresses = parse_addresses(canopus / "targets" / TARGET / "generated" / "canopus_band11_addresses.h")
    for key, macro in ADDRESS_MACROS.items():
        if macro not in addresses:
            raise SystemExit(f"missing address macro: {macro}")
    bindings = json.loads((NATIVE / "target" / "native-bindings.json").read_text())
    fingerprints = [(int(a, 16), int(w, 16)) for a, w in bindings["fingerprint_words"].items()]

    header = work / "canopus_band11_sizes.h"
    header.write_text(
        f"#define B11_SUPERVISOR_SIZE {len(payload)}u\n"
        f"#define B11_SUPERVISOR_CRC 0x{zlib.crc32(payload):08x}u\n")

    def compile(src: str, name: str) -> Path:
        obj = work / (name + ".o")
        subprocess.run([CC, *FLAGS, *inc, "-c", str(canopus / src), "-o", str(obj)],
                       check=True)
        return obj

    def link(name: str, objects: list[Path]) -> bytes:
        elf = work / (name + ".elf")
        subprocess.run(["ld.lld", "-static", "--gc-sections", "--emit-relocs",
                        "-e", f"canopus_band11_{name}_entry",
                        "-T", str(canopus / "scripts" / "canopus_band11_stage.ld"),
                        "-o", str(elf), *[str(o) for o in objects]], check=True)
        return linked_binary(elf)

    stage2 = link("stage2", [compile("manager/target/band11/stage2.c", "stage2"),
                             compile("runtime/loader/canopus_arm_reloc.c", "stage2-reloc"),
                             compile("runtime/loader/canopus_elf32_loader.c", "stage2-elf")])
    header.write_text(header.read_text() +
                      f"#define B11_STAGE2_SIZE {len(stage2)}u\n"
                      f"#define B11_STAGE2_CRC 0x{zlib.crc32(stage2):08x}u\n")
    stage1 = link("stage1", [compile("manager/target/band11/stage1.S", "stage1-entry"),
                             compile("manager/target/band11/stage1.c", "stage1")])
    if not 32 < len(stage1) < 16384 or len(stage2) > 32768:
        raise SystemExit(f"stage size out of budget: stage1={len(stage1)} stage2={len(stage2)}")
    if stage1[4:8] != bytes(4):
        raise SystemExit("stage1 mailbox patch offset drifted")

    profile = {
        "target_id": TARGET,
        "loader_family": "lua-owned-stage1-stage2",
        "status": "STATIC_RECOVERED",
        "device_status": "NOT_PROBED",
    }
    profile.update({key: addresses[macro] for key, macro in ADDRESS_MACROS.items()})
    profile.update({
        f"{name}_{key}": value
        for name, data in (("stage1", stage1), ("stage2", stage2), ("supervisor", payload))
        for key, value in (("size", len(data)), ("crc", zlib.crc32(data)))
    })
    profile_lua = "return {\n" + "".join(f"  {k} = {json.dumps(v)},\n" for k, v in profile.items())
    profile_lua += "  fingerprints = {\n" + "".join(
        f"    {{0x{a:08x}, 0x{w:08x}}},\n" for a, w in fingerprints) + "  },\n}\n"

    (out / f"canopus_stage1-{TARGET}.bin").write_bytes(stage1)
    (out / f"canopus_stage2-{TARGET}.bin").write_bytes(stage2)
    (out / f"canopus_supervisor-{TARGET}.bin").write_bytes(payload)
    (out / f"canopus_loader_profile-{TARGET}.bin").write_text(profile_lua, encoding="utf-8", newline="")

    print(f"stage1={len(stage1)} stage2={len(stage2)} supervisor={len(payload)} bytes")
    print(f"supervisor_crc=0x{zlib.crc32(payload):08x} (bound into rebuilt stage2)")
    print(f"resources written to {out}")


if __name__ == "__main__":
    main()
