#!/usr/bin/env python
"""Проверяет, что каждая цитата в ответах по вопросам есть в расшифровке.

    quotes.py ОТВЕТЫ.md РАСШИФРОВКА.md [--speaker Имя]

Цитата — строка вида

    - [17:40] «Отзывы смотрю, если первый раз там заказываю»

Для каждой цитаты скрипт ищет реплику, в которую попадает тайм-код,
и проверяет, что слова цитаты стоят в ней подряд и в том же порядке.
Пропуск внутри цитаты обозначается многоточием: «Сначала… а потом» —
тогда куски ищутся по очереди в той же реплике.

Сравниваются только слова: регистр, ё/е и знаки препинания не важны.
Подправленное слово, склейка из двух реплик или фраза из другого места
разговора — ошибка: цитата должна звучать ровно там, куда ведёт тайм-код.

--speaker — чьи слова цитируются. Цитата из чужой реплики, например
вопрос интервьюера, попадёт в список предупреждений.
"""
import argparse
import re
import sys

REPLY = re.compile(r"^\*\*\[(\d\d):(\d\d)\] ([^:*]+):\*\*\s*(.*)$")
QUOTE = re.compile(r"\[(\d\d):(\d\d)\][^«\n]*«(.+?)»")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
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
    for r in replies:
        r["words"] = words(r["text"])

    def reply_at(t):
        cur = None
        for r in replies:
            if r["t"] <= t:
                cur = r
            else:
                break
        return cur

    total, bad, foreign = 0, [], []
    for n, line in enumerate(open(args.answers, encoding="utf-8"), 1):
        for m in QUOTE.finditer(line):
            total += 1
            t = int(m[1]) * 60 + int(m[2])
            text = m[3]
            pieces = [words(p) for p in re.split(r"…|\.\.\.", text)]
            if not any(pieces):
                continue
            here = reply_at(t)
            if here and contains(here["words"], pieces):
                if args.speaker and here["who"] != args.speaker:
                    foreign.append((n, t, here["who"], text))
                continue
            elsewhere = [r for r in replies if contains(r["words"], pieces)]
            if elsewhere:
                at = ", ".join(f"[{clock(r['t'])}] {r['who']}" for r in elsewhere[:3])
                bad.append((n, t, text, f"тайм-код не тот, эти слова в реплике {at}"))
            else:
                bad.append((n, t, text, "таких слов подряд в расшифровке нет — "
                                        "цитата пересказана или склеена"))

    print(f"цитат: {total}")
    if not total:
        print("Не нашлось ни одной строки вида  - [ММ:СС] «цитата».")
    for n, t, text, why in bad:
        print(f"\n  строка {n}, [{clock(t)}] «{text[:80]}»\n    {why}")
    for n, t, who, text in foreign:
        print(f"\n  строка {n}, [{clock(t)}] это слова {who}, а не {args.speaker}: «{text[:80]}»")
    if bad or foreign:
        print(f"\nисправь: ошибок {len(bad)}, чужих реплик {len(foreign)}")
        sys.exit(1)
    print("все цитаты на месте")


if __name__ == "__main__":
    main()
