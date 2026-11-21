#!/usr/bin/env bash
# Build the MIbandR vibe-player payload (zero-import ELF32 ET_REL, ARM Cortex-M33).
# Requires clang with the arm-none-eabi target and ld.lld (CI installs LLVM).
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."

CC="${CC:-clang}"
OBJCOPY="${OBJCOPY:-llvm-objcopy}"
OUT="${OUT:-out}"
TARGET_INC="target"
SRC="payload/vibe_player.c"

mkdir -p "$OUT"

CFLAGS="--target=arm-none-eabi -mcpu=cortex-m33 -mthumb -mfloat-abi=soft \
  -ffreestanding -fno-common -fno-builtin -fno-jump-tables \
  -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables \
  -fdata-sections -fno-function-sections -Os -Wall -Wextra -Werror"

echo "[1/3] compile payload (Cortex-M33 Thumb soft-float)"
"$CC" $CFLAGS -I"$TARGET_INC" -c "$SRC" -o "$OUT/vibe_player.o"

echo "[2/3] relocatable link (ld.lld -r)"
ld.lld -r -T loader/sections.ld -o "$OUT/vibe_player.elf" "$OUT/vibe_player.o"

echo "[3/3] strip debug/unneeded (keep relocations, exports, constructors)"
"$OBJCOPY" --strip-debug --strip-unneeded \
    --remove-section=.ARM.exidx --remove-section=.rel.ARM.exidx \
    "$OUT/vibe_player.elf" "$OUT/vibe_player.stripped.elf"
mv "$OUT/vibe_player.stripped.elf" "$OUT/vibe_player.elf"

SIZE=$(wc -c < "$OUT/vibe_player.elf")
echo "payload built: $OUT/vibe_player.elf ($SIZE bytes)"
# Loader budget: same slot as the Canopus supervisor (135168 B max per family).
[ "$SIZE" -le 135168 ] || { echo "error: payload exceeds loader budget"; exit 1; }
