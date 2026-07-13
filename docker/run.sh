#!/bin/bash
set -e

echo "=== SINator Rotator (Docker) ==="
echo "Container MAC: $(cat /sys/class/net/eth0/address 2>/dev/null || echo 'unknown')"
echo "Container hostname: $(hostname)"
echo "Python: $(python3 --version)"
echo ""
echo "LIVE BROWSER: http://localhost:6080/vnc.html"
echo ""

cd /app

# Start Xvfb
Xvfb :99 -screen 0 1920x1080x24 &>/dev/null &
export DISPLAY=:99
sleep 1

# Start VNC server (no password)
x11vnc -display :99 -forever -nopw -rfbport 5900 &>/dev/null &
sleep 1

# Start noVNC (web viewer)
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &>/dev/null &
sleep 1

echo "VNC: localhost:5900 | noVNC: http://localhost:6080/vnc.html"
echo ""

# Run rotation
python3 tools/rotate.py "$@" 2>&1

echo "=== Rotation complete ==="
