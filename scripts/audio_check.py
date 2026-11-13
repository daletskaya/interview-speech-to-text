#!/usr/bin/env python
"""Проверяет, можно ли разделить говорящих по звуку, не трогая картинку.

    audio_check.py ЗАПИСЬ

Смотрит три вещи:
  1. сколько в файле аудиопотоков — несколько значит дорожку на участника;
  2. различаются ли каналы стерео — если да, говорящие могут быть разведены
     по панораме;
  3. если каналы одинаковые, разделить по звуку нельзя.

Разные дорожки — самый надёжный путь, он не зависит от платформы.
Если их нет, переходи к разметке по картинке (speakers.py).
"""
import glob
import json
import os
import subprocess
import sys

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


def run(cmd):
    return subprocess.run(cmd, env=ENV, capture_output=True, text=True).stdout


def mean_volume(path, expr, seconds=300):
    out = subprocess.run(
        ["ffmpeg", "-t", str(seconds), "-i", path, "-af",
         f"pan=mono|c0={expr},volumedetect", "-vn", "-f", "null", "-"],
        env=ENV, capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].replace("dB", "").strip())
    return None


def main():
    path = sys.argv[1]
    info = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "a",
                           "-show_streams", "-of", "json", path]))
    streams = info.get("streams", [])
    print(f"аудиопотоков: {len(streams)}")
    for s in streams:
        print(f"  #{s['index']}: {s['codec_name']}, каналов {s['channels']},"
              f" раскладка {s.get('channel_layout', '?')}")

    if len(streams) > 1:
        print("\nЕсть несколько дорожек — скорее всего, по одной на участника.")
        print("Распознавай каждую отдельно, разметка по картинке не нужна.")
        return

    if not streams or streams[0]["channels"] < 2:
        print("\nОдна моно-дорожка. По звуку не разделить, иди в speakers.py.")
        return

    left = mean_volume(path, "c0")
    right = mean_volume(path, "c1")
    diff = mean_volume(path, "0.5*c0-0.5*c1")
    print(f"\nлевый канал:  {left} dB")
    print(f"правый канал: {right} dB")
    print(f"разница L-R:  {diff} dB")

    if diff is not None and diff < -60:
        print("\nКаналы одинаковые: это моно, разложенное в стерео.")
        print("По звуку не разделить, иди в speakers.py.")
    else:
        print("\nКаналы различаются — возможно, говорящие разведены по панораме.")
        print("Послушай каналы отдельно, прежде чем на это закладываться.")


if __name__ == "__main__":
    main()
