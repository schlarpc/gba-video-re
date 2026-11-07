#!/usr/bin/env python3
"""Generate the cart and stream tables of the suite from the per-family measurements.

    python3 tools/gen_tables.py          # rewrites docs/carts.md and the summary in README.md
    python3 tools/gen_tables.py --check  # exit 1 if either file is out of date

Inputs (all in the repository):
  crates/gbav-rom/src/nointro.csv   the 47 No-Intro DAT entries with family, engine and player build
  verify/carts.csv                  short ids used in manifest names
  docs/<family>/stats.json          one record per (cart, stream):
      nointro, sha1, family, player_build, stream, title, width, height, fps, frames, duration_s,
      video_bytes, audio_bytes, video_kbps_mean, video_kbps_p99_1s, audio_kbps, audio_codec,
      audio_rate_hz, audio_channels, audio_out_rate_hz, keyframes, seek_points,
      cpu_busy_mean/p99/max, video_decode_share, audio_decode_share, blit_share (fractions of all
      CPU time), late_or_dropped_frames, verified_video / verified_audio ("ok/total"), notes

Outputs:
  docs/carts.md   Table 1 (per No-Intro entry), Table 2 (per stream), Table 3 (family comparison)
  README.md       the block between <!-- BEGIN gen_tables:summary --> and <!-- END gen_tables:summary -->
"""
import csv
import json
import os
import statistics
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STATS = ["vx", "fvmv", "hydrogen", "sonicx2-proto", "ads"]
PREFIX = "Game Boy Advance Video - "
FAMILY_NAME = {"vx": "VX", "fvmv": "FVMV", "hydrogen": "Hydrogen", "ads": "ADS"}
FAMILY_DOC = {"vx": "docs/vx/README.md", "fvmv": "docs/fvmv/README.md", "hydrogen": "docs/hydrogen/README.md",
              "ads": "docs/ads/README.md"}

# Why a cart's counts are not all equal. The counts themselves come from stats.json.
EXCEPTIONS = {
    "shrek2-rev5": "audio: the cart itself loses an audio refill request at block 296,039 in mGBA and plays "
                   "misaligned audio after it; every block before that matches ([docs/vx](vx/README.md#open-issues))",
    "noah-proto": "video: in the intro the cart falls behind and shows a stale codebook for 41 frames; the "
                  "decoder output is the intended picture, and the stale frames are reproduced exactly by "
                  "`tools/ads/explain_stale.py`",
    "cnf-platinum": "audio: the last block of movie_3 is never decoded by the cart, so it has no ground truth",
    "nick3": "audio: the last block of movie_2 is never decoded by the cart, so it has no ground truth",
}


def read_csv(path):
    with open(os.path.join(ROOT, path), newline="") as f:
        return list(csv.DictReader(f))


def load_stats():
    recs = []
    for fam in STATS:
        p = os.path.join(ROOT, "docs", fam, "stats.json")
        with open(p) as f:
            for r in json.load(f):
                r["_doc"] = fam
                recs.append(r)
    return recs


def frac(s):
    a, b = (int(x) for x in s.split("/"))
    return a, b


def fmt_int(n):
    return f"{n:,}"


def fmt_hms(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pct(x):
    return f"{100 * x:.0f}" if x is not None else "-"


def short_name(nointro):
    return nointro[len(PREFIX):] if nointro.startswith(PREFIX) else nointro


def variant(r):
    """Player build as the family names it (VX: VX++/VXGB; FVMV: UI build A/B)."""
    pb = str(r["player_build"])
    if pb.startswith(("VX++", "VXGB")):
        return pb.split()[0]
    if "/" in pb:
        return "build " + pb.split("/")[1]
    return ""


def build_id(r):
    pb = str(r["player_build"])
    return pb.split()[-1].split("/")[0]


def table1(entries, by_sha, short):
    out = ["| No-Intro name | family | engine | player build | streams | hours | video frames verified | audio blocks verified | status |",
           "|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        recs = by_sha.get(e["sha1"], [])
        name = short_name(e["name"])
        fam = FAMILY_NAME.get(e["family"], e["family"] or "?")
        build = f"`{e['build']}`" if e["build"] else "unknown"
        if not recs:
            out.append(f"| {name} | {fam} | {e['engine'] or '?'} | {build} | - | - | - | - | **not available** to this project; unverified |")
            continue
        v = [frac(r["verified_video"]) for r in recs]
        a = [frac(r["verified_audio"]) for r in recs]
        vok, vtot = sum(x for x, _ in v), sum(y for _, y in v)
        aok, atot = sum(x for x, _ in a), sum(y for _, y in a)
        hours = sum(r["duration_s"] for r in recs) / 3600
        sid = short.get(e["sha1"], "")
        var = sorted({variant(r) for r in recs} - {""})
        bstr = build + (f" ({', '.join(var)})" if var else "")
        if vok == vtot and aok == atot:
            status = "bit-exact"
        else:
            status = "bit-exact except as noted: " + EXCEPTIONS.get(sid, "see the family docs")
        out.append(f"| {name} | [{fam}]({os.path.relpath(FAMILY_DOC[e['family']], 'docs')}) | {e['engine']} | {bstr} | {len(recs)} | "
                   f"{hours:.2f} | {fmt_int(vok)} / {fmt_int(vtot)} | {fmt_int(aok)} / {fmt_int(atot)} | {status} |")
    return out


def table2(recs, short):
    out = ["| cart | stream | title | size | fps | duration | video kbit/s mean (1-s p99) | audio kbit/s | CPU busy mean / p99 / max % | video / audio / blit % of CPU | late frames | video verified | audio verified |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        sid = short.get(r["sha1"], short_name(r["nointro"]))
        title = (r["title"] or "").replace("|", "/")
        late = "-" if r["late_or_dropped_frames"] is None else fmt_int(r["late_or_dropped_frames"])
        out.append(f"| {sid} | {r['stream']} | {title} | {r['width']}x{r['height']} | {r['fps']:.4g} | {fmt_hms(r['duration_s'])} | "
                   f"{r['video_kbps_mean']:.1f} ({r['video_kbps_p99_1s']:.0f}) | {r['audio_kbps']:.1f} | "
                   f"{pct(r['cpu_busy_mean'])} / {pct(r['cpu_busy_p99'])} / {pct(r['cpu_busy_max'])} | "
                   f"{pct(r['video_decode_share'])} / {pct(r['audio_decode_share'])} / {pct(r['blit_share'])} | {late} | "
                   f"{r['verified_video']} | {r['verified_audio']} |")
    return out


# Qualitative rows of Table 3, drawn from docs/<family>/features.md. Order of columns: COLS.
COLS = ["VX++", "VXGB", "FVMV", "Hydrogen", "ADS", "AM3"]
FEATURES = [
    ("developer / engine", ["Actimagine; Actimagine loader, 64 MiB Matrix mapper", "same as VX++",
                            "Actimagine codec and player; ARM SDT image", "Majesco Hydrogen library (\"crt table\" engine)",
                            "Majesco ADS engine (SFCD archive + .mmstr); also 2 Hydrogen-engine carts", "Actimagine Loader 1.0.620 on the adapter; player on the card"]),
    ("codec type", ["hybrid block codec: prediction + 4x4 transform", "same as VX++", "block-based pixel-domain codec (no transform)",
                    "vector quantisation, 1 byte per block", "vector quantisation, 1 byte per block", "unknown (needs a card dump)"]),
    ("colour", ["YUV 4:2:0, shift-only YUV->BGR555, 2x2 dither", "same", "RGB555 directly (no YUV)",
                "YCbCr codebook -> RGB555 per segment, 4x4 ordered dither, brightness gain", "same as Hydrogen", "?"]),
    ("blocks", ["16x16 MBs, binary split tree to 2x2, 12 modes", "same", "8x8 cells, recursive halving to 1x2/2x1, 7 leaf modes",
                "4x4, 4x3, 3x3 or 4x2 per segment", "same as Hydrogen", "?"]),
    ("intra / inter", ["per block; MC from 3 references, integer-pel", "same", "per block; MV copy from 1 reference, integer-pel, +-8",
                       "every frame a full raster of codebook indices; no MC", "same as Hydrogen", "?"]),
    ("transform / residual", ["H.264 4x4 integer IDCT", "same", "none (colour offset and fills)", "none", "none", "?"]),
    ("entropy coding", ["Exp-Golomb + MPEG-4 intra TCOEF VLC", "Exp-Golomb + H.264 CAVLC tables, own syntax, nC grids",
                        "fixed prefix codes, raw colours and MV bytes", "DEFLATE variant (codebook + index blobs per segment)",
                        "LZMA (lc0 lp0 pb2, 128 KiB dictionary)", "?"]),
    ("rate control", ["constant QP (per-frame QP syntax)", "one QP per stream (header)", "no quantiser; encoder mode choice",
                      "codebook per segment; no quantiser", "codebook per segment; no quantiser", "?"]),
    ("keyframes / seek", ["seek table every ~200 frames", "same", "seek points every 3-225 frames, linked records",
                          "segment = keyframe = seek point (median 32 frames)", "segment = keyframe; chapters point at segments", "?"]),
    ("audio codec", ["Actimagine LPC/pulse, trained codebook, 20/14/12/10-B frames", "same",
                     "stripped GSM 06.10 RPE-LPC, 40 B per 256 samples", "Majesco ADPCM (flat 5-bit context table)",
                     "Majesco ADPCM (VLC context table)", "?"]),
    ("audio output", ["16,384 Hz -> 4x interpolation + noise shaping -> 65,536 Hz s8", "same", "same output stage as VX",
                      "16,384 Hz s8, locked to the frame clock", "16,384 Hz s8", "?"]),
]


def column_of(r):
    if r["family"] == "VX":
        return variant(r)
    return {"FVMV": "FVMV", "Hydrogen": "Hydrogen", "ADS": "ADS"}[r["family"]]


def rng(vals, f="{:.0f}"):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "-"
    lo, hi = min(vals), max(vals)
    return f.format(lo) if f.format(lo) == f.format(hi) else f"{f.format(lo)}-{f.format(hi)}"


def table3(recs):
    by = {c: [r for r in recs if column_of(r) == c] for c in COLS}
    out = ["| | " + " | ".join(COLS) + " |", "|---|" + "---|" * len(COLS)]
    for name, cells in FEATURES:
        out.append(f"| {name} | " + " | ".join(cells) + " |")

    def row(name, fn):
        out.append(f"| {name} | " + " | ".join(fn(by[c]) if by[c] else "-" for c in COLS) + " |")

    row("carts / streams", lambda rs: f"{len({r['sha1'] for r in rs})} / {len(rs)}")
    row("picture", lambda rs: ", ".join(sorted({f"{r['width']}x{r['height']}" for r in rs}, reverse=True)))
    row("fps", lambda rs: ", ".join(f"{x:g}" for x in sorted({round(r['fps'], 4) for r in rs})))
    long = lambda rs: [r for r in rs if r["duration_s"] >= 300] or rs  # episodes and movies, not logos
    row("video kbit/s, mean per stream (median)", lambda rs: f"{rng([r['video_kbps_mean'] for r in long(rs)])} ({statistics.median(r['video_kbps_mean'] for r in long(rs)):.0f})")
    row("audio kbit/s", lambda rs: rng([r["audio_kbps"] for r in long(rs)], "{:.1f}"))
    row("CPU busy, stream mean %", lambda rs: rng([100 * r["cpu_busy_mean"] for r in long(rs)]))
    row("CPU busy, worst frame window %", lambda rs: f"{100 * max(r['cpu_busy_max'] for r in rs):.0f}")
    row("video decode % of CPU", lambda rs: rng([100 * r["video_decode_share"] for r in long(rs)]))
    row("audio decode % of CPU", lambda rs: rng([100 * r["audio_decode_share"] for r in long(rs)]))
    row("blit % of CPU", lambda rs: rng([100 * r["blit_share"] for r in long(rs)]))
    row("late frames (all streams)", lambda rs: fmt_int(sum(r["late_or_dropped_frames"] or 0 for r in rs)))
    return out


def summary(entries, recs):
    out = ["| family | carts (No-Intro) | streams | hours | video frames verified | audio blocks verified | docs |",
           "|---|---|---|---|---|---|---|"]
    fams = ["vx", "fvmv", "hydrogen", "ads"]
    for fam in fams:
        rs = [r for r in recs if r["family"].lower() == fam]
        es = [e for e in entries if e["family"] == fam]
        avail = len({r["sha1"] for r in rs})
        v = [frac(r["verified_video"]) for r in rs]
        a = [frac(r["verified_audio"]) for r in rs]
        vals = [avail, len(es), len(rs), sum(r["duration_s"] for r in rs) / 3600,
                sum(x for x, _ in v), sum(y for _, y in v), sum(x for x, _ in a)]
        atot = sum(y for _, y in a)
        out.append(f"| [{FAMILY_NAME[fam]}]({FAMILY_DOC[fam]}) | {avail} of {len(es)} | {len(rs)} | {vals[3]:.1f} | "
                   f"{fmt_int(vals[4])} / {fmt_int(vals[5])} | {fmt_int(vals[6])} / {fmt_int(atot)} | [{fam}]({FAMILY_DOC[fam]}) |")
    allv = [frac(r["verified_video"]) for r in recs]
    alla = [frac(r["verified_audio"]) for r in recs]
    out.append(f"| **all** | {len({r['sha1'] for r in recs})} of {len(entries)} | {len(recs)} | "
               f"{sum(r['duration_s'] for r in recs) / 3600:.1f} | {fmt_int(sum(x for x, _ in allv))} / {fmt_int(sum(y for _, y in allv))} | "
               f"{fmt_int(sum(x for x, _ in alla))} / {fmt_int(sum(y for _, y in alla))} | [carts.md](docs/carts.md) |")
    out.append("| [AM3](docs/am3/README.md) (adapter + SmartMedia cards) | not in the DAT | - | - | firmware loader only | - | [am3](docs/am3/README.md) |")
    return out


def carts_md(entries, recs, short, by_sha):
    order = {e["sha1"]: i for i, e in enumerate(entries)}
    srt = sorted(recs, key=lambda r: (order.get(r["sha1"], 999), str(r["stream"]).zfill(12)))
    L = ["# GBA Video carts: the No-Intro table",
         "",
         "<!-- Generated by tools/gen_tables.py from crates/gbav-rom/src/nointro.csv, verify/carts.csv and",
         "     docs/<family>/stats.json. Do not edit by hand. -->",
         "",
         "Every entry of the No-Intro *Nintendo - Game Boy Advance (Video)* DAT (version 20261006-104433),",
         "with the codec family, the player build (`gbav-rom` build id: sha1 of the player code image with",
         "ROM pointers masked), the streams on the cart and how much of them is verified bit-exact against",
         "the cart's own code running in mGBA. \"Verified\" counts are frames (video) and audio blocks as each",
         "family's manifest defines them; see [README.md](../README.md#verification) and the family docs.",
         "",
         "## Table 1: carts",
         ""]
    L += table1(entries, by_sha, short)
    L += ["", "## Table 2: streams", "",
          "One row per stream. Rates are over the coded stream; the 1-s p99 is the 99th percentile of a",
          "one-second sliding window. CPU figures come from an instrumented mGBA run over the whole stream",
          "(\"busy\" = everything except the player's idle wait; p99 and max are over frame windows), cross-checked",
          "against a GBATEK cycle model in unicorn. Late frames are frames shown after their scheduled time.",
          "Cart ids are the short ids of [verify/carts.csv](../verify/carts.csv).", ""]
    L += table2(srt, short)
    L += ["", "## Table 3: families compared", "",
          "Qualitative rows summarise the families' `features.md`; the numeric rows are computed from the",
          "streams above (episodes and movies only, not logos, for the rate and load ranges).", ""]
    L += table3(recs)
    L += [""]
    return "\n".join(L)


def main():
    entries = read_csv("crates/gbav-rom/src/nointro.csv")
    short = {r["sha1"]: r["short_id"] for r in read_csv("verify/carts.csv")}
    recs = load_stats()
    by_sha = {}
    for r in recs:
        by_sha.setdefault(r["sha1"], []).append(r)
    unknown = set(by_sha) - {e["sha1"] for e in entries}
    if unknown:
        sys.exit(f"stats.json records for carts not in nointro.csv: {sorted(unknown)}")
    md = carts_md(entries, recs, short, by_sha)
    readme_p = os.path.join(ROOT, "README.md")
    readme = open(readme_p).read()
    b, e = "<!-- BEGIN gen_tables:summary -->", "<!-- END gen_tables:summary -->"
    if b in readme and e in readme:
        head, rest = readme.split(b, 1)
        _, tail = rest.split(e, 1)
        new_readme = head + b + "\n" + "\n".join(summary(entries, recs)) + "\n" + e + tail
    else:
        new_readme = readme
    carts_p = os.path.join(ROOT, "docs", "carts.md")
    if "--check" in sys.argv:
        old = open(carts_p).read() if os.path.exists(carts_p) else ""
        stale = [p for p, new, cur in ((carts_p, md, old), (readme_p, new_readme, readme)) if new != cur]
        if stale:
            sys.exit("out of date: " + ", ".join(os.path.relpath(p, ROOT) for p in stale))
        return
    open(carts_p, "w").write(md)
    if new_readme != readme:
        open(readme_p, "w").write(new_readme)
    print(f"docs/carts.md: {len(entries)} carts, {len(recs)} streams")


if __name__ == "__main__":
    main()
