# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""Méthode A3 : Chatterbox Multilingual (MIT) avec les voix de référence inventées par Qwen3-TTS.
Usage : python outils/voix/cbx.py <travail.json>  (lit out/refs/*.wav, écrit out/A3/*.wav)"""
import json, os, sys, time
import numpy as np, soundfile as sf, torch
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

job = json.load(open(sys.argv[1]))

def prononce(txt):
    """Remplacements de prononciation donnés par le travail (job["prononciation"] = {écrit: dit})."""
    for a, b in job.get("prononciation", {}).items():
        txt = txt.replace(a, b)
    return txt

SEEDS = job.get("seeds", [1, 2])
torch.set_num_threads(os.cpu_count() or 2)
t0 = time.time()
model = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
os.makedirs("out/A3", exist_ok=True)
for it in job["lines"]:
    for s in SEEDS:
        torch.manual_seed(s); np.random.seed(s)
        t1 = time.time()
        wav = model.generate(prononce(it["text"]), language_id="fr", audio_prompt_path=f"out/refs/{it['who']}.wav",
                             exaggeration=it.get("exag", 0.5), cfg_weight=0.4, temperature=0.8)
        w = wav.squeeze(0).numpy().astype(np.float32)
        sf.write(f"out/A3/{it['id']}__s{s}.wav", w, model.sr, subtype="PCM_16")
        print(f"A3 {it['id']} s{s}: {len(w)/model.sr:.1f} s audio en {time.time()-t1:.0f} s", flush=True)
print(f"fini en {time.time()-t0:.0f} s", flush=True)
