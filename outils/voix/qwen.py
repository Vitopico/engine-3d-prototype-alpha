# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""Voix par Qwen3-TTS (Alibaba, Apache 2.0) sur CPU.
Usage : python outils/voix/qwen.py <travail.json> design|clone
  design : fabrique une voix de référence par personnage (VoiceDesign, voix inventée d'après une description),
           puis chaque réplique avec la description + l'émotion (méthode A1) → out/refs/*.wav, out/A1/*.wav
  clone  : copie la voix de référence (Base) pour chaque réplique (méthode A2) → out/A2/*.wav
"""
import json, os, sys, time
import numpy as np, soundfile as sf, torch
from qwen_tts import Qwen3TTSModel

job = json.load(open(sys.argv[1]))

def prononce(txt):
    """Remplacements de prononciation donnés par le travail (job["prononciation"] = {écrit: dit})."""
    for a, b in job.get("prononciation", {}).items():
        txt = txt.replace(a, b)
    return txt

stage = sys.argv[2]
SEEDS = job.get("seeds", [1, 2])
torch.set_num_threads(os.cpu_count() or 2)
os.makedirs("out/refs", exist_ok=True)

def save(path, wavs, sr):
    w = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    w = np.asarray(w, dtype=np.float32).squeeze()
    sf.write(path, w, sr, subtype="PCM_16")
    return len(w) / sr

def lang_call(fn, **kw):
    for lang in ("French", "french", "fr", "Auto"):
        try:
            return fn(language=lang, **kw)
        except Exception as e:
            err = e
    raise err

t0 = time.time()
if stage == "design":
    m = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", device_map="cpu", dtype=torch.float32)
    print(f"VoiceDesign chargé en {time.time()-t0:.0f} s", flush=True)
    for who, c in job["chars"].items():
        torch.manual_seed(11)
        wavs, sr = lang_call(m.generate_voice_design, text=c["ref_text"], instruct=c["design"] + " Neutral, friendly tone.")
        d = save(f"out/refs/{who}.wav", wavs, sr)
        print(f"réf {who}: {d:.1f} s", flush=True)
    os.makedirs("out/A1", exist_ok=True)
    for it in job["lines"]:
        c = job["chars"][it["who"]]
        for s in SEEDS:
            torch.manual_seed(s)
            t1 = time.time()
            wavs, sr = lang_call(m.generate_voice_design, text=prononce(it["text"]), instruct=c["design"] + " Emotion: " + it["emo"] + ".")
            d = save(f"out/A1/{it['id']}__s{s}.wav", wavs, sr)
            print(f"A1 {it['id']} s{s}: {d:.1f} s audio en {time.time()-t1:.0f} s", flush=True)
elif stage == "clone":
    m = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-Base", device_map="cpu", dtype=torch.float32)
    print(f"Base chargé en {time.time()-t0:.0f} s", flush=True)
    os.makedirs("out/A2", exist_ok=True)
    prompts = {}
    for who, c in job["chars"].items():
        try:
            prompts[who] = m.create_voice_clone_prompt(ref_audio=f"out/refs/{who}.wav", ref_text=c["ref_text"])
        except Exception as e:
            print("prompt", who, e, flush=True)
    for it in job["lines"]:
        c = job["chars"][it["who"]]
        for s in SEEDS:
            torch.manual_seed(s)
            t1 = time.time()
            txt = prononce(it["text"])
            if it["who"] in prompts:
                wavs, sr = lang_call(m.generate_voice_clone, text=txt, voice_clone_prompt=prompts[it["who"]])
            else:
                wavs, sr = lang_call(m.generate_voice_clone, text=txt, ref_audio=f"out/refs/{it['who']}.wav", ref_text=c["ref_text"])
            d = save(f"out/A2/{it['id']}__s{s}.wav", wavs, sr)
            print(f"A2 {it['id']} s{s}: {d:.1f} s audio en {time.time()-t1:.0f} s", flush=True)
print(f"fini en {time.time()-t0:.0f} s", flush=True)
