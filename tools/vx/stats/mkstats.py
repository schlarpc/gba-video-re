#!/usr/bin/env python3
"""stats.json for the VX family: one record per (cart sha1, stream), brief schema.
Inputs: work/bitstats.json (bitstats.py), perf/runs/*/summary.json (vx_analyze.py),
work/verify_*.json (verify_vx.py --json)."""
import glob
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
WORK = os.path.join(ROOT, "tmp", "vx")  # bitstats.json and verify_vx.py --json outputs (verify_<cart>.json)
NOINTRO = {
    "shrek-rev6": "Game Boy Advance Video - Shrek (USA) (Rev 6)",
    "shrek-rev5": "Game Boy Advance Video - Shrek (USA) (Rev 5)",
    "shrek2-rev6": "Game Boy Advance Video - Shrek 2 (USA) (Rev 6)",
    "shrek2-rev5": "Game Boy Advance Video - Shrek 2 (USA) (Rev 5)",
    "sharktale-rev6": "Game Boy Advance Video - Shark Tale (USA) (Rev 6)",
    "sharktale-rev5": "Game Boy Advance Video - Shark Tale (USA) (Rev 5)",
    "shrek-sharktale-rev5": "Game Boy Advance Video - Shrek + Shark Tale (USA) (Rev 5)",
}
TITLE = {("shrek-sharktale-rev5", 0x20200): "Shrek", ("shrek-sharktale-rev5", 0x1eef800): "Shark Tale",
         ("shrek-sharktale-rev5", 0x3dd3600): "Shrek (credits)", ("shrek-sharktale-rev5", 0x3e76a00): "Shark Tale (credits)"}
BUILD = {"vxpp": "VX++ 344d68b1e9aa", "vxgb": "VXGB 0600eda5896e"}


def main():
    bs = json.load(open(os.path.join(WORK, "bitstats.json")))
    ver = {}
    for p in glob.glob(os.path.join(WORK, "verify_*.json")):
        ver.update(json.load(open(p)))
    out = []
    for key, b in bs.items():
        cart, off = b["cart"], b["stream"]
        run = f"{cart}_{off:x}"
        S = json.load(open(os.path.join(ROOT, "tools", "vx", "perf", "runs", run, "summary.json")))
        v = ver.get(f"{cart}:{off:#x}")
        title = TITLE.get((cart, off)) or {"shrek": "Shrek", "shrek2": "Shrek 2", "sharktale": "Shark Tale"}[cart.split("-")[0]]
        sh = S["shares"]
        aw = S["audio_window"]
        notes = []
        notes.append(f"QP {b['header_qp']} from header 0x14" if b["magic"] == "VXGB" else "QP per frame (ue(v) header)")
        notes.append(f"decode cycles/frame mean {S['decode_cyc']['mean']/1e3:.0f}k p99 {S['decode_cyc']['p99']/1e3:.0f}k max {S['decode_cyc']['max']/1e3:.0f}k")
        notes.append(f"late_or_dropped = {S['late_frames']} frames shown after their Timer3 tick (max lag {S['late_max_ticks']} ticks) + {S['skipped_blits']} skipped blits")
        notes.append(f"Matrix map commands {S['matrix_cmds']}; audio-window refill min margin {aw['margin']['min']/16777.216:.1f} ms")
        verified_audio = None
        if v:
            if v["video"]["vram_skipped_blit"]:
                notes.append(f"{v['video']['vram_skipped_blit']} screens show the previous frame (blit skipped by the player); counted as verified")
            if "audio" in v:
                a = v["audio"]
                verified_audio = f"{a['blocks_ok']}/{a['blocks']}"
                if a["first_mismatch"] is not None:
                    notes.append(f"audio: the emulated cart loses an audio-window refill request at AFrame {a['first_mismatch']} "
                                  f"(VX frame 16191) and plays misaligned AFrames to the end; vxaudio.py matches every block before it and "
                                  f"the player's ARM audio routine on all AFrames (perf-vx.md 4); {a['ok_after_first_mismatch']} later blocks match by coincidence")
        if aw["lost_requests"]:
            notes.append("lost refill request deterministic in mGBA; real hardware very likely the same (perf-vx.md 4.3)")
        notes.append("keyframes = seek-table entries (VX has no frame types; intra/inter is per block)")
        if off in (0x3dd3600, 0x3e76a00):
            notes.append("one segment; chapter table holds only frame 0")
        if b["height"] == 112:
            notes.append("240x112 letterbox, centred at screen row 24")
        out.append({
            "nointro": NOINTRO[cart], "sha1": S["rom_sha1"], "family": "VX", "player_build": BUILD[S["profile"]],
            "stream": f"{off:#x}", "title": title,
            "width": b["width"], "height": b["height"], "fps": round(b["fps"], 5), "frames": b["frames"],
            "duration_s": round(b["duration_s"], 2),
            "video_bytes": b["video_bytes"], "audio_bytes": b["audio_bytes"],
            "video_kbps_mean": round(b["video_kbps_mean"], 2), "video_kbps_p99_1s": round(b["video_kbps_p99_1s"], 2),
            "audio_kbps": round(b["audio_kbps"], 2),
            "audio_codec": "Actimagine VX LPC/pulse (trained 3124-B codebook, 20/14/12/10-B AFrames)",
            "audio_rate_hz": 16384, "audio_channels": 1, "audio_out_rate_hz": 65536,
            "keyframes": b["seek_points"], "seek_points": b["seek_points"],
            "cpu_busy_mean": round(sh["busy"], 4), "cpu_busy_p99": round(S["busy_window"]["p99"], 4),
            "cpu_busy_max": round(S["busy_window"]["max"], 4),
            "video_decode_share": round(sh["video"], 4), "audio_decode_share": round(sh["audio"], 4),
            "blit_share": round(sh["blit"], 4),
            "late_or_dropped_frames": S["late_frames"] + S["skipped_blits"],
            "verified_video": f"{v['video']['yuv_ok']}/{v['video']['frames']}" if v else None,
            "verified_audio": verified_audio,
            "notes": "; ".join(notes)})
    json.dump(out, open(os.path.join(ROOT, "docs", "vx", "stats.json"), "w"), indent=1)
    print(f"{len(out)} records")


if __name__ == "__main__":
    main()
