#!/usr/bin/env python
"""Собирает черновик диалога из распознанного текста и разметки говорящих.

    build.py РАСПОЗНАННОЕ.json ВЫХОД.md --speakers РАЗМЕТКА.csv \
             --names "0=Интервьюер,1=Гость"

Имена привязываются к номерам плиток, которые напечатал speakers.py.
Участников может быть сколько угодно. Если раскладка в записи менялась,
один человек даёт несколько плиток — перечисли их через запятую с одним
именем: `--names "0=Интервьюер,2=Интервьюер,1=Гость"`.

Без --speakers соберёт сплошной текст с тайм-кодами и пометкой «?» вместо
имени — так бывает, когда подсветки в записи нет.

Говорящий приписывается ЦЕЛОМУ сегменту, а не отдельным словам. Пословная
разметка выглядит точнее, но на деле рассыпает реплики в пинг-понг из
обрывков: тайм-коды слов у Whisper гуляют на доли секунды, а подсветка
гаснет с задержкой. Сегмент — это фраза целиком, и по ней голосование
устойчивое.

Цена такого решения: короткое «угу» собеседника, попавшее внутрь чужой
фразы, достанется автору фразы. Это чинится только руками, при вычитке.

Выход — ЧЕРНОВИК. Его обязательно вычитывать: см. SKILL.md, шаг «Редактура».
"""
import argparse
import csv
import json
import sys
from collections import defaultdict


def parse_names(spec, columns):
    names = {}
    for piece in spec.split(","):
        if "=" not in piece:
            sys.exit(f"не понимаю «{piece}», нужно вида 0=Имя")
        idx, name = piece.split("=", 1)
        col = f"tile{idx.strip()}"
        if col not in columns:
            have = ", ".join(c[4:] for c in columns if c.startswith("tile"))
            sys.exit(f"в разметке нет плитки {idx.strip()}; есть: {have}")
        names[col] = name.strip()
    return names


def load_speakers(path, names):
    """Возвращает {полукадр: множество имён, чья подсветка горит}."""
    bins = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lit = {names[col] for col, value in r.items()
                   if col in names and int(value) >= 10}
            bins[round(float(r["t"]) * 2)] = lit
    return bins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asr")
    ap.add_argument("out")
    ap.add_argument("--speakers")
    ap.add_argument("--names", help='например "0=Интервьюер,1=Гость"')
    ap.add_argument("--gap", type=float, default=25.0,
                    help="пауза, после которой начинается новая реплика")
    args = ap.parse_args()

    segs = sorted(json.load(open(args.asr, encoding="utf-8"))["segments"],
                  key=lambda s: s["start"])

    bins = None
    if args.speakers:
        if not args.names:
            sys.exit("с --speakers нужен --names, иначе непонятно, кто есть кто")
        with open(args.speakers, encoding="utf-8") as f:
            columns = next(csv.reader(f))
        bins = load_speakers(args.speakers, parse_names(args.names, columns))

    def who(s):
        """Кто говорил в сегменте: побеждает тот, кто дольше горел один."""
        if bins is None:
            return "?"
        score = defaultdict(int)
        for k in range(int(s["start"] * 2), int(s["end"] * 2) + 1):
            lit = bins.get(k, set())
            if len(lit) == 1:              # пока горят несколько, кадр не в счёт
                score[next(iter(lit))] += 1
        if not score:
            return None
        best = max(score.values())
        winners = [n for n, v in score.items() if v == best]
        return winners[0] if len(winners) == 1 else None

    for s in segs:
        s["sp"] = who(s)
    for i, s in enumerate(segs):             # ничья и тишина — от соседей
        if s["sp"] is None:
            back = next((segs[j]["sp"] for j in range(i - 1, -1, -1) if segs[j]["sp"]), None)
            fwd = next((segs[j]["sp"] for j in range(i + 1, len(segs)) if segs[j]["sp"]), None)
            s["sp"] = back or fwd or "?"

    turns = []
    for s in segs:
        if turns and turns[-1]["sp"] == s["sp"] and s["start"] - turns[-1]["end"] < args.gap:
            turns[-1]["text"] += " " + s["text"].strip()
            turns[-1]["end"] = s["end"]
        else:
            turns.append({"sp": s["sp"], "start": s["start"], "end": s["end"],
                          "text": s["text"].strip()})

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# Черновик расшифровки\n\nСобран автоматически, не вычитан.\n\n---\n\n")
        for t in turns:
            m, sec = divmod(int(t["start"]), 60)
            f.write(f"**[{m:02d}:{sec:02d}] {t['sp']}:** {t['text']}\n\n")

    print(f"реплик: {len(turns)}, сегментов: {len(segs)}")
    if bins:
        said = defaultdict(int)
        for lit in bins.values():
            if len(lit) == 1:
                said[next(iter(lit))] += 1
        for name, n in sorted(said.items(), key=lambda kv: -kv[1]):
            print(f"  {name}: {n / 2:.0f} с речи")
        both = sum(1 for lit in bins.values() if len(lit) > 1)
        print(f"  говорили одновременно: {both / 2:.0f} с — в этих местах "
              f"разметка ненадёжна")
    print(f"готово: {args.out}")


if __name__ == "__main__":
    main()
