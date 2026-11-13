"""15 original, synthesized alert sounds (no samples, no existing melodies). 4.6 s each, loud, mono 44.1 kHz.
Telegram limit: under 5 s and under 300 KB -> 4.6 s MP3 at 128 kbps is about 75 KB."""
import numpy as np, subprocess, os, json, sys
SR = 44100; DUR = 4.6
OUT = sys.argv[1]
t_all = np.arange(int(SR * DUR)) / SR

def env(n, a=0.005, r=0.05):
    e = np.ones(n); ai, ri = int(a * SR), int(r * SR)
    if ai: e[:ai] = np.linspace(0, 1, ai)
    if ri: e[-ri:] = np.linspace(1, 0, ri)
    return e

def tone(f, d, wave="sine", a=0.005, r=0.05, decay=None):
    n = int(d * SR); t = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(np.broadcast_to(f, (n,)) if np.ndim(f) else np.full(n, f)) / SR
    if wave == "sine": s = np.sin(ph)
    elif wave == "square": s = np.tanh(4 * np.sin(ph))
    elif wave == "saw": s = 2 * ((ph / (2 * np.pi)) % 1) - 1
    elif wave == "tri": s = 2 * np.abs(2 * ((ph / (2 * np.pi)) % 1) - 1) - 1
    s = s * env(n, a, r)
    if decay: s = s * np.exp(-t / decay)
    return s

def bell(f, d, decay=0.9):
    n = int(d * SR); t = np.arange(n) / SR; s = np.zeros(n)
    for ratio, amp, dk in ((1, 1, 1), (2.76, 0.5, 0.6), (5.4, 0.3, 0.35), (8.9, 0.15, 0.2), (0.5, 0.35, 1.3)):
        s += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t / (decay * dk))
    return s * env(n, 0.002, 0.03)

def pluck(f, d):
    n = int(d * SR); p = int(SR / f); buf = np.random.default_rng(int(f)).uniform(-1, 1, p); out = np.zeros(n)
    for i in range(n):
        out[i] = buf[i % p]; buf[i % p] = 0.996 * 0.5 * (buf[i % p] + buf[(i + 1) % p])
    return out

def piano(f, d):
    n = int(d * SR); t = np.arange(n) / SR; s = np.zeros(n)
    for k, amp in enumerate((1, 0.6, 0.35, 0.2, 0.12, 0.08), 1):
        s += amp * np.sin(2 * np.pi * f * k * t * (1 + 0.0004 * k * k)) * np.exp(-t * (1.2 + 0.7 * k))
    return s * env(n, 0.003, 0.05)

def brass(f, d):
    n = int(d * SR); t = np.arange(n) / SR
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.5 * t) * np.clip(t * 3, 0, 1)
    s = sum(a * np.sin(2 * np.pi * f * k * vib * t) for k, a in ((1, 1), (2, .8), (3, .6), (4, .45), (5, .3), (6, .2)))
    return np.tanh(1.5 * s) * env(n, 0.03, 0.08)

def place(seq):
    out = np.zeros(len(t_all))
    for start, s in seq:
        i = int(start * SR)
        if i >= len(out): continue
        j = min(len(out), i + len(s)); out[i:j] += s[: j - i]
    return out

N = lambda m: 440 * 2 ** ((m - 69) / 12)  # midi -> Hz

def master(x):
    x = x - x.mean()
    x = x / (np.max(np.abs(x)) + 1e-9)
    drive = 2.2
    while True:   # soft limiter: push quiet sounds up until every sound is loud (mean level at least -9 dBFS)
        y = np.tanh(drive * x); y = y / np.max(np.abs(y)) * 0.97
        if 20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-12) >= -9 or drive > 12:
            break
        drive *= 1.3
    x = y
    f = int(0.03 * SR); x[-f:] *= np.linspace(1, 0, f)
    return x

S = {}
# ---- Rush / alarm (5)
sw = 700 + 500 * np.sin(2 * np.pi * 1.6 * t_all)
S["rush-1-siren"] = ("Rush", "Siren sweep", tone(sw, DUR, "square"))
S["rush-2-fast-beeps"] = ("Rush", "Fast beeps", place([(k * 0.15, tone(2400, 0.09, "square")) for k in range(30)]))
S["rush-3-klaxon"] = ("Rush", "Klaxon", place([(k * 0.75, tone(np.linspace(380, 520, int(.55 * SR)), .55, "saw") + .6 * tone(np.linspace(570, 780, int(.55 * SR)), .55, "saw")) for k in range(6)]))
S["rush-4-red-alert"] = ("Rush", "Red alert", place([(k * 0.5, tone(np.linspace(500, 1400, int(.42 * SR)), .42, "saw")) for k in range(9)]))
S["rush-5-buzzer"] = ("Rush", "Buzzer pulses", place([(k * 0.45, tone(160, .3, "square") + .7 * tone(320, .3, "square") + .5 * tone(1280, .3, "square")) for k in range(10)]))
# ---- Ringtone style (5)
def trill(f1, f2, d, rate=20):
    n = int(d * SR); tt = np.arange(n) / SR; sel = (np.floor(tt * rate) % 2).astype(bool)
    return np.where(sel, np.sin(2 * np.pi * f1 * tt), np.sin(2 * np.pi * f2 * tt)) * env(n, .01, .03)
S["ring-1-classic"] = ("Ringtone", "Classic phone ring", place([(k * 2.0, trill(1150, 1450, 1.4)) for k in range(3)]))
S["ring-2-double"] = ("Ringtone", "Double ring", place([(o + k * 1.6, tone(450, .38, "square") + tone(400, .38, "square")) for k in range(3) for o in (0, .58)]))
S["ring-3-old-bell"] = ("Ringtone", "Old bell ringer", place([(k * 2.0 + j * 0.05, bell(1900 + 60 * (j % 2), .35, .25)) for k in range(3) for j in range(26)]))
S["ring-4-digital"] = ("Ringtone", "Digital ring", place([(k * 1.15 + j * 0.13, tone(N(m), .11, "square", .002, .02)) for k in range(4) for j, m in enumerate((88, 91, 88, 91, 95, 91))]))
S["ring-5-marimba"] = ("Ringtone", "Wooden ring", place([(k * 1.5 + j * 0.16, piano(N(m), .5) * 1.0) for k in range(3) for j, m in enumerate((72, 79, 76, 84, 79, 76, 79, 72))]))
# ---- Instrumental (5)
S["inst-1-piano"] = ("Instrumental", "Piano rise", place([(j * 0.22, piano(N(m), 1.6)) for j, m in enumerate((60, 64, 67, 72, 76, 79, 84, 79, 84, 88))] + [(2.4, sum(piano(N(m), 2.2) for m in (60, 67, 72, 76, 84)))]))
S["inst-2-fanfare"] = ("Instrumental", "Brass fanfare", place([(s, brass(N(m), d)) for s, m, d in ((0, 67, .25), (.3, 67, .25), (.6, 72, .7), (1.4, 71, .25), (1.7, 72, .25), (2.0, 76, 1.2), (3.3, 79, 1.2))]))
S["inst-3-chimes"] = ("Instrumental", "Bright chimes", place([(j * 0.28, bell(N(m), 1.8, .7)) for j, m in enumerate((84, 88, 91, 96, 91, 88, 93, 96, 100, 96, 100, 103, 108))]))
S["inst-4-guitar"] = ("Instrumental", "Guitar strum", place([(k * 1.1 + j * 0.025, pluck(N(m), 1.2)) for k, chord in enumerate(((52, 59, 64, 67, 71, 76), (57, 64, 69, 72, 76), (55, 62, 67, 71, 74, 79), (52, 59, 64, 68, 71, 76))) for j, m in enumerate(chord)]))
S["inst-5-synth"] = ("Instrumental", "Synth arpeggio", place([(j * 0.115, tone(N(m), .2, "saw", .003, .06, .12) + .5 * tone(N(m + 12), .2, "square", .003, .06, .1)) for j, m in enumerate([60, 64, 67, 72, 76, 72, 67, 64] * 5)]))

meta = []
os.makedirs(OUT, exist_ok=True)
for key, (grp, name, x) in S.items():
    x = master(x)
    wav = f"/tmp/{key}.wav"; mp3 = os.path.join(OUT, key + ".mp3")
    import scipy.io.wavfile as w
    w.write(wav, SR, (x * 32767).astype(np.int16))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k", mp3], check=True)
    meta.append({"id": key, "group": grp, "name": name, "file": f"/sounds/{key}.mp3", "kb": round(os.path.getsize(mp3) / 1024)})
json.dump(meta, open(os.path.join(OUT, "sounds.json"), "w"), indent=1)
for m in meta: print(m)
