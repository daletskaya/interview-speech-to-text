#!/usr/bin/env python
"""Измеряет, на сколько обрезанное видео смещено относительно исходного звука.

    align.py ОБРЕЗАННОЕ_ВИДЕО ИСХОДНЫЙ_audio16k.wav

Печатает смещение в секундах и во сколько раз пик корреляции выше фона.
Пик меньше 5 — измерению верить нельзя (не тот файл или звук переделан).

Нужно, когда видео обрезали уже после расшифровки и тайм-коды разъехались.
Дальше: shift.py с этим смещением.
"""
import glob
import os
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


def read_wav(path):
    with wave.open(path) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return a.astype(np.float32) / 32768.0


def main():
    trimmed, original = sys.argv[1], sys.argv[2]
    for p in (trimmed, original):
        if not os.path.exists(p):
            sys.exit(f"нет файла: {p}\n"
                     "Нужен исходный audio16k.wav — тот, по которому делалась "
                     "расшифровка. Его кладёт рядом с видео prepare.sh; если он "
                     "потерялся, достать смещение уже неоткуда, и тайм-коды "
                     "придётся сверять руками по нескольким репликам.")
    head_seconds = 25
    search_seconds = 300          # ищем смещение в первых пяти минутах оригинала

    with tempfile.TemporaryDirectory() as tmp:
        head = os.path.join(tmp, "head.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", str(head_seconds),
                        "-i", trimmed, "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", head], env=ENV, check=True)
        new = read_wav(head)

    old = read_wav(original)[:search_seconds * 16000]
    new = new - new.mean()
    old = old - old.mean()

    c = np.correlate(old, new, mode="valid")
    k = int(np.argmax(c))
    around = slice(max(0, k - 8000), k + 8000)
    background = np.abs(np.delete(c, around)).max()
    ratio = c[k] / background if background else float("inf")

    print(f"смещение: {k / 16000:.3f} с")
    print(f"пик выше фона в {ratio:.1f} раза")
    if ratio < 5:
        print("ВНИМАНИЕ: пик слабый, измерению верить нельзя")


if __name__ == "__main__":
    main()
