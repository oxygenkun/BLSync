#!/bin/sh
set -e

# Use UID/GID from environment or default to 1000
UID=${UID:-1000}
GID=${GID:-1000}

# Create group if it doesn't exist
if ! getent group appuser >/dev/null 2>&1; then
    # Try to create with specific GID, fallback to any GID if conflict
    addgroup -g ${GID} appuser 2>/dev/null || addgroup appuser
fi

# Create user if it doesn't exist
if ! id appuser >/dev/null 2>&1; then
    adduser -u ${UID} -G appuser -D -s /bin/sh appuser
fi

# Fix ownership of /app directory
chown -R appuser:appuser /app

# Switch to appuser and run the command
exec su-exec appuser "$@"
