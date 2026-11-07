"""ADS family cartridge list: short id -> No-Intro name. ROM paths resolve under ROMDIR."""
import os, json, hashlib

ROMDIR = os.environ.get("GBAV_ROMS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "roms")
P = "Game Boy Advance Video - "
CARTS = {
    "srmt":        P + "Super Robot Monkey Team - Hyper Force Go! - Volume 1 (USA)",
    "proud":       P + "The Proud Family - Volume 1 (USA)",
    "disney1":     P + "Disney Channel Collection - Volume 1 (USA)",
    "disney2":     P + "Disney Channel Collection - Volume 2 (USA) (Rev 5)",
    "cn-limited":  P + "Cartoon Network Collection - Limited Edition (USA)",
    "cn-platinum": P + "Cartoon Network Collection - Platinum Edition (USA, Europe)",
    "cn-premium":  P + "Cartoon Network Collection - Premium Edition (USA, Europe)",
    "cn-special":  P + "Cartoon Network Collection - Special Edition (USA, Europe)",
    "cn-vol2":     P + "Cartoon Network Collection - Volume 2 (USA, Europe)",
    "cnf-platinum": P + "Cartoon Network Collection - Edition Platinum (France)",
    "cnf-premium": P + "Cartoon Network Collection - Edition Premium (France)",
    "cnf-speciale": P + "Cartoon Network Collection - Edition Speciale (France)",
    "nick2":       P + "Nicktoons Collection - Volume 2 (USA)",
    "nick3":       P + "Nicktoons - Volume 3 (USA)",
    "sponge3":     P + "SpongeBob SquarePants - Volume 3 (USA)",
    "dbgt":        P + "Dragon Ball GT - Volume 1 (USA)",
    "tmnt-fr":     P + "Teenage Mutant Ninja Turtles - Le Demenagement (France)",
    "yugioh-fr":   P + "Yu-Gi-Oh! - Yugi vs. Joey (France)",
    "tmnt-proto":  P + "Teenage Mutant Ninja Turtles - The Shredder Strikes (USA) (Proto)",
    "jimmy":       P + "The Adventures of Jimmy Neutron Boy Genius - Volume 1 (USA)",
    "noah-proto":  P + "Yu-Gi-Oh! - Noah's Final Threat (USA) (Proto)",
}
UNAVAILABLE = [P + "Cartoon Network Collection - Special Edition (USA) (Rev 5)"]

def rom_path(cid):
    return os.path.join(ROMDIR, CARTS[cid] + ".gba")

def load(cid):
    return open(rom_path(cid), "rb").read()

def resolve(arg):
    """Accept a short id or a path."""
    if arg in CARTS:
        return arg, rom_path(arg)
    for k, v in CARTS.items():
        if os.path.basename(arg).startswith(v):
            return k, arg
    return None, arg

# Disney Channel Collection Vol 2 (Rev 5) is a 64 MiB "Matrix" cartridge whose upper 32 MiB are 0xFF.
# The No-Intro dump is the 32 MiB that holds data; mGBA enables its Matrix mapper only for ROMs larger
# than 32 MiB, so the emulator gets a copy padded back to 64 MiB with 0xFF.
PADDED = {"disney2": 64 << 20}
EMU_DIR = os.environ.get("ADS_EMU_ROMS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tmp", "ads", "emu", "roms")

def emu_rom_path(cid):
    if cid not in PADDED:
        return rom_path(cid)
    os.makedirs(EMU_DIR, exist_ok=True)
    p = os.path.join(EMU_DIR, cid + "_padded.gba")
    if not os.path.exists(p) or os.path.getsize(p) != PADDED[cid]:
        d = load(cid)
        open(p, "wb").write(d + b"\xff" * (PADDED[cid] - len(d)))
    return p

# Brightness: every player starts at level 5 (gain 256, identity). Jimmy Neutron's boot code adds
# 51 to the gain (global at 0x03003738) after its logo, so its intro and episodes play with gain 307.
def video_args(cid, stream):
    if cid == "jimmy" and stream not in ("res0",):
        return ["--gain-bias", "51"]
    return []
