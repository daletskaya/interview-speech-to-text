#!/usr/bin/env python
"""Размечает, кто говорит, по подсветке активного участника в кадре.

Почти все платформы видеовстреч обводят или подсвечивают плитку того, кто
сейчас говорит. Цвет и толщина у всех разные, поэтому скрипт ничего не знает
про платформы: он сам находит подсветку в конкретной записи.

    speakers.py calibrate ЗАПИСЬ
        Ищет цвета, которые в этой записи ведут себя как подсветка: образуют
        длинные вертикальные линии и то появляются, то пропадают. Печатает
        кандидатов и секунды, где каждый виден, — проверь их скриншотом,
        прежде чем идти дальше.

    speakers.py detect ЗАПИСЬ ВЫХОД.csv --color 22F666
        Строит таймлайн: для каждого полукадра — какие плитки подсвечены.
        Плитки находит сам, по парам вертикальных линий, поэтому участников
        может быть сколько угодно: двое, интервьюер с двумя коллегами,
        фокус-группа.

Плиток может оказаться больше, чем людей: при смене раскладки (включили
демонстрацию экрана) плитки уезжают на новые места. Сопоставить позиции
с именами — дело вызывающего, detect печатает их список.
"""
import argparse
import glob
import os
import subprocess
import sys

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


def frame_size(path, width):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        env=ENV, capture_output=True, text=True).stdout.strip()
    w, h = (int(x) for x in out.split(",")[:2])
    nh = int(round(width * h / w / 2)) * 2
    return width, nh


def frames(path, fps, width):
    """Отдаёт кадры как массивы, не записывая их на диск."""
    w, h = frame_size(path, width)
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        env=ENV, stdout=subprocess.PIPE)
    size = w * h * 3
    n = 0
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield n, np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
        n += 1
    p.stdout.close()
    p.wait()


def longest_runs(codes):
    """Самая длинная одноцветная вертикальная полоса в каждом столбце.

    Возвращает (длина, цвет полосы). Цвет берётся именно у полосы, а не самый
    частый в столбце: в узкой полоске участников столбец почти весь чёрный,
    и частый цвет — это фон, а не подсветка.
    """
    eq = codes[1:] == codes[:-1]
    best = np.zeros(codes.shape[1], dtype=np.int32)
    best_code = codes[0].copy()
    run = np.zeros(codes.shape[1], dtype=np.int32)
    for i in range(eq.shape[0]):
        run = np.where(eq[i], run + 1, 0)
        upd = run > best
        best = np.where(upd, run, best)
        best_code = np.where(upd, codes[i + 1], best_code)
    return best + 1, best_code


def hollow_box(mask, min_side):
    """Похожи ли пиксели маски на пустую рамку, а не на заливку.

    Подсветка участника — это контур плитки: пиксели лежат по краям
    прямоугольника, внутри пусто. Картинка на демонстрируемом экране —
    баннеры, кнопки, текст — заливает площадь целиком. Это и есть главное
    различие между рамкой и ярким пятном в содержимом экрана.
    """
    ys, xs = np.nonzero(mask)
    if len(ys) < 50:
        return False
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    if (y1 - y0) < min_side or (x1 - x0) < min_side:
        return False
    pad = 5
    iy0, iy1 = y0 + pad, y1 - pad
    ix0, ix1 = x0 + pad, x1 - pad
    if iy1 <= iy0 or ix1 <= ix0:
        return False
    inside = mask[iy0:iy1, ix0:ix1].sum()
    return inside / mask.sum() < 0.25


def calibrate(path, args):
    w, h = frame_size(path, args.width)
    need = int(h * 0.08)                     # плитка в узкой полоске тоже считается
    seen, shots, total = {}, [], 0
    for n, img in frames(path, args.fps_calibrate, args.width):
        total += 1
        if len(shots) < args.max_frames:
            shots.append(img.copy())
        q = img >> 3                            # грубее цвет — устойчивее к сжатию
        codes = (q[:, :, 0].astype(np.int32) << 16 |
                 q[:, :, 1].astype(np.int32) << 8 | q[:, :, 2])
        best, best_code = longest_runs(codes)
        for x in np.nonzero(best >= need)[0]:
            code = int(best_code[x])
            r, g, b = ((code >> 16) & 255) << 3, ((code >> 8) & 255) << 3, (code & 255) << 3
            if max(r, g, b) - min(r, g, b) < 60:   # серое, чёрное, белое — не подсветка
                continue
            seen.setdefault((r + 4, g + 4, b + 4), set()).add(n)

    print(f"кадров просмотрено: {total}")
    if not seen:
        print("\nПодсветки не нашлось. Возможно, платформа её не рисует —")
        print("тогда говорящих придётся размечать по смыслу реплик.")
        return

    # Второй проход: у кого из кандидатов пиксели складываются в пустую рамку.
    top = sorted(seen.items(), key=lambda kv: -len(kv[1]))[:args.max_colors]
    print(f"цветов на проверку: {len(top)}\n")
    rows = []
    for color, where in top:
        target = np.array(color, dtype=np.int16)
        boxes = []
        for i, img in enumerate(shots):
            if i not in where:
                continue
            mask = np.abs(img.astype(np.int16) - target).max(axis=2) <= args.tol
            if hollow_box(mask, need):
                boxes.append(i)
        if boxes:
            rows.append((len(boxes), len(where), color, boxes))

    if not rows:
        print("Ни один цвет не складывается в рамку. Либо подсветки нет, либо")
        print("она не контур, а заливка плитки — посмотри кадры глазами.")
        return

    rows.sort(key=lambda r: -r[0])
    print("кандидаты (сверху самые похожие на подсветку):")
    for nbox, nseen, (r, g, b), boxes in rows[:6]:
        ex = ", ".join(f"{i / args.fps_calibrate:.0f}" for i in boxes[:4])
        print(f"  #{r:02X}{g:02X}{b:02X}  rgb({r},{g},{b})"
              f"  рамкой в {nbox} кадрах из {nseen}  секунды: {ex}")
    print("\nПроверь верхнего кандидата скриншотом: на этих секундах подсветка")
    print("должна быть у одного участника, а не у обоих и не у всех подряд.")


def runs_of(cols, gap=4):
    """Сливает соседние столбцы в отрезки: две линии рамки стоят в пикселе
    друг от друга, и считать их по отдельности бессмысленно."""
    if len(cols) == 0:
        return []
    out, cur = [], [cols[0]]
    for x in cols[1:]:
        if x - cur[-1] > gap:
            out.append((cur[0], cur[-1]))
            cur = [x]
        else:
            cur.append(x)
    out.append((cur[0], cur[-1]))
    return out


def tiles_in_frame(col, min_px, min_width):
    """Прямоугольники подсвеченных плиток в одном кадре.

    Подсвеченная плитка даёт ровно две вертикальные линии — левый и правый
    край. Значит, линии идут парами, и каждая пара и есть плитка. Так
    получается столько плиток, сколько человек говорит, без всяких
    предположений о том, что их двое.
    """
    lines = runs_of(np.nonzero(col >= min_px)[0])
    out = []
    for (a, _), (_, b) in zip(lines[0::2], lines[1::2]):
        if b - a >= min_width:
            out.append((int(a), int(b)))
    return out


def detect(path, out, args):
    target = np.array([int(args.color[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)
    rows = []
    for n, img in frames(path, args.fps, args.width):
        d = np.abs(img.astype(np.int16) - target).max(axis=2)
        col = (d <= args.tol).sum(axis=0)
        rows.append((n / args.fps, col, tiles_in_frame(col, args.min_px, args.min_width)))

    if not rows:
        sys.exit("не удалось прочитать кадры")
    if not any(t for _, _, t in rows):
        sys.exit("подсветки такого цвета не нашлось — перепроверь calibrate")

    # Одна и та же плитка от кадра к кадру стоит на том же месте, но при смене
    # раскладки (включили демонстрацию экрана) уезжает. Поэтому кластеров может
    # быть больше, чем людей: один человек — несколько позиций.
    seen = {}
    for _, _, tiles in rows:
        for a, b in tiles:
            key = next((k for k in seen if abs(k[0] - a) <= 12 and abs(k[1] - b) <= 12), None)
            seen[key or (a, b)] = seen.get(key or (a, b), 0) + 1
    places = sorted(seen.items(), key=lambda kv: kv[0][0])

    print("найденные плитки:")
    for i, ((a, b), n) in enumerate(places):
        print(f"  {i}: x {a}-{b}  подсвечена {n / args.fps:.0f} с")
    print("\nЕсли плиток больше, чем участников, значит, раскладка менялась:")
    print("один человек даёт несколько позиций. Сопоставь их с именами по кадру")
    print("и передай build.py, например: --names \"0=Интервьюер,2=Интервьюер,1=Гость\".")

    with open(out, "w", encoding="utf-8") as f:
        f.write("t," + ",".join(f"tile{i}" for i in range(len(places))) + "\n")
        for t, col, tiles in rows:
            cells = []
            for (a, b), _ in places:
                hit = any(abs(a - ta) <= 12 and abs(b - tb) <= 12 for ta, tb in tiles)
                cells.append(str(int(col[a:b + 1].max()) if hit else 0))
            f.write(f"{t}," + ",".join(cells) + "\n")

    quiet = sum(1 for _, _, t in rows if not t)
    print(f"\nполукадров без подсветки: {quiet} из {len(rows)} "
          f"({quiet * 100 // len(rows)}%) — это паузы")
    overlap = sum(1 for _, _, t in rows if len(t) > 1)
    if overlap:
        print(f"подсвечено несколько сразу: {overlap} "
              f"({overlap * 100 // len(rows)}%) — там разметка ненадёжна")
    print(f"готово: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["calibrate", "detect"])
    ap.add_argument("video")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--color", help="цвет подсветки, RRGGBB")
    ap.add_argument("--tol", type=int, default=40, help="допуск по цвету")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--fps-calibrate", type=float, default=0.05)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--max-frames", type=int, default=120,
                    help="сколько кадров держать в памяти при калибровке")
    ap.add_argument("--max-colors", type=int, default=15,
                    help="сколько цветов проверять на рамку")
    ap.add_argument("--min-px", type=int, default=20,
                    help="сколько пикселей в столбце считать линией")
    ap.add_argument("--min-width", type=int, default=40,
                    help="минимальная ширина плитки в пикселях")
    args = ap.parse_args()

    if args.mode == "calibrate":
        calibrate(args.video, args)
    else:
        if not args.out or not args.color:
            sys.exit("нужны ВЫХОД.csv и --color")
        detect(args.video, args.out, args)


if __name__ == "__main__":
    main()
