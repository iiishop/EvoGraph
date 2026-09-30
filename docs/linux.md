# Linux desktop setup

EvoGraph uses pywebview's native bridge in desktop mode. Linux needs an explicit
WebView backend as well as a logged-in graphical session. Browser mode remains
an optional HTTP transport; it is not substituted silently for desktop mode.

## Recommended: Qt / PySide6

From the repository checkout, with Python 3.11+ and Node.js/npm installed:

```sh
npm ci
uv sync --extra linux
uv run --extra linux python run.py --gui qt
```

The `linux` extra installs PySide6 (including Qt WebEngine) and QtPy through
pywebview's supported `pyside6` extra. Keep `--extra linux` on subsequent `uv run`
commands so uv retains these optional dependencies. Without uv:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[linux]'
.venv/bin/python run.py --gui qt
```

Run from a terminal in your Linux desktop, not a headless SSH/container session.
`DISPLAY` (X11) or `WAYLAND_DISPLAY` (Wayland) must identify the real session.
Do not invent a display address or disable Qt WebEngine's Chromium sandbox.

Qt wheels include Qt itself, but still need the distribution's native graphics,
font, X11/Wayland and xcb libraries. If Qt reports a missing shared library or
platform plugin, install the corresponding package for your distribution.
See [Qt's Linux requirements](https://doc.qt.io/qt-6/linux-requirements.html) and
[pywebview installation](https://pywebview.flowrl.com/guide/installation).
No system packages or security settings are changed by EvoGraph's launcher.

## GTK alternative

Install your distribution's GTK 3, WebKit2GTK and GObject introspection development
packages, then install `pywebview[gtk]` into the **same Python environment** used
to run EvoGraph. Use `python run.py --gui gtk`. A system `python3-gi` installation
is not automatically available to a separately installed Python or isolated venv;
the Python versions and native bindings must match. Consult pywebview's
installation guide for the current distro-specific package names.

## Writable state and secrets

The existing default remains `~/.evograph`. On a restricted system, explicitly
choose a writable private directory:

```sh
uv run --extra linux python run.py --gui qt --data-dir /path/to/writable/evograph
```

`EVOGRAPH_DATA_DIR` is equivalent. EvoGraph reports an actionable error if it
cannot open this directory; it never silently relocates existing data.

Model keys still use the OS credential store via keyring. On Linux this generally
requires a desktop session bus and an unlocked, configured Secret Service
provider. Project management and the demo work without a model key. If the
credential store is unavailable, model configuration fails closed; there is no
plaintext key fallback. Establishing a credential store is a separate security
setup step. Tests below do not need real keys or make paid model calls.

## Verification

```sh
uv sync --extra linux --extra test
uv run --extra linux --extra test pytest
uv run --extra linux --extra test ruff check backend tests
npm test
npm run build
```

`QT_QPA_PLATFORM=offscreen` can be used for explicit native integration tests. It
is not a visible desktop and must not be presented as successful on-screen use.
Headless HTTP mode is separately available with `python run.py --browser` and
binds to loopback only. Do not disable browser or network protections if a hosted
environment prevents reaching this loopback service.

Native smoke test (disposable data, no model calls):

```sh
QT_QPA_PLATFORM=offscreen QT_API=pyside6 \
  uv run --extra linux python tools/smoke_desktop.py
```

This checks that the real Qt WebEngine loads the Vue demo, receives its native
bridge data, and that project creation/settings commands succeed. It does not
replace mouse/keyboard testing of a visible desktop window. In restricted
containers a command timeout can be used to bound native platform failures.
