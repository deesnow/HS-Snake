#!/bin/sh
# Fix ownership of bind-mounted volumes so botuser can write to them.
# This script runs as root; after chown it drops to botuser via gosu.
chown -R botuser:botuser /app/logs /app/data 2>/dev/null || true
exec gosu botuser "$@"
