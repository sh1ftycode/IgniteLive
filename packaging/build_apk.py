"""Build Ignite for Android (android/): dist/Ignite-<version>.apk.

    python packaging/build_apk.py [--dist DIR]

Needs a JDK (17 or newer) and the Android SDK with a platform (android-35) and build-tools installed:
ANDROID_HOME (or ANDROID_SDK_ROOT) points at the SDK. Install them with Android Studio, or with the
command-line tools: `sdkmanager --licenses` (read and accept them yourself), then
`sdkmanager "platforms;android-35" "build-tools;35.0.0"`. GitHub's Ubuntu runners have both.

No Gradle: the app is one Java file with no libraries, so the SDK's own tools build it (aapt2, javac, d8,
zipalign, apksigner) in a few seconds.

Signing: with IGNITE_ANDROID_KEYSTORE (a .jks/.p12 file), IGNITE_ANDROID_KEYSTORE_PASSWORD and
IGNITE_ANDROID_KEY_ALIAS (default "ignite") it's signed with your release key. Without them, with a debug key
made in build/android/ (fine for trying it; phones won't update a debug-signed app with a release-signed one,
or the other way round, without uninstalling first).

The default server shown on first launch is the same one the desktop builds bundle: IGNITE_MAIN_SERVER, or
packaging/bundled_config.json. It's never in the source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "android"
BUILD = ROOT / "build" / "android"
MIN_SDK = 24  # Android 7.0.
TARGET_SDK = 35  # Android 15.


def version() -> str:
    text = (ROOT / "src" / "ignite_live" / "__init__.py").read_text("utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def version_code(name: str) -> int:
    """1.6.4 -> 10604: always higher for a newer version (Android refuses to update to a lower one)."""
    parts = [int(p) for p in re.findall(r"\d+", name)[:3]] + [0, 0, 0]
    return parts[0] * 10000 + parts[1] * 100 + parts[2]


def main_server() -> str:
    url = os.environ.get("IGNITE_MAIN_SERVER", "").strip()
    if not url:
        try:
            data = json.loads((ROOT / "packaging" / "bundled_config.json").read_text("utf-8"))
            url = str(data.get("main_server", "")).strip()
        except (OSError, ValueError, AttributeError):
            url = ""
    return url.rstrip("/")


def sdk() -> tuple[Path, Path]:
    """(build-tools folder, android.jar), the newest of each that's installed."""
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not home or not Path(home).is_dir():
        raise SystemExit("Set ANDROID_HOME to the Android SDK (see the top of this file).")
    tools = sorted((Path(home) / "build-tools").glob("*"), key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)])
    jars = sorted((Path(home) / "platforms").glob("android-*/android.jar"),
                  key=lambda p: int(re.sub(r"\D", "", p.parent.name) or 0))
    if not tools or not jars:
        raise SystemExit('Install a platform and build-tools: sdkmanager "platforms;android-35" "build-tools;35.0.0"')
    return tools[-1], jars[-1]


def tool(folder: Path, name: str) -> str:
    for candidate in (folder / name, folder / f"{name}.bat", folder / f"{name}.exe"):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"{name} isn't in {folder}.")


def run(*args: str) -> None:
    print("  " + " ".join(Path(a).name if os.sep in a and not a.startswith("-") else a for a in args[:3]) + " …")
    subprocess.run(args, check=True)


def keystore() -> tuple[str, str, str]:
    """(file, password, alias): the release key from the environment, or a debug key made here."""
    path = os.environ.get("IGNITE_ANDROID_KEYSTORE", "")
    if path:
        password = os.environ.get("IGNITE_ANDROID_KEYSTORE_PASSWORD", "")
        if not password:
            raise SystemExit("Set IGNITE_ANDROID_KEYSTORE_PASSWORD too.")
        return path, password, os.environ.get("IGNITE_ANDROID_KEY_ALIAS") or "ignite"
    debug = BUILD / "debug.keystore"
    if not debug.exists():
        keytool = shutil.which("keytool") or str(Path(os.environ.get("JAVA_HOME", "")) / "bin" / "keytool")
        run(keytool, "-genkeypair", "-keystore", str(debug), "-storepass", "android", "-keypass", "android",
            "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=Android Debug,O=Android,C=US")
    print("Signing with a debug key (set IGNITE_ANDROID_KEYSTORE for a release key).")
    return str(debug), "android", "androiddebugkey"


def build(dist: Path) -> Path:
    tools, android_jar = sdk()
    name, code, server = version(), version_code(version()), main_server()
    print(f"Ignite {name} for Android (code {code}) with {tools.name} and {android_jar.parent.name}")
    if BUILD.exists():
        for child in ("res", "compiled", "gen", "classes", "dex"):
            shutil.rmtree(BUILD / child, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    # Resources, plus the values decided at build time (version, default server).
    shutil.copytree(APP / "res", BUILD / "res")
    (BUILD / "res" / "values" / "build.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <string name="version" translatable="false">{escape(name)}</string>\n'
        f'    <string name="default_server" translatable="false">{escape(server)}</string>\n'
        "</resources>\n", "utf-8")
    aapt2 = tool(tools, "aapt2")
    (BUILD / "compiled").mkdir()
    run(aapt2, "compile", "--dir", str(BUILD / "res"), "-o", str(BUILD / "compiled" / "res.zip"))
    unsigned = BUILD / "unsigned.apk"
    run(aapt2, "link", "-I", str(android_jar), "--manifest", str(APP / "AndroidManifest.xml"),
        "--min-sdk-version", str(MIN_SDK), "--target-sdk-version", str(TARGET_SDK),
        "--version-code", str(code), "--version-name", name,
        "--java", str(BUILD / "gen"), "-o", str(unsigned), str(BUILD / "compiled" / "res.zip"))

    # Java → classes → dex.
    sources = [str(p) for p in sorted((APP / "src").rglob("*.java")) + sorted((BUILD / "gen").rglob("*.java"))]
    javac = shutil.which("javac") or str(Path(os.environ.get("JAVA_HOME", "")) / "bin" / "javac")
    run(javac, "-source", "8", "-target", "8", "-bootclasspath", str(android_jar), "-Xlint:-options",
        "-encoding", "UTF-8", "-d", str(BUILD / "classes"), *sources)
    classes = [str(p) for p in (BUILD / "classes").rglob("*.class")]
    (BUILD / "dex").mkdir()
    run(tool(tools, "d8"), "--release", "--min-api", str(MIN_SDK), "--lib", str(android_jar),
        "--output", str(BUILD / "dex"), *classes)
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as apk:
        apk.write(BUILD / "dex" / "classes.dex", "classes.dex")

    # Align, then sign.
    aligned = BUILD / "aligned.apk"
    run(tool(tools, "zipalign"), "-p", "-f", "4", str(unsigned), str(aligned))
    store, password, alias = keystore()
    dist.mkdir(parents=True, exist_ok=True)
    out = dist / f"Ignite-{name}.apk"
    env = {**os.environ, "IGNITE_KS_PASS": password}
    subprocess.run([tool(tools, "apksigner"), "sign", "--ks", store, "--ks-pass", "env:IGNITE_KS_PASS",
                    "--ks-key-alias", alias, "--min-sdk-version", str(MIN_SDK), "--out", str(out), str(aligned)],
                   check=True, env=env)
    subprocess.run([tool(tools, "apksigner"), "verify", str(out)], check=True)
    print(f"Built {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}"
          + ("" if server else " (no default server: the app asks for one)"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        build(args.dist)
    except subprocess.CalledProcessError as error:
        print(f"Failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
