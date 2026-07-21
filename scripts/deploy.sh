#!/usr/bin/env bash
set -e

DEST="${DEST:-${1:-$NAV_SERVER}}"

if [ -z "$DEST" ]; then
    echo "Error: Destination not specified."
    echo "Usage: make deploy DEST=<user@host:/path/to/plugins> or ./scripts/deploy.sh <user@host:/path/to/plugins>"
    exit 1
fi

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

echo "Deployment complete!"
