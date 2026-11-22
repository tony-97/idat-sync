# Verify hidden pywebview rAF & CSS animations on macOS and Linux

Status: ready-for-human

## Context

On Windows (Edge WebView2 / WinForms), `webview.create_window(..., hidden=True)` and `window.hide()` call `Form.Hide()`, which sets `document.visibilityState === 'hidden'` and freezes `requestAnimationFrame` and CSS animations (`animationend`). That broke Microsoft Entra ID's email -> password slide transition (`#i0118` stayed `opacity: 0` / `.moveOffScreen`).

Windows is now fixed in `src/idat_sync/infrastructure/sharepoint_auth.py` via `_cloak_win32_window()` (`WS_EX_LAYERED` alpha=0 + `WS_EX_TRANSPARENT` + `WS_EX_TOOLWINDOW` + `WS_EX_NOACTIVATE`), and Android already uses a `1x1` `View.INVISIBLE` WebView.

macOS (`WKWebView` / AppKit) and Linux (`WebKitGTK`) still use `hidden=True` + `window.hide()` and have not been tested against live Entra ID yet.

## Verification harness

Run this snippet on macOS and Linux (no credentials needed) to check whether `hidden=True` freezes `rAF` / CSS animations on that platform:

```python
import time
import webview

html = """<html><head><style>@keyframes f{from{opacity:0}to{opacity:1}}.a{animation:f 50ms forwards}</style></head>
<body><div id="d" class="a">x</div><script>
window.raf = 0; window.ae = 0;
requestAnimationFrame(() => { window.raf = 1; });
document.getElementById("d").addEventListener("animationend", () => { window.ae = 1; });
</script></body></html>"""

res = {}
w = webview.create_window("", html=html, hidden=True)

def run(win):
    time.sleep(1.0)
    res.update(
        win.evaluate_js(
            "({vis:document.visibilityState,raf:window.raf,ae:window.ae,"
            "op:getComputedStyle(document.getElementById('d')).opacity})"
        )
        or {}
    )
    win.destroy()

webview.start(run, w)
print("HIDDEN:", res)
```

- **Pass:** `raf: 1`, `ae: 1`, `op: '1'` -> `hidden=True` works as-is on that OS.
- **Fail:** `raf: 0` or `ae: 0` or `op: '0'` -> add a platform-specific cloak in `_create_hidden_window()` (e.g., `NSWindow` alpha=0 / `ignoresMouseEvents=True` on macOS, or equivalent non-unmapped invisible window on GTK), then re-test live Entra ID login.

## Related fixes already landed on Windows

1. `_js_visible_el` gates on `cs.visibility !== 'hidden'`, `parseFloat(cs.opacity) > 0`, `!el.disabled`, and `aria-disabled !== 'true'`.
2. MFA proof click targets `#idDiv_SAOTCS_Proofs > div:nth-child(2) [role="button"]` (inner button, not outer `role="listitem"`).
3. `_create_hidden_window` + `_cloak_win32_window` keep WebView2 active while invisible on Windows.
