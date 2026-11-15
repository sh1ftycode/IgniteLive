# Ignite for Android

The web dashboard of your Ignite server, as an app: a remote for Ignite on your streaming PC. OBS, chat,
music and stream keys stay on the PC. The phone shows and controls them through the server, like the
dashboard in a browser does.

- **Server:** builds with a main server bundled (`IGNITE_MAIN_SERVER`, like the desktop's) always use it.
  Builds without one ask for the address the first time. Plain `http://` only works for a server on the
  phone's own network; anything else needs `https://`.
- **Signing in:** the server's own login page (password or Discord). Discord's sign-in stays in the app,
  so its login comes back here. Other sites (Streamlabs, YouTube) open in the browser.
- **Changing server** (builds without a main server): the **Server** link in the menu bar, next to Log Out.
- The screen stays on while the app is open. The music can play in the app when no Now Playing overlay is
  open (Music tab).

iPhone has no app to install. In Safari, open the dashboard, tap **Share**, then **Add to Home Screen**.
The dashboard says so the first time. That home-screen app and Android's **Install app** in Chrome use
the server's web app manifest and service worker (`web/ignite_web/sw.js`).

## Build

```sh
python packaging/build_apk.py        # → dist/Ignite-<version>.apk
```

It needs a JDK 17+ and the Android SDK (`ANDROID_HOME`) with `platforms;android-35` and a
`build-tools`. Accept the SDK's licenses yourself first (`sdkmanager --licenses`). There's no Gradle: the
app is one Java file with no libraries, built with the SDK's own tools. See the top of
`packaging/build_apk.py`.

The release workflow builds it too (the `android` job) and attaches it to each GitHub release.

## Signing

Phones only update an app signed with the same key, so make a release key once and keep it safe:

```sh
keytool -genkeypair -keystore ignite.keystore -alias ignite -keyalg RSA -keysize 4096 -validity 10000
```

For local builds, set `IGNITE_ANDROID_KEYSTORE` (the file), `IGNITE_ANDROID_KEYSTORE_PASSWORD` and
`IGNITE_ANDROID_KEY_ALIAS`. For the release workflow, add these repository secrets:
`IGNITE_ANDROID_KEYSTORE_B64` (`base64 -w0 ignite.keystore`), `IGNITE_ANDROID_KEYSTORE_PASSWORD` and
`IGNITE_ANDROID_KEY_ALIAS`. Without them, builds are signed with a throwaway debug key, and each one has to
be uninstalled before the next is installed. `*.keystore` files are ignored by git.

## Icons

`python packaging/make_mobile_icons.py` draws the launcher icons (`res/mipmap-*`) and the web app's icons from
the desktop's logo (`src/ignite_live/ui/icons.py`).
