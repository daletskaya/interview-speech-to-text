#!/usr/bin/env python
"""Собирает список неразборчивых мест — чтобы человек мог их вспомнить.

    unclear.py РАСШИФРОВКА.md РАСПОЗНАННОЕ.json [--offset СЕКУНДЫ]

Для каждого `(нрзбр)` печатает тайм-код, фразу вокруг и — главное — что
на этом месте услышал распознаватель.

Искажённое звучание и есть подсказка: «кура-мису» ничего не значит
в расшифровке, но человек, который там был, по нему вспоминает название блюда.
Без этой колонки список бесполезен: сказать «тут неразборчиво» можно и так,
а вспомнить помогает именно то, как оно прозвучало.

--offset нужен, если тайм-коды в расшифровке уже сдвигали под обрезанное
видео, а asr.json остался от исходного (столько же, сколько отдали shift.py).
"""
import argparse
import json
import re

MARK = "(нрзбр)"


def words_with_time(path):
    out = []
    for s in sorted(json.load(open(path, encoding="utf-8"))["segments"],
                    key=lambda x: x["start"]):
        for w in s.get("words") or []:
            out.append((w["word"].strip(), w["start"]))
        if not (s.get("words") or []):
            for piece in s["text"].split():
                out.append((piece, s["start"]))
    return out


def norm(s):
    return re.sub(r"[^\w]+", "", s.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("asr")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--context", type=int, default=6, help="слов для привязки")
    args = ap.parse_args()

    raw = words_with_time(args.asr)
    raw_norm = [norm(w) for w, _ in raw]

    text = open(args.transcript, encoding="utf-8").read()
    plain = re.sub(r"\*\*|\[\d\d:\d\d\]", " ", text)

    found = 0
    for m in re.finditer(re.escape(MARK), plain):
        found += 1
        before = plain[:m.start()].split()[-args.context:]
        after = plain[m.end():].split()[:args.context]
        keys = [norm(w) for w in before if norm(w)]

        best, score_best = None, 0
        for i in range(len(raw_norm)):            # куда в сыром тексте это попадает
            score = sum(1 for k in keys if k in raw_norm[max(0, i - len(keys)):i])
            if score > score_best:
                best, score_best = i, score

        if best is None or score_best < max(2, len(keys) // 2):
            print(f"\n{found}. не удалось привязать к записи")
            print(f"   …{' '.join(before)} [{MARK}] {' '.join(after)}…")
            continue

        t = raw[best][1] - args.offset
        heard = " ".join(w for w, _ in raw[best:best + 6])
        print(f"\n{found}. [{int(t // 60):02d}:{int(t % 60):02d}]")
        print(f"   в расшифровке: …{' '.join(before)} [{MARK}] {' '.join(after)}…")
        print(f"   расслышано как: {heard}")

    print(f"\nвсего мест: {found}")
    if found:
        print("Отправь этот список человеку, который вёл интервью: по искажённому")
        print("звучанию он обычно вспоминает, что там было на самом деле.")


if __name__ == "__main__":
    main()
