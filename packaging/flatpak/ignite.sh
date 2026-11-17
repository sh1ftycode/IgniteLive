#!/bin/sh
# Ignite in its Flatpak: the PyInstaller build lives in /app/lib/ignite.
exec /app/lib/ignite/Ignite "$@"
