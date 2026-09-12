#!/bin/sh
# Entrypoint wrapper for the worker/api containers. Runs the migration step
# and, if it fails, sends a throttled Telegram alert before exiting — without
# this, `restart: unless-stopped` retries a crash-looping container silently
# forever (see README.md's alembic "Gotcha" note for how that happens). The
# throttle marker lives in /tmp so it resets on container recreation but not
# on a plain restart, meaning one alert per crash episode instead of one per
# retry. On success it clears the marker and execs the real service command.
set -e

MARKER=/tmp/.boot_alert_sent
SERVICE_NAME="${1:?boot.sh requires a service name as the first argument}"
shift

if ! python -m alembic upgrade head; then
    if [ ! -f "$MARKER" ]; then
        touch "$MARKER"
        BOOT_ALERT_TEXT="$SERVICE_NAME failed to start: alembic upgrade head failed (stale image vs already-migrated DB?). Rebuild with: docker compose up -d --build $SERVICE_NAME" \
            python -c "import os; from src.alerts import send_telegram_alert; send_telegram_alert(os.environ['BOOT_ALERT_TEXT'], source='boot')" || true
    fi
    exit 1
fi

rm -f "$MARKER"
exec "$@"
