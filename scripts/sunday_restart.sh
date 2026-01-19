#!/bin/bash
# Sunday Gateway Restart Script
# Restarts gateway if not connected, triggering fresh 2FA
#
# Schedule (cron on EC2):
#   0 10 * * 0    - Primary restart at 10:00 AM ET (attend this one)
#   0 11-18 * * 0 - Retry every hour if missed

cd ~/ibkr-spy-puts

# Check if connected
STATUS=$(curl -s http://localhost:8000/api/connection-status 2>/dev/null | grep -o '"logged_in": true' || echo "")

if [ -z "$STATUS" ]; then
    echo "$(date): Not connected - restarting gateway for 2FA"
    sudo docker-compose restart ib-gateway
else
    echo "$(date): Already connected - no restart needed"
fi
