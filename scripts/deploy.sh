#!/usr/bin/env bash
set -e

DEFAULT_DEST="lxc-120:/opt/docker/player-stack/config/navidrome/plugins/"
DEST="${DEST:-${1:-$DEFAULT_DEST}}"

TELEGRAM_NDP="plugins/telegram-plugin/navidrome-telegram.ndp"
LYRICS_NDP="plugins/lyrics-plugin/navidrome-lyrics-plugin.ndp"

if [ ! -f "$TELEGRAM_NDP" ] && [ ! -f "$LYRICS_NDP" ]; then
    echo "Error: No .ndp packages found to deploy. Run 'make package' first."
    exit 1
fi

echo "Deploying Navidrome plugin packages to $DEST..."

if [ -f "$TELEGRAM_NDP" ]; then
    echo "  -> Transferring $TELEGRAM_NDP..."
    scp "$TELEGRAM_NDP" "$DEST"
fi

if [ -f "$LYRICS_NDP" ]; then
    echo "  -> Transferring $LYRICS_NDP..."
    scp "$LYRICS_NDP" "$DEST"
fi

echo "Restarting Navidrome container on lxc-120..."
ssh lxc-120 "docker restart navidrome"

echo "Deployment complete and Navidrome restarted successfully!"
