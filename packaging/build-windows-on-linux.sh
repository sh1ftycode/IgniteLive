#!/usr/bin/env bash
# Build the Windows version of Ignite (Ignite.exe + Setup installer) on Linux.
#
#   packaging/build-windows-on-linux.sh [--bundle-keys]
#
# --bundle-keys bakes your API keys (YouTube, Euler) into the .exe so a fresh
# Windows install already has them. Keep that .exe private.
#
# PyInstaller can't cross-compile, so this runs the *Windows* Python and
# PyInstaller under Wine, in a private Wine prefix that never touches ~/.wine.
# The first run downloads Python and Inno Setup into that prefix (a few
# minutes); later runs reuse it. Output: dist/windows/
#
# Start over: rm -rf ~/.cache/ignite-wine
set -euo pipefail

PY_VERSION=3.13.16
PY_SHA256=fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b
INNO_VERSION=6.7.3
INNO_SHA256=9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/ignite-wine"
export WINEPREFIX="$CACHE/prefix" WINEARCH=win64 WINEDEBUG=-all
# Skip the Mono/Gecko install prompts: nothing here needs .NET or Internet Explorer.
export WINEDLLOVERRIDES="mscoree,mshtml="
# Mesa probes the GPU every time Wine starts and prints harmless driver warnings.
export EGL_LOG_LEVEL=fatal

WIN_PYTHON='C:\Python313\python.exe'
WIN_ISCC='C:\InnoSetup6\ISCC.exe'

# wine_run CMD... : run a Wine command, hiding only Mesa's GPU-probe warnings.
wine_run() {
    "$@" 2> >(grep --line-buffered -vE '^(MESA-EGL: warning|pci id for fd|libEGL warning)' >&2 || true)
}

say() { printf '\033[1m==> %s\033[0m\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v wine >/dev/null || die "Wine isn't installed.
  Arch/CachyOS:  sudo pacman -S wine
  Debian/Ubuntu: sudo apt install wine64
  Fedora:        sudo dnf install wine"

# fetch URL NAME SHA256 -> prints the path of a verified download (cached).
fetch() {
    local url=$1 file="$CACHE/downloads/$2" sum=$3
    if [[ ! -f $file ]] || ! echo "$sum  $file" | sha256sum --check --quiet - >/dev/null 2>&1; then
        say "Downloading $2"
        mkdir -p "$CACHE/downloads"
        curl -fL --retry 3 --progress-bar -o "$file.part" "$url"
        echo "$sum  $file.part" | sha256sum --check --quiet - || die "Checksum mismatch for $2; not using it."
        mv "$file.part" "$file"
    fi
    printf '%s\n' "$file"
}

# system.reg is written at the end of setup, so this also repairs an interrupted first run.
if [[ ! -f $WINEPREFIX/system.reg ]]; then
    say "Setting up a private Wine prefix in $WINEPREFIX (first run only, about a minute)"
    mkdir -p "$CACHE"
    wine_run wineboot --init
    wine_run wineserver --wait
fi

if [[ ! -f $WINEPREFIX/drive_c/Python313/python.exe ]]; then
    installer=$(fetch "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-amd64.exe" \
        "python-$PY_VERSION-amd64.exe" "$PY_SHA256")
    say "Installing Windows Python $PY_VERSION (inside Wine)"
    wine_run wine "$installer" /quiet InstallAllUsers=0 PrependPath=0 Shortcuts=0 Include_launcher=0 \
        Include_test=0 Include_tcltk=0 Include_doc=0 TargetDir='C:\Python313'
    wine_run wineserver --wait
    [[ -f $WINEPREFIX/drive_c/Python313/python.exe ]] || die "Python didn't install. Try: rm -rf $CACHE"
fi

if [[ ! -f $WINEPREFIX/drive_c/InnoSetup6/ISCC.exe ]]; then
    installer=$(fetch "https://github.com/jrsoftware/issrc/releases/download/is-${INNO_VERSION//./_}/innosetup-$INNO_VERSION.exe" \
        "innosetup-$INNO_VERSION.exe" "$INNO_SHA256")
    say "Installing Inno Setup $INNO_VERSION (inside Wine)"
    wine_run wine "$installer" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER /DIR='C:\InnoSetup6'
    wine_run wineserver --wait
    [[ -f $WINEPREFIX/drive_c/InnoSetup6/ISCC.exe ]] || die "Inno Setup didn't install. Try: rm -rf $CACHE"
fi

cd "$ROOT"
say "Installing Ignite's dependencies into Windows Python (about 200 MB the first time)"
wine_run wine "$WIN_PYTHON" -m pip install --disable-pip-version-check --no-warn-script-location ".[desktop,build]"

build_args=(--dist dist/windows)
if [[ ${1:-} == --bundle-keys ]]; then
    # Read the keys from the Linux keychain now; the Windows Python under Wine can't see it.
    say "Exporting your API keys from this computer's keychain"
    trap 'rm -f "$ROOT/packaging/bundled_keys.json"' EXIT  # Never leave the keys behind, even on failure.
    "$ROOT/.venv/bin/python" packaging/export_keys.py
    build_args+=(--bundle-keys)
fi

say "Building the Windows app"
export ISCC="$WIN_ISCC"
wine_run wine "$WIN_PYTHON" packaging/build.py "${build_args[@]}"
wine_run wineserver --wait

say "Done. Windows files are in dist/windows:"
for file in dist/windows/*.exe dist/windows/*.zip; do
    [[ -e $file ]] && printf '    %s\n' "${file##*/}" >&2
done
