package io.github.sh1ftycode.ignite;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.net.InetAddress;
import java.util.Locale;

/**
 * Ignite for Android: the Ignite server's web dashboard, full screen. Ignite on your PC stays the engine (OBS,
 * chat, music); this is the remote. A build with a main server always uses it; one without asks which server to
 * use, the first time. Links to other sites open in the browser, except Discord's sign-in, which comes back here.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "ignite";
    private static final String SERVER = "server";
    private static final String APP_SCHEME = "ignite-app";  // The dashboard's "Server" link: ignite-app:server.
    private static final int WINDOW = Color.rgb(0x0E, 0x10, 0x15);
    private static final int PANEL = Color.rgb(0x16, 0x19, 0x20);
    private static final int RAISED = Color.rgb(0x1E, 0x22, 0x2B);
    private static final int TEXT = Color.rgb(0xE8, 0xEA, 0xEE);
    private static final int MUTED = Color.rgb(0x9A, 0xA1, 0xAE);
    private static final int ACCENT = Color.rgb(0xFF, 0x7A, 0x3D);
    private static final int LIVE = Color.rgb(0xFF, 0x4D, 0x5E);

    private FrameLayout root;
    private WebView web;
    private String server = "";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);  // A remote shouldn't doze mid-stream.
        root = new FrameLayout(this);
        root.setBackgroundColor(WINDOW);
        fitSystemBars(root);
        setContentView(root);
        // A build with a main server always uses it (like the desktop builds); others ask, the first time.
        String bundled = allowed(getString(R.string.default_server));
        server = bundled != null ? bundled : getSharedPreferences(PREFS, MODE_PRIVATE).getString(SERVER, "");
        if (server.isEmpty()) {
            showSetup("", "");
        } else {
            showDashboard(state);
        }
    }

    /** Android 15 draws apps under the status and navigation bars: keep the page clear of them. */
    private static void fitSystemBars(View view) {
        view.setOnApplyWindowInsetsListener((v, insets) -> {
            int left, top, right, bottom;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                left = bars.left; top = bars.top; right = bars.right; bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft(); top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight(); bottom = insets.getSystemWindowInsetBottom();
            }
            v.setPadding(left, top, right, bottom);
            return insets;
        });
    }

    // The server.

    /** https:// anywhere; plain http:// only on this phone's own network (like the desktop app's rule). */
    static String allowed(String text) {
        String url = text.trim();
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        if (url.isEmpty()) return null;
        if (!url.contains("://")) url = "https://" + url;
        Uri uri = Uri.parse(url);
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
        if (host.isEmpty()) return null;
        if (scheme.equals("https")) return url;
        if (!scheme.equals("http")) return null;
        if (host.equals("localhost") || host.endsWith(".local") || host.endsWith(".lan") || host.endsWith(".home.arpa")
                || host.endsWith(".internal")) {
            return url;
        }
        return isPrivateAddress(host) ? url : null;
    }

    private static boolean isPrivateAddress(String host) {
        if (!host.matches("[0-9.]+") && !host.contains(":")) return false;  // A name: never looked up here.
        try {
            InetAddress address = InetAddress.getByName(host);
            return address.isSiteLocalAddress() || address.isLoopbackAddress() || address.isLinkLocalAddress();
        } catch (Exception e) {
            return false;
        }
    }

    private void showSetup(String prefill, String error) {
        if (web != null) {
            root.removeView(web);
            web.destroy();
            web = null;
        }
        root.removeAllViews();
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundColor(PANEL);
        int pad = dp(24);
        card.setPadding(pad, pad, pad, pad);

        TextView title = text("Ignite", 26, TEXT);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(title);
        TextView intro = text("Run your stream from your phone. Ignite on your PC stays in charge: this app "
                + "connects to the same Ignite server your PC is signed in to.", 15, MUTED);
        intro.setPadding(0, dp(8), 0, dp(20));
        card.addView(intro);
        card.addView(text("Server address", 13, MUTED));

        EditText field = new EditText(this);
        field.setSingleLine(true);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        field.setImeOptions(EditorInfo.IME_ACTION_GO);
        field.setHint("https://ignite.example.com");
        field.setText(prefill);
        boolean locked = allowed(getString(R.string.default_server)) != null;
        if (locked) {  // This build always uses its main server: only Try Again.
            field.setEnabled(false);
            field.setTextColor(MUTED);
        }
        field.setTextColor(TEXT);
        field.setHintTextColor(MUTED);
        field.setBackgroundColor(RAISED);
        field.setPadding(dp(12), dp(10), dp(12), dp(10));
        card.addView(field, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView problem = text(error, 14, LIVE);
        problem.setPadding(0, dp(8), 0, 0);
        problem.setVisibility(error.isEmpty() ? View.GONE : View.VISIBLE);
        card.addView(problem);

        Button connect = new Button(this);
        connect.setText(locked ? "Try Again" : "Connect");
        connect.setAllCaps(false);
        connect.setTextColor(WINDOW);
        connect.setBackgroundColor(ACCENT);
        LinearLayout.LayoutParams buttonSize = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48));
        buttonSize.topMargin = dp(20);
        card.addView(connect, buttonSize);

        Runnable go = () -> {
            String url = allowed(field.getText().toString());
            if (url == null) {
                problem.setText("Use an https:// address. Plain http:// only works for a server on your own network.");
                problem.setVisibility(View.VISIBLE);
                return;
            }
            server = url;
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(SERVER, url).apply();
            showDashboard(null);
        };
        connect.setOnClickListener(v -> go.run());
        field.setOnEditorActionListener((v, action, event) -> {
            go.run();
            return true;
        });

        FrameLayout.LayoutParams place = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        place.leftMargin = place.rightMargin = dp(16);
        root.addView(card, place);
    }

    // The dashboard.

    private void showDashboard(Bundle state) {
        root.removeAllViews();
        web = new WebView(this);
        web.setBackgroundColor(WINDOW);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);  // The tab, theme and "play music here" are remembered.
        settings.setMediaPlaybackRequiresUserGesture(false);  // Music in this app when no Now Playing overlay is open.
        settings.setUserAgentString(settings.getUserAgentString() + " IgniteApp/" + getString(R.string.version));
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new Client());
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        if (state == null || web.restoreState(state) == null) {
            web.loadUrl(server + "/app");
        }
    }

    private boolean ours(Uri uri) {
        Uri home = Uri.parse(server);
        String host = uri.getHost() == null ? "" : uri.getHost();
        if (host.equalsIgnoreCase(home.getHost() == null ? "" : home.getHost())
                && String.valueOf(uri.getScheme()).equalsIgnoreCase(String.valueOf(home.getScheme()))
                && uri.getPort() == home.getPort()) {
            return true;
        }
        // Discord's sign-in comes back to the server, so it stays in the app (its cookies are the login).
        return uri.getScheme() != null && uri.getScheme().equals("https")
                && (host.equals("discord.com") || host.endsWith(".discord.com"));
    }

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            if (APP_SCHEME.equals(uri.getScheme())) {
                showSetup(server, "");
                return true;
            }
            if (ours(uri)) return false;
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, uri));  // Streamlabs, YouTube, help pages: the browser.
            } catch (ActivityNotFoundException e) {
                // No browser: stay put.
            }
            return true;
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            // The page itself couldn't load (the server is down, or the address is wrong): back to the setup.
            // Once the dashboard has opened, its own offline page (the service worker) shows instead.
            if (request.isForMainFrame() && !view.canGoBack()) {
                showSetup(server, "Can't reach " + Uri.parse(server).getHost() + ". Check the address and your "
                        + "connection, then try again.");
            }
        }
    }

    // Plumbing.

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        if (web != null) web.saveState(state);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();  // Stay signed in when Android closes the app.
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        return view;
    }

    private int dp(int value) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, getResources().getDisplayMetrics()));
    }
}
