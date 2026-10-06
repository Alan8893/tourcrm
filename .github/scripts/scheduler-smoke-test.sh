#!/usr/bin/env bash
# Runtime smoke test of the scheduler container (Issue #289, ADR-0044).
#
# Usage: scheduler-smoke-test.sh IMAGE DATABASE_URL
#
# IMAGE is the built apps/api image; DATABASE_URL must reach a migrated
# PostgreSQL from a container on the host network. Starts two scheduler
# containers exactly as configured (`supercronic -json
# /app/scheduler/crontab` as the main process) and waits for real
# every-minute iterations:
#   - against the database: the reconciliation CLI runs and succeeds;
#   - against an unreachable database: each run fails with its exit status
#     logged, the scheduler keeps running and the next minute's run still
#     happens, and the database password never appears in the logs.
# Then stops both and checks the graceful SIGTERM shutdown.
set -euo pipefail

image="$1"
database_url="$2"
ok=tourcrm-scheduler-smoke-ok
failing=tourcrm-scheduler-smoke-failing
secret="smoke-secret-$RANDOM$RANDOM"
scheduler_command=(supercronic -json /app/scheduler/crontab)

cleanup() { docker rm -f "$ok" "$failing" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker run -d --name "$ok" --network host \
  -e DATABASE_URL="$database_url" "$image" "${scheduler_command[@]}" >/dev/null
docker run -d --name "$failing" --network none \
  -e DATABASE_URL="postgresql+psycopg://scheduler:${secret}@127.0.0.1:1/unreachable" \
  "$image" "${scheduler_command[@]}" >/dev/null

# The first run starts at the next minute boundary; two failing runs need
# up to ~2 minutes.
deadline=$((SECONDS + 150))
until docker logs "$ok" 2>&1 | grep -q '"msg":"job succeeded"' &&
  [ "$(docker logs "$failing" 2>&1 | grep -c '"msg":"error running command: exit status 1"')" -ge 2 ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "::error::scheduler did not produce the expected runs in time"
    docker logs "$ok" 2>&1 | tail -n 40
    docker logs "$failing" 2>&1 | tail -n 40
    exit 1
  fi
  sleep 5
done

echo "--- scheduler logs (database reachable) ---"
docker logs "$ok" 2>&1
echo "--- scheduler logs (database unreachable, tail) ---"
docker logs "$failing" 2>&1 | grep -v '"channel":"stderr"' || true

docker logs "$ok" 2>&1 | grep -q 'Event lifecycle reconciliation: .* due event(s)'
docker logs "$ok" 2>&1 | grep -q '"job.command":"timeout --verbose --kill-after=5s 50s python -m app.cli.reconcile_event_lifecycle"'
[ "$(docker exec "$ok" cat /proc/1/comm)" = "supercronic" ] || { echo "::error::supercronic is not PID 1"; exit 1; }
[ "$(docker exec "$ok" id -u)" != "0" ] || { echo "::error::scheduler runs as root"; exit 1; }
[ "$(docker inspect -f '{{.State.Running}}' "$failing")" = "true" ] || { echo "::error::scheduler exited after a failed run"; exit 1; }
if docker logs "$failing" 2>&1 | grep -q "$secret"; then
  echo "::error::database password leaked into the scheduler logs"
  exit 1
fi

docker stop -t 60 "$ok" "$failing" >/dev/null
for container in "$ok" "$failing"; do
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container")"
  [ "$exit_code" = "0" ] || { echo "::error::$container exit code $exit_code after SIGTERM"; exit 1; }
  docker logs "$container" 2>&1 | grep -q '"msg":"received terminated, shutting down"'
done
echo "scheduler smoke test passed"
