# SPDX-License-Identifier: GPL-3.0-or-later
"""Compositeur de musique originale : écrit des morceaux en MIDI à partir d'un motif (celui de l'univers s'il en donne
un, univers.MOTIF), puis FluidSynth (LGPL) les joue avec la police de sons FluidR3_GM (MIT).
Aucune musique existante n'est reprise. Engine 3D Prototype (Alpha) — GPL-3.0-or-later.

Ambiances : merveille (célesta, harpe, cordes douces), aventure (cordes en pizzicato, cors, timbales, plus vif),
tension (mineur, cordes graves en trémolo, piano rare), joie (marimba, flûte, petites percussions).
Usage : python musique.py <dossier> [ambiance=duree ...]   ex. python musique.py out merveille=30 aventure=20
"""
import os, sys, struct, math, random

MOTIF = [0, 2, 4, 7, 9, 12]             # gamme pentatonique (demi-tons au-dessus de la tonique)
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import univers_charge
    MOTIF = univers_charge.get("MOTIF", MOTIF) or MOTIF
except Exception:
    pass

def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))

class Piste:
    def __init__(self, canal, programme=None, nom=""):
        self.c = canal
        self.ev = []                       # (temps en pulsations, octets)
        if nom:
            b = nom.encode("utf-8")
            self.ev.append((0, b"\xff\x03" + vlq(len(b)) + b))
        if programme is not None:
            self.ev.append((0, bytes([0xC0 | canal, programme])))

    def note(self, t, dur, hauteur, vel=80):
        if 0 <= hauteur < 128:
            self.ev.append((t, bytes([0x90 | self.c, hauteur, max(1, min(vel, 127))])))
            self.ev.append((t + max(dur, 1), bytes([0x80 | self.c, hauteur, 0])))

    def cc(self, t, num, val):
        self.ev.append((t, bytes([0xB0 | self.c, num, val])))

    def octets(self):
        evs = sorted(self.ev, key=lambda e: (e[0], e[1][0] & 0xF0 == 0x90))
        data, last = b"", 0
        for t, b in evs:
            data += vlq(t - last) + b
            last = t
        data += b"\x00\xff\x2f\x00"
        return b"MTrk" + struct.pack(">I", len(data)) + data

def ecrire_midi(path, pistes, tempo_bpm, ppq=480):
    tempo = b"\x00\xff\x51\x03" + struct.pack(">I", int(60_000_000 / tempo_bpm))[1:] + b"\x00\xff\x2f\x00"
    head = b"MThd" + struct.pack(">IHHH", 6, 1, len(pistes) + 1, ppq)
    with open(path, "wb") as f:
        f.write(head + b"MTrk" + struct.pack(">I", len(tempo)) + tempo + b"".join(p.octets() for p in pistes))

AMBIANCES = {
    #            tempo, tonique, mode, instruments (canal: programme GM)
    "merveille": (72, 60, "majeur", {"melodie": 8, "harpe": 46, "cordes": 49, "basse": 42}),
    "aventure":  (112, 62, "majeur", {"melodie": 60, "harpe": 45, "cordes": 48, "basse": 43, "timbales": 47}),
    "tension":   (66, 57, "mineur", {"melodie": 0, "harpe": 46, "cordes": 44, "basse": 42}),
    "joie":      (120, 65, "majeur", {"melodie": 12, "harpe": 73, "cordes": 48, "basse": 32}),
}
ACCORDS = {"majeur": [[0, 4, 7], [5, 9, 12], [7, 11, 14], [9, 12, 16]],      # I IV V vi
           "mineur": [[0, 3, 7], [8, 12, 15], [5, 8, 12], [7, 10, 14]]}      # i VI iv v

def composer(nom, duree, graine=7):
    tempo, ton, mode, inst = AMBIANCES[nom]
    rnd = random.Random(graine)
    ppq = 480
    mesure = 4 * ppq
    n_mes = max(2, int(round(duree * tempo / 60 / 4)))
    canaux = {}
    pistes = []
    for i, (role, prog) in enumerate(inst.items()):
        c = i if i < 9 else i + 1
        p = Piste(c, prog, role)
        p.cc(0, 7, {"melodie": 100, "harpe": 80, "cordes": 70, "basse": 85, "timbales": 70}[role])
        p.cc(0, 91, 70)                    # réverbération
        canaux[role] = p
        pistes.append(p)
    motif = [m - 3 if mode == "mineur" and m in (4, 9) else m for m in MOTIF]
    for m in range(n_mes):
        t0 = m * mesure
        acc = ACCORDS[mode][m % 4]
        fin = m == n_mes - 1
        if fin:
            acc = ACCORDS[mode][0]
        # cordes : accord tenu (trémolo en tension)
        for h in acc:
            if nom == "tension":
                for k in range(16):
                    canaux["cordes"].note(t0 + k * ppq // 4, ppq // 4 - 10, ton - 12 + h, 50 + 10 * (k % 2))
            else:
                canaux["cordes"].note(t0, mesure - 20, ton - 12 + h, 60 if not fin else 70)
        # basse
        b = ton - 24 + acc[0]
        if nom in ("aventure", "joie"):
            for k in range(4):
                canaux["basse"].note(t0 + k * ppq, ppq - 40, b + (7 if k % 2 else 0), 80)
        else:
            canaux["basse"].note(t0, mesure - 40, b, 70)
        # harpe / accompagnement : arpège montant
        pas = ppq // 2 if nom in ("merveille", "tension") else ppq // 4
        for k in range(mesure // pas):
            h = acc[k % 3] + 12 * ((k // 3) % 2)
            if nom == "tension" and k % 2:
                continue
            canaux["harpe"].note(t0 + k * pas, pas * 2, ton + h, 55 + rnd.randint(-6, 6))
        # mélodie : le motif, exposé puis varié
        if m % 2 == 0 or fin:
            notes = motif if (m // 2) % 2 == 0 else list(reversed(motif))[2:] + [motif[0]]
            dur = ppq // 2 if nom in ("aventure", "joie") else ppq
            for k, h in enumerate(notes[: mesure // dur]):
                if fin and k >= 3:
                    break
                canaux["melodie"].note(t0 + k * dur, dur - 30, ton + 12 + h, 88 + (8 if k == 0 else 0))
            if fin:
                canaux["melodie"].note(t0 + 3 * dur, mesure, ton + 12 + motif[0] + 12, 80)
        else:                                  # réponse plus calme sur les notes de l'accord
            for k, h in enumerate([acc[2], acc[1], acc[0] + 12]):
                canaux["melodie"].note(t0 + k * ppq * 4 // 3, ppq, ton + 12 + h, 70)
        # timbales (aventure) : temps forts
        if "timbales" in canaux and (m % 2 == 1 or fin):
            canaux["timbales"].note(t0, ppq, ton - 24, 90)
            canaux["timbales"].note(t0 + 3 * ppq, ppq, ton - 17, 75)
        # petites percussions (joie) sur le canal 9
    if nom == "joie":
        perc = Piste(9, None, "percussions")
        for m in range(n_mes):
            for k in range(8):
                perc.note(m * mesure + k * ppq // 2, 60, 42 if k % 2 else 39, 60 if k % 2 else 75)   # charleston, frappé de mains
        pistes.append(perc)
    return pistes, tempo

if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    demandes = dict(a.split("=") for a in sys.argv[2:]) or {"merveille": "30", "aventure": "20", "tension": "20", "joie": "20"}
    for nom, d in demandes.items():
        pistes, tempo = composer(nom, float(d))
        ecrire_midi(os.path.join(out, f"theme_{nom}.mid"), pistes, tempo)
        print("composé", nom, d, "s")
