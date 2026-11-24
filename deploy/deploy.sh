#!/bin/sh
# Выкладка кода на сервер: ./deploy/deploy.sh <ssh-хост>
# Перезапуск обрывает идущие сборки, поэтому сначала ждём, пока они закончатся (до 10 минут).
set -e
H=${1:?укажите ssh-хост: ./deploy/deploy.sh <хост>}
cd "$(dirname "$0")/.."
ssh "$H" '
  i=0
  while [ $i -lt 120 ]; do
    busy=$(find /var/lib/nitka/renders -maxdepth 2 -name status.json -mmin -3 -exec grep -lE "\"state\": \"(running|queued)\"" {} + 2>/dev/null | wc -l)
    [ "$busy" = "0" ] && break
    [ $i = 0 ] && echo "идёт сборка карт — жду, пока закончится…"
    i=$((i + 1)); sleep 5
  done'
tar czf - --exclude='__pycache__' --exclude='logo-src.png' engine server web fonts deploy/nitka.service | ssh "$H" '
  set -e
  mkdir -p /opt/nitka && cd /opt/nitka && rm -rf engine server web fonts && tar xzf - &&
  cp deploy/nitka.service /etc/systemd/system/nitka.service && systemctl daemon-reload && systemctl restart nitka && sleep 1.5 && systemctl is-active nitka'
