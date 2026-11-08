#!/bin/sh
# Run the real Play-Yan video firmware (LLE: VC01 emulator under the patched mGBA running the real
# GBA ROM) on one ASF file and dump every 240x160 RGB565 frame it sends to the GBA.
# usage: fw_frames.sh FILE.asf OUTDIR [VIDEO_SECS] [rom: orig|micro]
set -e
F=$(realpath "$1"); OUT=$(realpath -m "$2"); SECS=${3:-12}; ROM=${4:-orig}
N=$(basename "$F" | tr -c 'A-Za-z0-9_\n' _)
SD=$(realpath -m "$OUT.sd"); rm -rf "$SD" "$OUT"; mkdir -p "$SD"; cp "$F" "$SD/"
cd "$(dirname "$0")/../emu"
SCN=video_plain; [ "$ROM" = micro ] && SCN=micro_video
LLE_FRAMES="$OUT" VIDEO_SECS=$SECS python3 run.py $ROM $SCN --lle --sd "$SD" --tag "dec_$N" > "$OUT.log" 2>&1
echo "$N: $(ls "$OUT" 2>/dev/null | grep -c bin) frames"
