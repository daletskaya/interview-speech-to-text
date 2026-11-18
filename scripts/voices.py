#!/usr/bin/env python
"""Размечает, кто говорит, по голосу — видео не нужно.

    voices.py ЗВУК.wav РАСПОЗНАННОЕ.json ВЫХОД.csv [--speakers 2]

Берёт фразы из распознавания, для каждой считает высоту голоса и форму
спектра, делит фразы на столько голосов, сколько участников, и пишет ту же
таблицу, что speakers.py: её понимает build.py. Колонки называются tile0,
tile1… — здесь это не плитки, а голоса; имя оставлено ради build.py.

Распознавание нужно с тайм-кодами слов (transcribe.py --words). Границы
сегментов у Whisper грубые: захватывают паузу и кусок чужой реплики, и голос
по ним определяется хуже. На пробной записи в разных прогонах распознавания
верно размечалось 90–96% фраз со словами и 87–90% без них. Картинка на той же
записи давала 81–86%.

Номера голосов идут в порядке первого появления. Кто есть кто, скрипт не
знает: он печатает самые длинные фразы каждого голоса — по ним видно, кто
спрашивает, а кто рассказывает.

Чего скрипт не умеет:
  — различать похожие голоса. Мужской и женский разводит уверенно, два
    женских или два мужских — как повезёт. Поэтому печатается оценка
    «различимость голосов»: если она низкая, разметке верить нельзя;
  — понимать, сколько людей в записи. Число участников надо назвать;
  — делить фразу, где говорили оба сразу: она целиком уйдёт одному.
"""
import argparse
import csv
import glob
import json
import os
import subprocess
import sys

import numpy as np

SR = 16000
FR, HOP = 640, 160              # кадр 40 мс, шаг 10 мс
PER_SEC = SR // HOP
BANDS = 12


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


def load(path):
    """Звук из любого файла — моно, 16 кГц. Видеоряд ffmpeg пропускает."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], env=ffmpeg_env(), capture_output=True)
    if out.returncode or not out.stdout:
        sys.exit("не удалось прочитать звук: " + out.stderr.decode(errors="replace")[:300])
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768


def frame_features(x):
    """Для каждого кадра: громкость, высота голоса (или nan) и спектр по полосам."""
    n = 1 + (len(x) - FR) // HOP
    win = np.hanning(FR).astype(np.float32)
    freqs = np.fft.rfftfreq(FR, 1 / SR)
    edges = np.geomspace(100, 7000, BANDS + 1)
    bands = np.array([(freqs >= lo) & (freqs < hi)
                      for lo, hi in zip(edges[:-1], edges[1:])], dtype=np.float32)
    lo, hi = SR // 350, SR // 70                 # голос бывает от 70 до 350 Гц
    rms = np.empty(n, np.float32)
    f0 = np.full(n, np.nan, np.float32)
    spec = np.empty((n, BANDS), np.float32)
    inside = np.arange(FR)[None, :]
    for s in range(0, n, 20000):                 # кусками, чтобы не съесть память
        e = min(n, s + 20000)
        fr = x[(np.arange(s, e) * HOP)[:, None] + inside]
        rms[s:e] = np.sqrt((fr ** 2).mean(1)) + 1e-9
        power = np.abs(np.fft.rfft(fr * win, axis=1)) ** 2
        spec[s:e] = np.log(power @ bands.T + 1e-12)
        z = fr - fr.mean(1, keepdims=True)
        ac = np.fft.irfft(np.abs(np.fft.rfft(z, 2 * FR, axis=1)) ** 2, axis=1)[:, :hi]
        k = lo + ac[:, lo:hi].argmax(1)
        peak = ac[np.arange(e - s), k]
        voiced = (ac[:, 0] > 0) & (peak > 0.5 * ac[:, 0])
        f0[s:e] = np.where(voiced, SR / k, np.nan)
    return 20 * np.log10(rms), f0, spec


def phrase_features(db, f0, spec, a, b):
    """Признаки голоса во фразе: высота и форма спектра без громкости.

    Берём только громкую половину кадров фразы — это речь, а не паузы
    между словами. Возвращает None, если во фразе слишком мало звука.
    """
    a, b = int(a * PER_SEC), max(int(b * PER_SEC), int(a * PER_SEC) + 15)
    d = db[a:b]
    if len(d) < 15:
        return None
    loud = d >= np.percentile(d, 50)
    pitch = f0[a:b][loud]
    pitch = pitch[~np.isnan(pitch)]
    shape = spec[a:b][loud].mean(0)
    return {"pitch": float(np.median(pitch)) if len(pitch) >= 3 else np.nan,
            "shape": shape - shape.mean(), "frames": int(loud.sum())}


def kmeans(z, k, rng, tries=20):
    best, best_cost = None, None
    for _ in range(tries):
        centers = z[rng.choice(len(z), k, replace=False)]
        for _ in range(60):
            lab = ((z[:, None, :] - centers[None]) ** 2).sum(2).argmin(1)
            new = np.array([z[lab == c].mean(0) if (lab == c).any() else centers[c]
                            for c in range(k)])
            if np.allclose(new, centers):
                break
            centers = new
        cost = ((z - centers[lab]) ** 2).sum()
        if best_cost is None or cost < best_cost:
            best, best_cost = lab, cost
    return best


def fit(z, lab, k):
    """Профиль каждого голоса: среднее и общий для всех разброс."""
    means = np.array([z[lab == c].mean(0) for c in range(k)])
    resid = z - means[lab]
    cov = resid.T @ resid / max(1, len(z) - k) + 0.1 * np.eye(z.shape[1])
    return means, np.linalg.inv(cov)


def scores(z, means, icov):
    d = z[:, None, :] - means[None]
    return -0.5 * np.einsum("nkd,de,nke->nk", d, icov, d)


def phrases(asr, split):
    """Фразы с настоящими границами речи.

    Если есть тайм-коды слов, границы берутся по первому и последнему слову,
    а сегмент режется на паузах длиннее split секунд: за долгую паузу часто
    успевает ответить собеседник, и сегмент оказывается про двоих.
    """
    out, have_words = [], False
    for s in sorted(asr["segments"], key=lambda s: s["start"]):
        if not s["text"].strip():
            continue
        ws = [w for w in s.get("words") or [] if w["end"] > w["start"]]
        if not ws:
            out.append({"a": s["start"], "b": s["end"], "text": s["text"].strip()})
            continue
        have_words = True
        part = [ws[0]]
        for w in ws[1:] + [None]:
            if w is None or w["start"] - part[-1]["end"] > split:
                out.append({"a": part[0]["start"], "b": part[-1]["end"],
                            "text": "".join(p["word"] for p in part).strip()})
                part = []
            if w is not None:
                part.append(w)
    return out, have_words


def clock(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("asr")
    ap.add_argument("out")
    ap.add_argument("--speakers", type=int, default=2, help="сколько человек говорит")
    ap.add_argument("--split", type=float, default=1.0,
                    help="пауза внутри сегмента, на которой он режется на две фразы")
    args = ap.parse_args()
    k = args.speakers

    units, have_words = phrases(json.load(open(args.asr, encoding="utf-8")), args.split)
    if not have_words:
        print("В распознавании нет тайм-кодов слов: голоса определятся хуже.")
        print("Перераспознай с ключом --words: transcribe.py ЗВУК ВЫХОД.json --words\n")

    db, f0, spec = frame_features(load(args.audio))
    for u in units:
        u["f"] = phrase_features(db, f0, spec, u["a"], u["b"])
    usable = [u for u in units if u["f"]]
    fill = float(np.nanmedian([u["f"]["pitch"] for u in usable]))

    def vec(u):
        p = u["f"]["pitch"]
        return np.concatenate([[np.log(p if not np.isnan(p) else fill)], u["f"]["shape"]])

    # Профили учим на фразах подлиннее: на коротких голос неустойчив.
    train = [u for u in usable if u["f"]["frames"] >= 30] or usable
    if len(train) < 10 * k:
        sys.exit("слишком мало речи, чтобы различить голоса")
    raw = np.array([vec(u) for u in train])
    mu, sd = raw.mean(0), raw.std(0) + 1e-9
    z = (raw - mu) / sd
    lab = kmeans(z, k, np.random.default_rng(0))
    for _ in range(5):                            # уточняем границу между голосами
        means, icov = fit(z, lab, k)
        new = scores(z, means, icov).argmax(1)
        if (new == lab).all() or len(set(new)) < k:
            break
        lab = new
    means, icov = fit(z, lab, k)

    for u in usable:
        sc = scores(((vec(u) - mu) / sd)[None], means, icov)[0]
        order = sc.argsort()[::-1]
        u["voice"], u["margin"] = int(order[0]), float(sc[order[0]] - sc[order[1]])

    # Номера голосов — в порядке первого появления.
    first = {}
    for u in usable:
        first.setdefault(u["voice"], len(first))
    for v in range(k):
        first.setdefault(v, len(first))
    for u in usable:
        u["voice"] = first[u["voice"]]
    lab = np.array([first[int(c)] for c in lab])
    means, icov = fit(z, lab, k)

    # Фразы, где голос не посчитался (слишком короткие), — от соседей.
    for i, u in enumerate(units):
        if "voice" not in u:
            near = [units[j] for j in (i - 1, i + 1)
                    if 0 <= j < len(units) and "voice" in units[j]]
            if near:
                u["voice"], u["margin"] = near[0]["voice"], 0.0

    n_bins = int(max(u["b"] for u in units) * 2) + 2
    lit = [set() for _ in range(n_bins)]
    for u in units:
        if "voice" in u:
            for i in range(int(u["a"] * 2), int(u["b"] * 2) + 1):
                lit[i].add(u["voice"])
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["t"] + [f"tile{v}" for v in range(k)])
        for i, s in enumerate(lit):
            wr.writerow([f"{i / 2:.1f}"] + [255 if v in s else 0 for v in range(k)])

    print(f"фраз: {len(units)}")
    print("голоса:")
    for v in range(k):
        mine = [u for u in units if u.get("voice") == v]
        pitch = np.nanmedian([u["f"]["pitch"] for u in mine if u.get("f")])
        print(f"  {v}: высота {pitch:.0f} Гц, фраз {len(mine)}, "
              f"речи {sum(u['b'] - u['a'] for u in mine):.0f} с. Самые длинные:")
        for u in sorted(mine, key=lambda u: u["a"] - u["b"])[:3]:
            print(f"     [{clock(u['a'])}] {u['text'][:90]}")
    print("\nКто есть кто, скрипт не знает. По длинным фразам видно, кто спрашивает,")
    print("а кто рассказывает; если есть видео — вырежи кадр на этих секундах.")
    print('Имена передай build.py: --names "0=Имя,1=Имя"')

    gaps = [float(np.sqrt((means[i] - means[j]) @ icov @ (means[i] - means[j])))
            for i in range(k) for j in range(i + 1, k)]
    print(f"\nразличимость голосов: {min(gaps):.1f}")
    if min(gaps) < 3.0:
        print("  Голоса похожи — разметке по голосу верить нельзя. Размечай по картинке")
        print("  (speakers.py) или по смыслу и скажи об этом в ответе.")

    # Порог подобран по пробной записи: под него попадало около 6% фраз
    # и больше половины ошибок голоса.
    shaky = sorted((u for u in units if "voice" in u and u["margin"] < 10.0
                    and len(u["text"].split()) >= 3), key=lambda u: u["a"])
    print(f"\nфраз под сомнением: {len(shaky)} — при вычитке проверь их в первую очередь")
    for u in shaky[:25]:
        print(f"  [{clock(u['a'])}] голос {u['voice']}: {u['text'][:70]}")
    if len(shaky) > 25:
        print(f"  …и ещё {len(shaky) - 25}")
    print(f"готово: {args.out}")


if __name__ == "__main__":
    main()
