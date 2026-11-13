#!/usr/bin/env python
"""Сдвигает тайм-коды в готовой расшифровке — когда видео обрезали после неё.

    shift.py РАСШИФРОВКА.md СМЕЩЕНИЕ_В_СЕКУНДАХ

Смещение бери из align.py, а не на глаз: разница в секунду незаметна в плеере,
но из неё вырастает недоверие ко всему файлу. Положительное число — у видео
отрезали начало.

Правит файл на месте. Тайм-коды, уехавшие в минус, становятся 00:00.
"""
import re
import sys


def main():
    path, offset = sys.argv[1], float(sys.argv[2])
    text = open(path, encoding="utf-8").read()

    def fix(m):
        t = int(m.group(1)) * 60 + int(m.group(2)) - offset
        t = max(0.0, t)
        return f"[{int(t // 60):02d}:{int(t % 60):02d}]"

    out, n = re.subn(r"\[(\d{2}):(\d{2})\]", fix, text)
    open(path, "w", encoding="utf-8").write(out)
    print(f"пересчитано тайм-кодов: {n}")
    print("Проверь: открой видео на любом тайм-коде из середины и сверь,")
    print("что звучит там то же, что написано.")


if __name__ == "__main__":
    main()
