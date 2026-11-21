#!/usr/bin/env python3
"""Pack the vibe-player installer watchface into a .face container.

Reproduces the layout of the validated vib-probe installer face:
  images/_lua/xiaomi-band-11/main.lua      -> type_element_lua entry
  images/_lua/xiaomi-band-11/canopus_*.bin -> extraJs entries (io.open-able
                                              resources via SCRIPT_PATH)

Requires: Java + Mi8WfBinTool (C:/Users/HP/WorkBuddy/Mi8WfBinTool).
Base face: Mi8WfBinTool/vib-probe-installer.face (unpack as the container
skeleton: wfDef.json, preview.png, pack/unpack conventions).

Usage:
  python native/scripts/pack_face.py <watchface-dir> <out.face> [--base <base.face>]
where <watchface-dir> contains main.lua + canopus_*.bin (as published on
the dist branch under vibe-player-watchface/).
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MI8_DIR = Path(r"C:/Users/HP/WorkBuddy/Mi8WfBinTool")
BASE_FACE = MI8_DIR / "vib-probe-installer.face"
LUA_DIR = "images/_lua/xiaomi-band-11"
EXTRA_FILES = [
    "canopus_loader_profile-xiaomi-band-11-4.100.179.bin",
    "canopus_stage1-xiaomi-band-11-4.100.179.bin",
    "canopus_stage2-xiaomi-band-11-4.100.179.bin",
    "canopus_supervisor-xiaomi-band-11-4.100.179.bin",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("watchface_dir", type=Path)
    ap.add_argument("out_face", type=Path)
    ap.add_argument("--base", type=Path, default=BASE_FACE)
    args = ap.parse_args()

    src: Path = args.watchface_dir
    if not (src / "main.lua").is_file():
        print(f"error: {src}/main.lua not found", file=sys.stderr)
        return 1
    for name in EXTRA_FILES:
        if not (src / name).is_file():
            print(f"error: {src}/{name} not found", file=sys.stderr)
            return 1

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pack_dir = td / "pack"
        subprocess.run(
            ["java", "-cp", ".", "Mi8WfBinTool", "unpack", str(args.base), str(pack_dir)],
            cwd=MI8_DIR, check=True, capture_output=True,
        )
        lua_dir = pack_dir / LUA_DIR
        for old in lua_dir.glob("*.bin"):
            old.unlink()  # drop previous payload entries
        shutil.copy2(src / "main.lua", lua_dir / "main.lua")
        for name in EXTRA_FILES:
            shutil.copy2(src / name, lua_dir / name)

        wfdef_path = pack_dir / "wfDef.json"
        wfdef = json.loads(wfdef_path.read_text(encoding="utf-8"))
        wfdef["name"] = "MIbandR Vibe Player"
        face = wfdef["faces"][0]
        face["extraJs"] = [
            {
                "idx": i,
                "name": f"{LUA_DIR}/{name}",
                "raw": (lua_dir / name).read_bytes().hex(),
            }
            for i, name in enumerate(EXTRA_FILES, 1)
        ]
        wfdef_path.write_text(json.dumps(wfdef, ensure_ascii=False, indent=1), encoding="utf-8")

        out = args.out_face.resolve()
        subprocess.run(
            ["java", "-cp", ".", "Mi8WfBinTool", "pack", str(pack_dir), str(out)],
            cwd=MI8_DIR, check=True,
        )
    data = out.read_bytes()
    if data[:4] != bytes.fromhex("5aa53412"):
        print("error: packed face has bad magic", file=sys.stderr)
        return 1
    print(f"packed {out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
