#!/bin/sh
# Virtual screen :99 -> VNC (this container only) -> noVNC web page on :6080, then the agent server on :5100.
# :5100 stays on the compose network for the web app; :6080 (noVNC) is published in docker-compose.prod.yml.
set -e
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
until [ -e /tmp/.X11-unix/X99 ]; do sleep 0.2; done
fluxbox >/dev/null 2>&1 &
x11vnc -display :99 -forever -shared -nopw -localhost -quiet >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
exec python -m psc_agent agent-server --host 0.0.0.0 --pdf "/data/original paper" --chrome-profile /profile "$@"
