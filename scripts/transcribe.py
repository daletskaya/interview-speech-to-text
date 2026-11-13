#!/usr/bin/env python
"""Распознаёт речь и сам чинит места, где модель сорвалась.

    transcribe.py АУДИО.wav ВЫХОД.json [--glossary ПУТЬ] [--model ПУТЬ]

Делает три прохода:
  1. основной — по всему файлу;
  2. чинящий — куски, где модель зациклилась, потеряла пунктуацию
     или пропустила речь, перераспознаются поодиночке без оглядки
     на предыдущий текст;
  3. словарь — замены из глоссария.

Второй проход обязателен. Whisper на русском регулярно срывается:
выдаёт «Продолжение следует...» вместо речи, печатает поток строчными
без единого знака препинания или молча пропускает тихие реплики.
Виновата оглядка на предыдущий текст (condition_on_previous_text):
раз сбившись, модель тянет сбой дальше. На коротком куске без оглядки
тот же участок распознаётся нормально.

Выход: JSON со списком segments (start, end, text, words).
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np

def ffmpeg_env():
    """Окружение для ffmpeg с подстраховкой от битой libtiff.

    На части маков хомбрю оставляет ffmpeg со ссылкой на старую libtiff.
    Если такая версия ещё лежит в Cellar, подставляем её. На здоровой
    машине это просто лишние пути в списке — вреда нет.
    """
    env = dict(os.environ)
    old = [p for p in glob.glob("/usr/local/Cellar/libtiff/*/lib")
           + glob.glob("/opt/homebrew/Cellar/libtiff/*/lib") if os.path.isdir(p)]
    if old:
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
            old + ["/usr/local/lib", "/opt/homebrew/lib", "/usr/lib"])
    return env


ENV = ffmpeg_env()

def find_model():
    """Ищет уже скачанную модель. Качать заново долго, а она часто есть."""
    roots = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/models--*faster-whisper*/snapshots/*"))
    big = [r for r in roots if "large" in r.lower()]
    for r in sorted(big or roots, reverse=True):
        if os.path.exists(os.path.join(r, "model.bin")):
            return r
    return None

# Что Whisper выдаёт на русском вместо речи, когда срывается.
JUNK = ["продолжение следует", "субтитры", "спасибо за просмотр",
        "подписывайтесь", "редактор субтитров", "фонд кино"]

TEMPS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def load_glossary(path):
    terms, swaps = [], []
    if not path or not os.path.exists(path):
        return terms, swaps
    section = None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#"):
            low = line.lower()
            section = "terms" if "термин" in low else "swaps" if "замен" in low else None
            continue
        if not line or section is None:
            continue
        if section == "terms":
            terms.append(line)
        elif "->" in line:
            a, b = line.split("->", 1)
            swaps.append((a.strip(), b.strip()))
    return terms, swaps


def duration(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def loudness(path, start, length):
    out = subprocess.run(
        ["ffmpeg", "-ss", str(start), "-t", str(length), "-i", path,
         "-af", "volumedetect", "-vn", "-f", "null", "-"],
        env=ENV, capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].replace("dB", "").strip())
    return -99.0


def segments_of(result):
    out = []
    for s in result:
        out.append({"start": s.start, "end": s.end, "text": s.text,
                    "words": [{"word": w.word, "start": w.start, "end": w.end}
                              for w in (s.words or [])]})
    return out


def shifted(segs, offset):
    for s in segs:
        s["start"] += offset
        s["end"] += offset
        for w in s["words"]:
            w["start"] += offset
            w["end"] += offset
    return segs


def find_broken(segs, audio, total):
    """Возвращает окна (начало, конец), которые надо перераспознать."""
    bad = []

    for s in segs:                                   # галлюцинации
        low = s["text"].strip().lower()
        if any(j in low for j in JUNK):
            bad.append((s["start"], s["end"]))

    for s in segs:                                   # потерянная пунктуация
        if len(s["text"]) > 25 and not any(c in s["text"] for c in ".,?!—"):
            bad.append((s["start"], s["end"]))

    prev = 0.0                                       # пропуски, в которых есть звук
    for s in sorted(segs, key=lambda x: x["start"]):
        if s["start"] - prev > 4 and loudness(audio, prev, s["start"] - prev) > -40:
            bad.append((prev, s["start"]))
        prev = max(prev, s["end"])
    if total - prev > 4 and loudness(audio, prev, total - prev) > -40:
        bad.append((prev, total))

    bad.sort()
    merged = []                                      # склеиваем соседние, добавляем поля
    for a, b in bad:
        a, b = max(0.0, a - 3), min(total, b + 3)
        if merged and a <= merged[-1][1] + 5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a > 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("out")
    ap.add_argument("--glossary")
    ap.add_argument("--model", help="папка с моделью; по умолчанию ищется сама")
    args = ap.parse_args()

    model_path = args.model or find_model()
    if not model_path or not os.path.exists(model_path):
        sys.exit("не нашла модель распознавания.\n"
                 "Скачай Systran/faster-whisper-large-v3 или укажи папку "
                 "через --model. Если загрузка виснет — см. references/okruzhenie.md.")

    from faster_whisper import WhisperModel

    terms, swaps = load_glossary(args.glossary)
    prompt = ("Разговор о приложении. Термины: " + ", ".join(terms) + ".") if terms else None
    if prompt:
        print(f"подсказка модели: {prompt}", flush=True)

    model = WhisperModel(model_path, device="cpu", compute_type="int8",
                         cpu_threads=8, local_files_only=True)
    total = duration(args.audio)

    print(">>> основной проход", flush=True)
    segs, _ = model.transcribe(args.audio, language="ru", word_timestamps=True,
                               vad_filter=False, beam_size=5,
                               condition_on_previous_text=True,
                               initial_prompt=prompt)
    segs = segments_of(segs)
    print(f"    сегментов: {len(segs)}", flush=True)

    windows = find_broken(segs, args.audio, total)
    print(f">>> чиним участков: {len(windows)}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        piece = os.path.join(tmp, "piece.wav")
        for a, b in windows:
            print(f"    {a:.0f}–{b:.0f} с", flush=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(a),
                            "-t", str(b - a), "-i", args.audio, piece],
                           env=ENV, check=True)
            fixed, _ = model.transcribe(piece, language="ru", word_timestamps=True,
                                        vad_filter=False, beam_size=5,
                                        condition_on_previous_text=False,
                                        temperature=TEMPS)
            fixed = shifted(segments_of(fixed), a)
            segs = [s for s in segs if not (a <= s["start"] < b)] + fixed

    segs.sort(key=lambda s: s["start"])

    # Замены только с начала слова: иначе замена сработает внутри другого
    # слова и склеит бессмыслицу. Конец слова свободен, чтобы в словаре
    # можно было писать основу: «крикабельн» поймает «крикабельная».
    for s in segs:
        for wrong, right in swaps:
            pat = r"(?<!\w)" + re.escape(wrong)
            s["text"] = re.sub(pat, right, s["text"], flags=re.IGNORECASE)
            for w in s["words"]:
                w["word"] = re.sub(pat, right, w["word"], flags=re.IGNORECASE)

    json.dump({"segments": segs}, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False)
    print(f"готово: {len(segs)} сегментов -> {args.out}")


if __name__ == "__main__":
    main()
