#!/usr/bin/env python
"""Проверяет, что каждая цитата в саммари есть в расшифровке.

    quotes.py САММАРИ.md РАСШИФРОВКА.md [--speaker Имя]

Пункт саммари — строка списка (и её продолжение на следующих строках)
с пересказом, цитатами в «ёлочках» и тайм-кодом или диапазоном:

    - Заказ к определённому времени: сначала говорит «не вижу, как это
      сделать», потом находит значок часов. [22:07–22:46]

Для каждой цитаты скрипт ищет реплику внутри диапазона пункта и проверяет,
что слова цитаты стоят в ней подряд и в том же порядке. Пропуск внутри
цитаты обозначается многоточием: «Сначала… а потом» — тогда куски ищутся
по очереди в той же реплике.

Сравниваются только слова: регистр, ё/е и знаки препинания не важны.
Подправленное слово, склейка из двух реплик или фраза из другого места
разговора — ошибка: цитата должна звучать там, куда ведёт тайм-код.

Цитата без тайм-кода в пункте — тоже ошибка: её нельзя проверить
по записи. Исключение — строки «Не обсуждали: …» и «Ответа по существу
нет: …»: в них в кавычках стоит вопрос гайда, а не слова респондента,
и раздел «Выводы ведущего»: там слова ведущего из его ответов скиллу,
а их в записи нет.

--speaker — чьи слова цитируются. Цитата из чужой реплики, например
вопрос интервьюера, попадёт в список предупреждений.
"""
import argparse
import re
import sys

REPLY = re.compile(r"^\*\*\[(\d\d):(\d\d)\] ([^:*]+):\*\*\s*(.*)$")
BRACKET = re.compile(r"\[([^\]]*\d\d:\d\d[^\]]*)\]")
TIME = re.compile(r"(\d\d):(\d\d)(?:\s*[–—-]\s*(\d\d):(\d\d))?")
QUOTE = re.compile(r"«(.+?)»", re.S)
NO_ANSWER = re.compile(r"^\s*[-*]?\s*(не обсуждали|ответа по существу нет)", re.I)
SLACK = 3   # секунд: тайм-код реплики округлён вниз


def words(s):
    s = s.lower().replace("ё", "е").replace("(нрзбр)", " нрзбр ")
    return re.findall(r"\w+", s)


def contains(hay, pieces):
    """Есть ли куски в hay подряд, по очереди, без перестановок."""
    pos = 0
    for p in pieces:
        if not p:
            continue
        for i in range(pos, len(hay) - len(p) + 1):
            if hay[i:i + len(p)] == p:
                pos = i + len(p)
                break
        else:
            return False
    return True


def clock(t):
    return f"{t // 60:02d}:{t % 60:02d}"


def items(path):
    """Пункты саммари: (номер первой строки, текст) — строка списка вместе
    с продолжением или отдельный абзац. Раздел «Выводы ведущего» пропускается."""
    out, cur, start, skip = [], [], 0, False
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        s = line.rstrip("\n")
        if s.startswith("#"):
            skip = "выводы ведущего" in s.lower()
        if skip:
            if cur:
                out.append((start, " ".join(cur)))
                cur = []
            continue
        # Продолжение пункта — только строка с отступом. Строка с начала
        # («Не обсуждали: …», новый абзац) — уже отдельный пункт.
        new_item = re.match(r"^\s{0,3}[-*] ", s) or not s.strip() or s.startswith("#") \
            or s.startswith("```") or not s[:1].isspace()
        if new_item and cur:
            out.append((start, " ".join(cur)))
            cur = []
        if s.strip() and not s.startswith("#") and not s.startswith("```"):
            if not cur:
                start = n
            cur.append(s.strip())
    if cur:
        out.append((start, " ".join(cur)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("summary")
    ap.add_argument("transcript")
    ap.add_argument("--speaker", help="чьи слова должны быть в цитатах")
    args = ap.parse_args()

    replies = []
    for line in open(args.transcript, encoding="utf-8"):
        m = REPLY.match(line.strip())
        if m:
            replies.append({"t": int(m[1]) * 60 + int(m[2]), "who": m[3].strip(),
                            "text": m[4]})
        elif replies and line.strip() and not line.startswith("#"):
            replies[-1]["text"] += " " + line.strip()
    if not replies:
        sys.exit("в расшифровке не нашлось реплик вида **[ММ:СС] Имя:**")
    for i, r in enumerate(replies):
        r["words"] = words(r["text"])
        r["end"] = replies[i + 1]["t"] if i + 1 < len(replies) else 10 ** 6

    def in_ranges(ranges):
        """Реплики, которые пересекаются хотя бы с одним диапазоном."""
        return [r for r in replies
                if any(r["t"] <= b + SLACK and r["end"] > a - SLACK for a, b in ranges)]

    total, bad, foreign, untimed = 0, [], [], []
    for n, text in items(args.summary):
        quotes = QUOTE.findall(text)
        if not quotes:
            continue
        if NO_ANSWER.match(text):
            continue
        ranges = []
        for br in BRACKET.finditer(text):        # [02:11–03:23; 04:13–05:19]
            for m in TIME.finditer(br[1]):
                a = int(m[1]) * 60 + int(m[2])
                b = int(m[3]) * 60 + int(m[4]) if m[3] else a
                ranges.append((min(a, b), max(a, b)))
        for q in quotes:
            pieces = [words(p) for p in re.split(r"…|\.\.\.", q)]
            if not any(pieces):
                continue
            total += 1
            if not ranges:
                untimed.append((n, q))
                continue
            here = [r for r in in_ranges(ranges) if contains(r["words"], pieces)]
            if here:
                if args.speaker and all(r["who"] != args.speaker for r in here):
                    foreign.append((n, here[0]["t"], here[0]["who"], q))
                continue
            elsewhere = [r for r in replies if contains(r["words"], pieces)]
            span = ", ".join(f"{clock(a)}–{clock(b)}" for a, b in ranges)
            if elsewhere:
                at = ", ".join(f"[{clock(r['t'])}] {r['who']}" for r in elsewhere[:3])
                bad.append((n, q, f"в диапазоне {span} этих слов нет, они в реплике {at}"))
            else:
                bad.append((n, q, "таких слов подряд в расшифровке нет — "
                                  "цитата пересказана или склеена"))

    print(f"цитат: {total}")
    for n, q, why in bad:
        print(f"\n  строка {n}: «{q[:80]}»\n    {why}")
    for n, q in untimed:
        print(f"\n  строка {n}: «{q[:80]}»\n    у пункта нет тайм-кода — цитату не проверить по записи")
    for n, t, who, q in foreign:
        print(f"\n  строка {n}, [{clock(t)}]: это слова {who}, а не {args.speaker}: «{q[:80]}»")
    if bad or foreign or untimed:
        print(f"\nисправь: ошибок {len(bad)}, без тайм-кода {len(untimed)}, "
              f"чужих реплик {len(foreign)}")
        sys.exit(1)
    print("все цитаты на месте")


if __name__ == "__main__":
    main()
