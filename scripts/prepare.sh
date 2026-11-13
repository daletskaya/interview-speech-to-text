#!/bin/zsh
# Готовит записи к расшифровке: конвертирует в mp4, склеивает, извлекает аудио.
#
#   prepare.sh ВЫХОДНАЯ_ПАПКА файл1 файл2 ...
#
# Файлы перечисляй в хронологическом порядке. У многих платформ время начала
# стоит прямо в имени, так что сортировка по имени обычно и есть хронология —
# но проверь по длительностям, что конец одной записи стыкуется с началом
# следующей.
#
# На выходе: отдельные mp4, «полная.mp4» и «audio16k.wav».

set -e

# На части маков хомбрю оставляет ffmpeg со ссылкой на старую libtiff.
# Если такая версия ещё лежит в Cellar, подставляем её. Версий может быть
# несколько, и нужная — не обязательно последняя, поэтому берём все.
# (N) — чтобы zsh не падал, когда папки нет вовсе.
OLD_LIBS=(/usr/local/Cellar/libtiff/*/lib(N) /opt/homebrew/Cellar/libtiff/*/lib(N))
if [ ${#OLD_LIBS} -gt 0 ]; then
  export DYLD_FALLBACK_LIBRARY_PATH="${(j.:.)OLD_LIBS}:/usr/local/lib:/opt/homebrew/lib:/usr/lib"
fi

OUT="$1"
[ -z "$OUT" ] && { echo "нужна выходная папка"; exit 1; }
shift
[ $# -eq 0 ] && { echo "нужен хотя бы один файл"; exit 1; }
mkdir -p "$OUT"

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
  echo ">>> $base"
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
  cp "${PARTS[1]}" "$FULL"
fi
rm -f "$LIST"

echo ">>> аудио"
ffmpeg -y -v error -i "$FULL" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/audio16k.wav"

echo
echo "длительность: $(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$FULL") с"
echo "готово: $FULL"
