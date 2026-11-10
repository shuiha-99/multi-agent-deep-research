#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
base="$root/.cache/vendor/redis-8.10.3-linux"
data="$root/data/redis8"
server="$base/usr/bin/redis-server"
cli="$base/usr/bin/redis-cli"
module="$base/usr/lib/redis/modules/redisearch.so"
config="$data/redis.conf"
password_file="$data/password.txt"
port=6380
action=${1:-status}
if [[ ! -x "$server" || ! -f "$module" ]]; then
  echo 'Project Redis binaries missing. Run scripts/redis-setup.ps1 first.' >&2
  exit 1
fi
mkdir -p "$data"
umask 077
if [[ ! -s "$password_file" ]]; then
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$password_file"
fi
export REDISCLI_AUTH
REDISCLI_AUTH=$(cat "$password_file")
own_server() {
  local actual
  actual=$("$cli" --no-auth-warning --raw -h 127.0.0.1 -p "$port" INFO server 2>/dev/null | tr -d '\r' | sed -n 's/^config_file://p') || return 1
  [[ "$actual" == "$config" ]]
}
status() {
  if ! own_server; then
    echo 'Project Redis is not running on 6380, or another service owns that port.' >&2
    return 1
  fi
  "$cli" --no-auth-warning --raw -h 127.0.0.1 -p "$port" PING
  "$cli" --no-auth-warning --raw -h 127.0.0.1 -p "$port" INFO server | tr -d '\r' | sed -n '/^redis_version:/p'
  "$cli" --no-auth-warning --raw -h 127.0.0.1 -p "$port" FT._LIST
  echo 'Project Redis Search available on 127.0.0.1:6380.'
}
case "$action" in
  start)
    if own_server; then status; exit 0; fi
    cat > "$config" <<EOF
bind 127.0.0.1
port $port
protected-mode yes
requirepass $REDISCLI_AUTH
daemonize yes
dir "$data"
pidfile "$data/redis.pid"
logfile "$data/redis.log"
appendonly yes
appendfsync everysec
maxmemory 256mb
maxmemory-policy noeviction
loadmodule "$module"
EOF
    "$server" "$config"
    for attempt in $(seq 1 30); do
      if own_server; then status; exit 0; fi
      sleep 0.2
    done
    echo 'Project Redis failed to start. Inspect data/redis8/redis.log.' >&2
    exit 1
    ;;
  stop)
    if ! own_server; then
      echo 'Refusing to stop a process not identified as this project Redis.' >&2
      exit 1
    fi
    "$cli" --no-auth-warning -h 127.0.0.1 -p "$port" SHUTDOWN SAVE
    echo 'Project Redis stopped; data retained.'
    ;;
  status) status ;;
  *) echo 'Usage: redis-local.sh start|status|stop' >&2; exit 2 ;;
esac
