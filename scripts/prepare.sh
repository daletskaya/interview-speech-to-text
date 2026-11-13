#!/bin/zsh
# Готовит записи к расшифровке: конвертирует в mp4, склеивает, извлекает аудио.
#
#   prepare.sh ВЫХОДНАЯ_ПАПКА файл1 файл2 ...
#
# Файлы перечисляй в хронологическом порядке. У записей Google Meet время
# начала стоит прямо в имени, так что сортировка по имени обычно и есть
# хронология — но проверь по длительностям, что конец одной стыкуется
# с началом следующей.
#
# На выходе: отдельные mp4, «полная.mp4» и «audio16k.wav».

set -e
# На части маков хомбрю оставляет ffmpeg со ссылкой на старую libtiff.
# Если такая версия ещё лежит в Cellar, подставляем её; иначе строка
# просто добавит лишние пути, и ничего не изменится.
for d in /usr/local/Cellar/libtiff/*/lib /opt/homebrew/Cellar/libtiff/*/lib; do
  [ -d "$d" ] && export DYLD_FALLBACK_LIBRARY_PATH="$d:/usr/local/lib:/opt/homebrew/lib:/usr/lib"
done

OUT="$1"; shift
[ -z "$OUT" ] && { echo "нужна выходная папка"; exit 1; }
[ $# -eq 0 ] && { echo "нужен хотя бы один файл"; exit 1; }
mkdir -p "$OUT"

LIST="$OUT/.concat.txt"; : > "$LIST"
for src in "$@"; do
  base="${src:t:r}"
  dst="$OUT/$base.mp4"
  echo ">>> $base"
  ffmpeg -y -v warning -stats -i "$src" \
    -r 30 -vsync cfr -c:v h264_videotoolbox -b:v 5000k -maxrate 6500k -bufsize 12000k \
    -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 -ac 2 -movflags +faststart "$dst"
  printf "file '%s'\n" "$dst" >> "$LIST"
done

FULL="$OUT/полная.mp4"
if [ $# -gt 1 ]; then
  echo ">>> склейка"
  ffmpeg -y -v warning -stats -f concat -safe 0 -i "$LIST" -c copy -movflags +faststart "$FULL"
else
  cp "$OUT/${1:t:r}.mp4" "$FULL"
fi
rm -f "$LIST"

echo ">>> аудио"
ffmpeg -y -v error -i "$FULL" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/audio16k.wav"

echo
echo "длительность: $(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$FULL") с"
echo "готово: $FULL"
