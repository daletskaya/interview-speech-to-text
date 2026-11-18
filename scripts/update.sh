#!/bin/sh
# Проверяет и ставит новую версию скилла.
#
#   update.sh check   — есть ли новая версия и что в ней изменилось
#   update.sh apply   — обновить
#
# Скилл обновляется, только если его поставили клоном репозитория
# (по ссылке). Папка из архива ничего не знает о репозитории — тогда скрипт
# скажет, как переставить скилл.
#
# check ничего не меняет и не ждёт сеть дольше нескольких секунд: если
# GitHub недоступен, расшифровка не должна из-за этого стоять.

cd "$(dirname "$0")/.." || exit 1

if [ ! -d .git ]; then
  echo "скилл поставлен не по ссылке, а архивом — обновляться он не умеет."
  echo "Чтобы обновлять, удали папку $(pwd)"
  echo "и поставь скилл заново: git clone https://github.com/daletskaya/interview-speech-to-text"
  exit 2
fi

# Не спрашивать пароль и не висеть на медленной сети.
export GIT_TERMINAL_PROMPT=0 GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=5

if ! git fetch -q origin 2>/dev/null; then
  echo "не удалось связаться с GitHub — проверить обновления сейчас нельзя."
  exit 3
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)
BEHIND=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)

case "$1" in
  check)
    if [ "$BEHIND" -eq 0 ]; then
      echo "версия актуальная"
      exit 0
    fi
    echo "есть новая версия, изменений: $BEHIND"
    git log --format='  - %s' "HEAD..origin/$BRANCH"
    exit 10
    ;;
  apply)
    if [ "$BEHIND" -eq 0 ]; then
      echo "версия и так актуальная"
      exit 0
    fi
    # Свои файлы рядом (заполненный словарь, заметки) обновлению не мешают,
    # а вот правки в файлах скилла обновление затёрло бы.
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      echo "в файлах скилла есть свои правки — поверх них обновлять не буду:"
      git status --short --untracked-files=no
      echo "Сохрани их отдельно или удали и запусти снова."
      exit 4
    fi
    if git merge -q --ff-only "origin/$BRANCH"; then
      echo "обновлено, изменений: $BEHIND"
      git log --format='  - %s' "HEAD@{1}..HEAD"
    else
      echo "обновить не вышло: в папке скилла свои коммиты, которых нет на GitHub."
      exit 5
    fi
    ;;
  *)
    echo "нужно: update.sh check или update.sh apply"
    exit 1
    ;;
esac
