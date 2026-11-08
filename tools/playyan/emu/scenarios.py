"""Scripted input sequences. Each scenario is a function(r) where r is run.Runner.

Waits are tied to protocol events (commands seen, records posted) where possible, so the
sequences do not depend on exact frame counts.
"""


def boot(r):
    """Power on to the main menu."""
    if r.core.family == 3:
        return boot_nmp(r)
    r.until_cmd(0x8000, 900)
    r.step(20)
    r.shot("title")
    to_menu(r)
    r.shot("menu")
    r.mark("menu")


def nmp_to_menu(r):
    """Loader boot (0x8001), meteor.fup check, app start, health-and-safety screen."""
    r.until(lambda: r.model.booted, 1800, "NMP boot record")
    r.until_cmd(0x80, 900)                 # the app sends 0x0030 + 0x0080 on its first scene
    r.step(60)
    r.shot("safety")
    r.press("A", after=120)


def boot_nmp(r):
    nmp_to_menu(r)
    r.shot("menu")
    r.mark("menu")


def explore_nmp(r):
    nmp_to_menu(r)
    for i in range(12):
        r.step(40)
        r.shot("t%02d" % i)
    grid(r, "grid")


def explore(r, pre=None, n=16, every=30):
    """Debug helper: boot to the menu, optional input steps, then periodic screenshots."""
    to_menu(r)
    for p in (pre or []):
        if isinstance(p, int):
            r.step(p)
        else:
            r.press(p, after=20)
    for i in range(n):
        r.step(every)
        r.shot("t%02d" % i)


def grid(r, name, cols=4):
    from PIL import Image
    import os
    ims = [Image.open(p) for p in r.shots if "_t" in p]
    W = Image.new("RGB", (240 * cols, 160 * ((len(ims) + cols - 1) // cols)))
    for i, im in enumerate(ims):
        W.paste(im, ((i % cols) * 240, (i // cols) * 160))
    W.save(os.path.join(os.path.dirname(r.shots[0]), "%s_%s.png" % (r.tag, name)))


def to_menu(r):
    """Boot to the main menu (orig/micro): wait for the boot list request, then the intro.
    The Micro first shows a health-and-safety screen that needs a key press."""
    r.until_cmd(0x200, 1800)
    if r.core.family == 2:
        r.until_cmd(0xB00, 600)
        r.step(60)
        r.shot("safety")
        n = r.ncmd()
        r.press("A", after=1)
    else:
        n = r.ncmd()
    r.until_cmd(0xB00, 900, after=n)         # the menu scene sends 0x400 + 0xB00 on entry
    r.step(120)                              # menu input is ignored while it animates in


def explore_video(r):
    explore(r, ["A"], n=20, every=30)
    grid(r, "grid")


def to_video_browser(r):
    to_menu(r)
    r.press("A", after=10)                 # みる (watch): video firmware is loaded on demand
    r.until_cmd(0x500, 900)
    r.step(60)


def video(r):
    """Video browser, preview, playback, FF/REW, pause/step/step-back, brightness, volume,
    stop, resume (6-second rule)."""
    to_video_browser(r)
    r.shot("browser")
    n0 = len(r.model.cmd_log)
    r.step(240)                            # idle on the highlighted entry: live preview?
    r.shot("browser_idle")
    r.press("A", after=5)                  # play the highlighted file
    r.until_cmd(0x700, 300, after=n0)
    r.step(150)
    r.shot("play")
    r.mark("ff")
    r.hold("RIGHT", 120)                   # fast-forward
    r.shot("ff")
    r.step(60)
    r.mark("rew")
    r.hold("LEFT", 45)                     # rewind
    r.shot("rew")
    r.step(60)
    r.mark("pause")
    r.press("START", after=30)             # pause
    r.shot("paused")
    r.mark("step")
    for _ in range(3):
        r.press("RIGHT", after=20)         # step one frame
    r.shot("stepped")
    r.mark("stepback")
    r.hold("LEFT", 40)                     # step back
    r.step(60)
    r.shot("stepback")
    r.mark("resume_pause")
    r.press("A", after=60)                 # resume
    r.mark("bright")
    r.press("R", after=20)
    r.press("R", after=60)
    r.shot("bright2")
    r.press("L", after=10)
    r.press("L", after=30)
    r.mark("volume")
    r.press("DOWN", after=10)
    r.press("UP", after=30)
    r.step(300)
    r.shot("play2")
    r.mark("stop")
    n = r.nrec()
    r.press("B", after=5)                  # stop: stores the resume point
    r.until_rec(0x40000701, 300, after=n)
    r.step(120)
    r.shot("after_stop")
    r.mark("replay")
    r.press("A", after=40)                 # play again: "continue / from the start" prompt
    r.shot("resume_prompt")
    n = r.ncmd()
    r.press("A", after=5)                  # continue: resume 5 s before the stop point
    r.until_cmd(0x904, 300, after=n)
    r.step(120)
    r.shot("resumed")
    n = r.nrec()
    r.press("B", after=5)
    r.until_rec(0x40000701, 300, after=n)
    r.step(60)


def explore_music(r):
    explore(r, ["DOWN", "A", 120, "A", 60, "A"], n=12, every=40)
    grid(r, "grid")


def to_music_browser(r):
    to_menu(r)
    r.press("DOWN", after=10)              # きく (listen): the music firmware is already loaded
    n = r.ncmd()
    r.press("A", after=10)
    r.until_cmd(0x200, 600, after=n)
    r.step(120)


def music(r):
    """Folder navigation, ID3, playback with trackbar, FF/REW (absolute 0x905), pause,
    stop, play to the end of the song."""
    to_music_browser(r)
    r.shot("browser")
    n = r.ncmd()
    r.press("A", after=10)                 # enter MUSIC
    r.until_cmd(0x200, 300, after=n)
    r.step(60)
    n = r.ncmd()
    r.press("A", after=10)                 # enter Sub
    r.until_cmd(0x200, 300, after=n)
    r.step(60)
    r.shot("sub")
    n = r.ncmd()
    r.press("A", after=10)                 # play second_song
    r.until_cmd(0x800, 300, after=n)
    r.step(240)
    r.shot("play")
    r.mark("ff")
    r.hold("RIGHT", 150)
    r.shot("ff")
    r.step(60)
    r.mark("rew")
    r.hold("LEFT", 60)
    r.step(60)
    r.shot("rew")
    r.mark("pause")
    r.press("START", after=60)
    r.shot("paused")
    r.press("START", after=60)
    r.mark("seek_end")
    n = r.nrec()
    r.hold("A+RIGHT", 300)                 # A held: maximum step at once
    r.until_rec(0x40000801, 3600, after=n)  # end of song (cartridge-initiated)
    r.step(120)
    r.shot("after_end")


def garage(r):
    """Select AVOID.ASF in the video browser; with a thumbnail whose hash matches the table the
    browser starts Garage Game 1 instead of the player."""
    to_video_browser(r)
    r.press("RIGHT", after=60)             # cursor to AVOID
    r.shot("cursor")
    r.mark("launch")
    r.press("A", after=10)
    for i in range(8):
        r.step(60)
        r.shot("t%02d" % i)
    r.press("A", after=60)
    for i in range(8, 12):
        r.step(60)
        r.shot("t%02d" % i)
    grid(r, "grid")


def explore_preview(r):
    to_video_browser(r)
    r.step(900)
    r.mark("idle900")
    for b in ["SELECT", "R", "L", "START", "DOWN", "UP", "RIGHT", "LEFT"]:
        r.mark("btn_" + b)
        r.press(b, after=120)
        r.shot("t_" + b)


def explore_boot(r):
    r.until_cmd(0x200, 2400)
    for i in range(16):
        r.step(30)
        r.shot("t%02d" % i)
    grid(r, "grid")


def explore_micro(r):
    explore(r, [], n=12, every=40)
    grid(r, "grid")


def explore_micro_video(r):
    explore(r, ["RIGHT", "A"], n=12, every=40)
    grid(r, "grid")


def explore_micro_video2(r):
    explore(r, ["RIGHT", "A", 300, "UP", 60, "UP", 60, "UP", 60, "UP", 60, "UP"], n=8, every=40)
    grid(r, "grid")


def nmp_music(r):
    """NMP: listing (one entry per command), folder change, ID3, play, FF/REW (absolute 0x0060),
    pause, stop, end of song."""
    nmp_to_menu(r)
    r.step(200)
    r.shot("browser")
    n = r.ncmd()
    r.press("A", after=60)                 # enter Album
    r.until_cmd(0x10, 300, after=n)
    r.step(120)
    r.shot("album")
    n = r.ncmd()
    r.press("A", after=60)                 # play track2 (first press walks to it)
    if r.until_cmd(0x50, 120, after=n) is None:
        r.press("A", after=10)
        r.until_cmd(0x50, 300, after=n)
    r.step(240)
    r.shot("play")
    r.mark("ff")
    r.hold("RIGHT", 150)
    r.step(30)
    r.shot("ff")
    r.mark("rew")
    r.hold("LEFT", 60)
    r.step(60)
    r.shot("rew")
    r.mark("pause")
    r.press("START", after=60)
    r.shot("paused")
    r.press("START", after=60)
    r.mark("seek_end")
    n = r.nrec()
    r.hold("A+RIGHT", 300)
    r.until_rec(0x4051, 3600, after=n)
    r.step(120)
    r.shot("after_end")
    r.mark("stop")
    n = r.nrec()
    r.step(200)
    r.press("B", after=60)
    r.shot("after_b")


def nmp_ring(r):
    """Play, then compare the GBA's channel-1 ring (0x0300110C) with the samples posted: shows
    in which order the GBA code stores the two samples of each DSP word."""
    nmp_to_menu(r)
    r.step(200)
    n = r.ncmd()
    r.press("A", after=60)
    r.until_cmd(0x10, 300, after=n)
    r.step(120)
    n = r.ncmd()
    r.press("A", after=60)
    if r.until_cmd(0x50, 120, after=n) is None:
        r.press("A", after=10)
        r.until_cmd(0x50, 300, after=n)
    r.step(200)
    import numpy as np
    ring = np.frombuffer(r.core.gba_read(r.core.gba_u32(0x0300110C), 0x900), np.int8)
    src = r.model.music["audio"][:, 1] if r.model.music else None   # right channel -> ring 1
    res = {}
    for name, s in (("in_order", src), ("pair_swapped", src.reshape(-1, 2)[:, ::-1].reshape(-1))):
        best = 0
        for off in range(0, 0x900 - 64, 64):
            win = ring[off:off + 64]
            if not win.any():
                continue
            idx = np.nonzero((np.lib.stride_tricks.sliding_window_view(s[:200000], 64) == win).all(1))[0]
            if len(idx):
                best += 1
        res[name] = best
    r.core.note("RING CHECK %s" % res)
    print("ring check (64-sample windows of ring 1 found in source):", res)
    r.model.log("ring_check", **res)


def micro_video(r):
    """Micro: key file at boot, firmware switch to video, MP4 and ASF playback, FF, pause, stop."""
    to_menu(r)
    r.shot("menu")
    r.press("RIGHT", after=10)
    n = r.ncmd()
    r.press("A", after=10)
    r.until_cmd(0x500, 900, after=n)
    r.step(120)
    for _ in range(4):                     # page up to the first page
        n = r.ncmd()
        r.press("UP", after=60)
    r.press("LEFT", after=20)
    r.press("LEFT", after=40)
    r.shot("browser")
    n = r.ncmd()
    r.press("A", after=10)                 # MP4sample_movie.mp4
    r.until_cmd(0x700, 300, after=n)
    r.step(200)
    r.shot("play_mp4")
    r.mark("ff")
    r.hold("RIGHT", 90)
    r.shot("ff")
    r.mark("pause")
    r.press("START", after=30)
    r.press("RIGHT", after=20)
    r.shot("paused_step")
    r.press("A", after=60)
    r.mark("stop")
    n = r.nrec()
    r.press("B", after=5)
    r.until_rec(0x40000701, 300, after=n)
    r.step(120)
    r.press("RIGHT", after=40)
    r.shot("browser2")
    n = r.ncmd()
    r.press("A", after=10)                 # sample_movie.asf
    r.until_cmd(0x700, 300, after=n)
    r.step(200)
    r.shot("play_asf")
    n = r.nrec()
    r.press("B", after=5)
    r.until_rec(0x40000701, 300, after=n)
    r.step(60)


def micro_music(r):
    """Micro: music browser, folders, ID3, play, FF."""
    to_menu(r)
    n = r.ncmd()
    r.press("A", after=10)
    r.until_cmd(0x200, 600, after=n)
    r.step(120)
    r.shot("browser")
    for name in ("MUSIC", "Sub"):
        n = r.ncmd()
        r.press("A", after=10)
        r.until_cmd(0x200, 300, after=n)
        r.step(90)
    r.shot("sub")
    n = r.ncmd()
    r.press("A", after=10)
    r.until_cmd(0x800, 300, after=n)
    r.step(240)
    r.shot("play")
    r.mark("ff")
    r.hold("RIGHT", 120)
    r.step(30)
    r.shot("ff")
    n = r.nrec()
    r.press("B", after=5)
    r.until_rec(0x40000801, 300, after=n)
    r.step(60)
    r.shot("stopped")


def sleep(r):
    """Auto-sleep from the video browser: L+R arms it, 30 s idle -> 0x10000 SLEEP + BIOS Stop.
    After waking the GBA re-kicks VC 0x10000400 (30 frames later) and waits for 0x80010001.
    (mGBA's Stop returns at once without a key press, so the wake follows immediately.)"""
    to_video_browser(r)
    r.press("L+R", after=30)
    r.shot("armed")
    n = r.ncmd()
    m = r.nrec()
    r.until_cmd(0x10000, 2400, after=n)
    r.mark("asleep")
    r.until_rec(0x80010001, 600, after=m)
    r.step(120)
    r.shot("awake")
    n = r.ncmd()
    r.press("RIGHT", after=60)             # the browser still answers after the wake handshake
    r.shot("after_wake")


def nmp_idle(r):
    """NMP: boot to the menu, then idle 1800 frames (tick-watchdog experiments)."""
    nmp_to_menu(r)
    r.step(1800)
    r.shot("end")


def stereo(r):
    """Play sd/stereo/stereo_L440_R1500.mp3 (left 440 Hz, right 1500 Hz) and record the GBA's
    output: checks that the first half of each audio block (the model's RIGHT channel) comes out
    of the right speaker (FIFO A, SOUNDCNT_H = 0xA90E)."""
    r.core.retro.keep_audio = False
    to_music_browser(r)
    n = r.ncmd()
    r.press("A", after=60)
    if r.until_cmd(0x800, 120, after=n) is None:
        r.press("A", after=10)
        r.until_cmd(0x800, 300, after=n)
    r.step(60)
    r.core.retro.audio.clear()
    r.core.retro.keep_audio = True
    r.step(300)
    r.core.retro.keep_audio = False
    import numpy as np
    a = np.frombuffer(bytes(r.core.retro.audio), np.int16).reshape(-1, 2).astype(float)
    rate = r.core.retro.av.rate
    res = {}
    for ch, nm in ((0, "left_out"), (1, "right_out")):
        x = a[:, ch] - a[:, ch].mean()
        X = np.abs(np.fft.rfft(x))
        f = np.fft.rfftfreq(len(x), 1 / rate)
        res[nm] = dict(peak_hz=round(float(f[np.argmax(X)]), 1), rms=round(float(x.std()), 1))
    print("stereo check:", res)
    r.core.note("STEREO CHECK %s" % res)
    r.model.log("stereo_check", **res)


def keyfile(r):
    """Micro: boot, then report the key-file status byte and the applied settings
    (0x03002800: status, +2 Design, +3 StartMode, +4 volume, ..., +9 VideoBright)."""
    to_menu(r)
    r.shot("menu")
    st = list(r.core.gba_read(0x03002800, 12))
    print("keyfile settings:", st)
    r.core.note("KEYFILE SETTINGS %s" % st)
    r.model.log("keyfile_settings", raw=st)


def video_nostream(r):
    """As the start of 'video', but with the RAM-test flag (0x03002B18) forced to 0 (test failed):
    frame reads should then pulse CTRL bit 0 per 0x200-byte chunk instead of holding it."""
    to_video_browser(r)
    r.core.retro.write(0x03002B18, b"\0")
    r.core.note("FORCED RAM-test flag 0x03002B18 = 0")
    n = r.ncmd()
    r.press("A", after=5)
    r.until_cmd(0x700, 300, after=n)
    r.step(120)
    r.shot("play")


def _garage_n(r, k, name):
    to_video_browser(r)
    for _ in range(k):
        r.press("RIGHT", after=30)
    r.mark("launch_" + name)
    n = r.ncmd()
    r.press("A", after=10)
    for i in range(4):
        r.step(90)
        r.press("A", after=30)
    r.shot(name)
    ids = sorted({"%X" % c for _, c, _ in r.model.cmd_log[n:]})
    print("garage", name, "commands after launch:", ids)
    r.model.log("garage_launch", game=name, commands=ids)


def garage_all(r):
    """Launch one Garage Game from the first browser page (env GARAGE_K = cursor steps right,
    1 AVOID .. 5 FISH) and list the cart commands it sends."""
    import os
    k = int(os.environ.get("GARAGE_K", "1"))
    name = ["sample", "AVOID", "BAT", "CREDIT", "FIRE", "FISH"][k]
    _garage_n(r, k, name)


def video_end(r):
    """Play a 10 s file to its end (run with --opt garage_force=false so AVOID.ASF is a plain
    video): the model sends 0x40000701 at the end; what does the GBA do next?"""
    to_video_browser(r)
    r.press("RIGHT", after=30)
    n = r.ncmd()
    r.press("A", after=10)
    r.until_cmd(0x700, 300, after=n)
    m = r.nrec()
    r.until_rec(0x40000701, 900, after=m)
    r.mark("ended")
    r.step(180)
    r.shot("after_end")
    print("after end:", ["%X" % c for _, c, _ in r.model.cmd_log[n:]])


def nmp_play_sample(r, secs=None, down=1):
    import os
    """NMP: from the root browser, select the file `down` entries below the first (sd/nmp: Album,
    sample_music.mp3, stereo_L440_R1500.mp3), play it for `secs` seconds without touching the keys.
    For the LLE audio comparisons (docs/playyan/c54x-hardware.md)."""
    secs = float(secs or os.environ.get("NMP_SECS", 20))
    nmp_to_menu(r)
    r.step(200)
    r.shot("browser")
    for _ in range(int(os.environ.get("NMP_DOWN", down))):
        r.press("DOWN", after=20)
    n = r.ncmd()
    r.press("A", after=30)
    if r.until_cmd(0x50, 300, after=n) is None:
        r.press("A", after=10)
        r.until_cmd(0x50, 300, after=n)
    r.mark("play")
    r.step(int(secs * 60))
    r.shot("playing")


def video_plain(r):
    """Play the highlighted video (sample_movie.asf) for VIDEO_SECS seconds (default 12) without
    touching the keys; with LLE_FRAMES=dir[:n] the LLE model dumps the frames (pre-bridge RGB565)."""
    import os
    to_video_browser(r)
    n = r.ncmd()
    r.press("A", after=5)
    r.until_cmd(0x700, 300, after=n)
    r.step(int(float(os.environ.get("VIDEO_SECS", 12)) * 60))
    r.shot("play")
