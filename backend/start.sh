#!/bin/sh
# Start Xvfb, then the application.
#
# Not `xvfb-run`. That wrapper waits for Xvfb to signal readiness by trapping
# SIGUSR1 and calling `wait`; as PID 1 in a container that signal does not
# arrive, so it blocks forever before ever running uvicorn. The process looks
# alive, no port is ever opened, and the platform reports "no open ports
# detected" - with nothing in the logs to explain it, because the application
# genuinely never started.
#
# Polling the socket is the check that does not depend on a signal.
set -e

SCREEN_WIDTH=1366
SCREEN_HEIGHT=768
DISPLAY_NUM=99

Xvfb ":$DISPLAY_NUM" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24" -nolisten tcp &
XVFB_PID=$!

# Shut the display down with the application, so a restart does not leave one
# running per exited container.
trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT INT TERM

waited=0
while [ ! -e "/tmp/.X11-unix/X$DISPLAY_NUM" ]; do
    waited=$((waited + 1))
    if [ "$waited" -gt 300 ]; then
        echo "Xvfb did not create /tmp/.X11-unix/X$DISPLAY_NUM within 30s" >&2
        exit 1
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "Xvfb exited before the display was ready" >&2
        exit 1
    fi
    sleep 0.1
done

export DISPLAY=":$DISPLAY_NUM"

# Passed through as arguments, so the container still starts with an ordinary
# command line and an ordinary `docker run image --help`.
exec "$@" 2>&1