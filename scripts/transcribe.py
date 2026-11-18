#!/usr/bin/env python
"""Распознаёт речь и сам чинит места, где модель сорвалась.

    transcribe.py АУДИО.wav ВЫХОД.json [--engine auto|mlx|faster]
                                       [--glossary ПУТЬ] [--model ПУТЬ]

Движков два, модель одна и та же — Whisper:
  mlx     считает на видеоядрах маков с процессором Apple. Полчаса записи —
          полторы-две минуты. Выбирается сам, если установлен mlx-whisper.
  faster  считает на процессоре, работает везде. Полчаса записи — около
          сорока минут.

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

Тайм-коды отдельных слов считаются только с ключом --words. Они нужны,
когда говорящих размечают по голосу (voices.py): границы сегментов у Whisper
грубые и захватывают паузы и чужие реплики, а по словам видно, где фраза
на самом деле началась и кончилась. Для разметки по картинке хватает сегментов.

Выход: JSON со списком segments (start, end, text, words). Без --words
список words пустой.
"""
import argparse
import glob
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import tempfile
import time
import wave

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

def find_faster_model():
    """Ищет уже скачанную модель. Качать заново долго, а она часто есть."""
    roots = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/models--*faster-whisper*/snapshots/*"))
    big = [r for r in roots if "large" in r.lower()]
    for r in sorted(big or roots, reverse=True):
        if os.path.exists(os.path.join(r, "model.bin")):
            return r
    return None


MLX_REPO = "mlx-community/whisper-large-v3-turbo"


def find_mlx_model():
    """Локальная папка с моделью для mlx, а если её нет — имя для скачивания."""
    roots = (glob.glob(os.path.expanduser("~/.cache/whisper-models/mlx-*"))
             + glob.glob(os.path.expanduser(
                 "~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3*/snapshots/*")))
    for r in sorted(roots, key=lambda x: "turbo" not in x):
        if glob.glob(os.path.join(r, "weights.*")) and os.path.exists(os.path.join(r, "config.json")):
            return r
    return MLX_REPO


class MlxEngine:
    """Whisper на видеоядрах Apple. Раз в двадцать быстрее процессора."""
    name = "mlx"

    def __init__(self, model):
        import mlx_whisper
        self.lib = mlx_whisper
        self.model = model or find_mlx_model()
        if self.model == MLX_REPO:
            print("модель скачается при первом запуске, около 1,6 ГБ", flush=True)

    def run(self, path, prompt, conditioned):
        r = self.lib.transcribe(path, path_or_hf_repo=self.model, language="ru",
                                initial_prompt=prompt, verbose=None,
                                condition_on_previous_text=conditioned,
                                word_timestamps=WORDS)
        return [{"start": float(s["start"]), "end": float(s["end"]),
                 "text": s["text"],
                 "words": [{"word": w["word"], "start": float(w["start"]),
                            "end": float(w["end"])} for w in s.get("words") or []]}
                for s in r["segments"]]


class FasterEngine:
    """Whisper на процессоре. Медленно, зато работает на любой машине."""
    name = "faster"

    def __init__(self, model):
        self.model_path = model or find_faster_model()
        if not self.model_path or not os.path.exists(self.model_path):
            sys.exit("модель распознавания не найдена.\n"
                     "Скачай Systran/faster-whisper-large-v3 или укажи папку "
                     "через --model. Если загрузка виснет — см. references/okruzhenie.md.")
        from faster_whisper import WhisperModel
        self.m = WhisperModel(self.model_path, device="cpu", compute_type="int8",
                              cpu_threads=8, local_files_only=True)
        self.model = self.model_path

    def run(self, path, prompt, conditioned):
        kw = {} if conditioned else {"temperature": TEMPS}
        segs, _ = self.m.transcribe(path, language="ru", word_timestamps=WORDS,
                                    vad_filter=False, beam_size=5,
                                    condition_on_previous_text=conditioned,
                                    initial_prompt=prompt, **kw)
        return [{"start": s.start, "end": s.end, "text": s.text,
                 "words": [{"word": w.word, "start": w.start, "end": w.end}
                           for w in s.words or []]}
                for s in segs]


def pick_engine(wanted, model):
    """auto: mlx на маках с процессором Apple, если он установлен; иначе faster."""
    apple = sys.platform == "darwin" and platform.machine() == "arm64"
    if wanted in ("auto", "mlx") and (apple or wanted == "mlx"):
        try:
            return MlxEngine(model)
        except ImportError:
            if wanted == "mlx":
                sys.exit("mlx-whisper не установлен: pip install mlx-whisper")
            print("mlx-whisper не установлен — считаю на процессоре, это в двадцать "
                  "раз медленнее. Поставить: pip install mlx-whisper", flush=True)
    return FasterEngine(model)

# Что Whisper выдаёт на русском вместо речи, когда срывается.
JUNK = ["продолжение следует", "субтитры", "спасибо за просмотр",
        "подписывайтесь", "редактор субтитров", "фонд кино"]

TEMPS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
WORDS = False                    # тайм-коды слов; включается ключом --words


def find_glossary(audio):
    """Ищет словарь: сначала рядом с записью, потом в самом скилле.

    Так у каждого проекта может быть свой словарь, и его не нужно каждый
    раз передавать руками — достаточно положить файл к записям.
    """
    here = pathlib.Path(audio).resolve().parent
    skill = pathlib.Path(__file__).resolve().parent.parent
    for folder in (here, skill):
        for name in ("глоссарий.md", "glossary.md"):
            candidate = folder / name
            if candidate.exists():
                return str(candidate)
    return None


def load_glossary(path):
    """Читает словарь: только то, что лежит внутри блоков ```.

    Всё остальное в файле — пояснения для человека, и читать их как
    термины нельзя: иначе в подсказку модели уедут целые предложения,
    и она начнёт слышать их в речи.
    """
    terms, swaps = [], []
    if not path or not os.path.exists(path):
        return terms, swaps
    section, inside = None, False
    for line in open(path, encoding="utf-8"):
        line = line.rstrip()
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if line.startswith("#"):
            low = line.lower()
            section = "terms" if "термин" in low else "swaps" if "замен" in low else None
            inside = False
            continue
        line = line.strip()
        if not line or not inside or section is None:
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
    ap.add_argument("--glossary",
                    help="по умолчанию ищется рядом с записью, потом в скилле")
    ap.add_argument("--model", help="папка с моделью; по умолчанию ищется сама")
    ap.add_argument("--engine", choices=["auto", "mlx", "faster"], default="auto")
    ap.add_argument("--words", action="store_true",
                    help="считать тайм-коды слов — нужны для разметки по голосу")
    args = ap.parse_args()
    global WORDS
    WORDS = args.words

    os.environ.update(ENV)        # mlx сам зовёт ffmpeg, ему нужна та же подстраховка
    engine = pick_engine(args.engine, args.model)
    print(f"движок: {engine.name}, модель: {engine.model}", flush=True)

    glossary = args.glossary or find_glossary(args.audio)
    if glossary:
        print(f"словарь: {glossary}", flush=True)
    terms, swaps = load_glossary(glossary)
    prompt = ("Разговор о приложении. Термины: " + ", ".join(terms) + ".") if terms else None
    if prompt:
        print(f"подсказка модели: {prompt}", flush=True)

    total = duration(args.audio)
    started = time.time()

    print(">>> основной проход", flush=True)
    segs = engine.run(args.audio, prompt, conditioned=True)
    print(f"    сегментов: {len(segs)}, прошло {time.time() - started:.0f} с", flush=True)

    windows = find_broken(segs, args.audio, total)
    print(f">>> чиним участков: {len(windows)}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        piece = os.path.join(tmp, "piece.wav")
        for a, b in windows:
            print(f"    {a:.0f}–{b:.0f} с", flush=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(a),
                            "-t", str(b - a), "-i", args.audio, piece],
                           env=ENV, check=True)
            fixed = shifted(engine.run(piece, prompt, conditioned=False), a)
            segs = [s for s in segs if not (a <= s["start"] < b)] + fixed

    segs.sort(key=lambda s: s["start"])

    # В конце записи модель нередко выдаёт пачку сегментов без единого слова.
    # В диалоге они превращаются в пустые реплики и хвост из пробелов.
    empty = sum(1 for s in segs if not s["text"].strip())
    if empty:
        segs = [s for s in segs if s["text"].strip()]
        print(f"    пустых сегментов отброшено: {empty}", flush=True)

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
    took = time.time() - started
    print(f"готово: {len(segs)} сегментов за {took:.0f} с "
          f"(запись {total:.0f} с, в {total / took:.1f} раза быстрее реального времени) "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
