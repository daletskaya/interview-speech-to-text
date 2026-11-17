#!/bin/zsh
# Готовит записи к расшифровке: склеивает куски и извлекает аудио.
#
#   prepare.sh [--mp4] ВЫХОДНАЯ_ПАПКА файл1 файл2 ...
#
# По умолчанию видео не перекодируется: для расшифровки это не нужно,
# а стоит шестую часть длительности записи. Один файл остаётся как есть,
# несколько склеиваются без пережатия в «полная.<расширение исходника>».
#
# --mp4 — перекодировать в mp4. Нужен, только если человек просил именно
# mp4, например чтобы запись открывалась где угодно.
#
# Файлы перечисляй в хронологическом порядке. У многих платформ время начала
# стоит прямо в имени, так что сортировка по имени обычно и есть хронология —
# но проверь по длительностям, что конец одной записи стыкуется с началом
# следующей.
#
# На выходе: «audio16k.wav» и путь к записи, с которой работать дальше.

set -e

# На части маков хомбрю оставляет ffmpeg со ссылкой на старую libtiff.
# Если такая версия ещё лежит в Cellar, подставляем её. Версий может быть
# несколько, и нужная — не обязательно последняя, поэтому берём все.
# (N) — чтобы zsh не падал, когда папки нет вовсе.
OLD_LIBS=(/usr/local/Cellar/libtiff/*/lib(N) /opt/homebrew/Cellar/libtiff/*/lib(N))
if [ ${#OLD_LIBS} -gt 0 ]; then
  export DYLD_FALLBACK_LIBRARY_PATH="${(j.:.)OLD_LIBS}:/usr/local/lib:/opt/homebrew/lib:/usr/lib"
fi

MP4=0
[ "$1" = "--mp4" ] && { MP4=1; shift; }

OUT="$1"
[ -z "$OUT" ] && { echo "нужна выходная папка"; exit 1; }
shift
[ $# -eq 0 ] && { echo "нужен хотя бы один файл"; exit 1; }
mkdir -p "$OUT"

length() { ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$1"; }

reencode() {
  # Аппаратный кодировщик есть только на маках; без него берём обычный.
  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_videotoolbox; then
    VIDEO=(-c:v h264_videotoolbox -b:v 5000k -maxrate 6500k -bufsize 12000k)
  else
    VIDEO=(-c:v libx264 -preset veryfast -crf 23)
  fi
  # Ключ постоянной частоты кадров в новых сборках называется иначе.
  if ffmpeg -hide_banner -h full 2>/dev/null | grep -q -- "-fps_mode"; then
    FPS=(-fps_mode cfr)
  else
    FPS=(-vsync cfr)
  fi

  LIST="$OUT/.concat.txt"; : > "$LIST"
  PARTS=()
  for src in "$@"; do
    base="${src:t:r}"
    dst="$OUT/$base.mp4"
    # Запись уже в mp4 и лежит в выходной папке — не затираем исходник.
    [ "${src:A}" = "${dst:A}" ] && dst="$OUT/$base.conv.mp4"
    echo ">>> перекодирую: $base"
    ffmpeg -y -v warning -stats -i "$src" \
      -r 30 $FPS $VIDEO -pix_fmt yuv420p \
      -c:a aac -b:a 128k -ar 48000 -ac 2 -movflags +faststart "$dst"
    printf "file '%s'\n" "${dst:A}" >> "$LIST"
    PARTS+=("$dst")
  done

  FULL="$OUT/полная.mp4"
  if [ ${#PARTS} -gt 1 ]; then
    echo ">>> склейка"
    ffmpeg -y -v warning -stats -f concat -safe 0 -i "$LIST" -c copy -movflags +faststart "$FULL"
  else
    FULL="${PARTS[1]}"
  fi
  rm -f "$LIST"
}

if [ $MP4 -eq 1 ]; then
  reencode "$@"
elif [ $# -eq 1 ]; then
  FULL="$1"                      # один файл: работаем прямо с исходником
else
  # Несколько кусков одной встречи обычно записаны одинаково, и их можно
  # склеить без пережатия. Проверяем результат по длительности: если она
  # не сошлась с суммой кусков, склейка вышла битой — тогда перекодируем.
  LIST="$OUT/.concat.txt"; : > "$LIST"
  SUM=0
  for src in "$@"; do
    printf "file '%s'\n" "${src:A}" >> "$LIST"
    SUM=$(( SUM + $(length "$src") ))
  done
  FULL="$OUT/полная.${1:e}"
  echo ">>> склейка без пережатия"
  DIFF=99
  if ffmpeg -y -v error -f concat -safe 0 -i "$LIST" -c copy "$FULL" 2>/dev/null; then
    DIFF=$(( $(length "$FULL") - SUM ))
  fi
  if (( DIFF < 2 && DIFF > -2 )); then
    rm -f "$LIST"
  else
    echo "    не вышло — куски записаны по-разному, перекодирую"
    rm -f "$LIST" "$FULL"
    reencode "$@"
  fi
fi

echo ">>> аудио"
ffmpeg -y -v error -i "$FULL" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/audio16k.wav"

echo
echo "длительность: $(length "$FULL") с"
echo "запись: $FULL"
