"""
PyInstaller entry point for Auctus Agent.
Starts the FastAPI server and opens a native PyWebView window.
Falls back to system browser if pywebview is not available.
"""
import sys
import os
import threading
import time
import urllib.request
import hashlib


def _setup_bundle_paths():
    """Adjust sys.path, set env vars, and patch importlib.resources for frozen env."""
    if not getattr(sys, "frozen", False):
        return

    bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
    exe_dir = os.path.dirname(sys.executable)
    os.chdir(exe_dir)
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

    # Point tiktoken to its bundled encoding cache (must be set before any import of litellm/tiktoken)
    cache_candidates = [
        os.path.join(bundle_dir, "tiktoken_cache"),
        os.path.join(exe_dir, "_internal", "tiktoken_cache"),
        os.path.join(exe_dir, "tiktoken_cache"),
    ]
    os.environ["TIKTOKEN_CACHE_DIR"] = next(
        (path for path in cache_candidates if os.path.isdir(path)),
        cache_candidates[0],
    )

    try:
        import tiktoken.load as _tiktoken_load

        _orig_read_file_cached = _tiktoken_load.read_file_cached

        def _read_file_cached(blobpath, expected_hash=None):
            cache_key = hashlib.sha1(blobpath.encode()).hexdigest()
            for cache_dir in cache_candidates:
                cache_path = os.path.join(cache_dir, cache_key)
                if not os.path.exists(cache_path):
                    continue
                with open(cache_path, "rb", buffering=0) as f:
                    data = f.read()
                if expected_hash is None or hashlib.sha256(data).hexdigest() == expected_hash:
                    return data
            return _orig_read_file_cached(blobpath, expected_hash)

        _tiktoken_load.read_file_cached = _read_file_cached
    except Exception:
        pass

    # Python 3.9 + PyInstaller: importlib.resources can't find package data files
    # because __spec__.submodule_search_locations is broken in frozen context.
    # Patch open_text / open_binary to fall back to the _MEIPASS directory tree.
    import importlib.resources as _ir

    _orig_open_text = _ir.open_text
    _orig_open_binary = _ir.open_binary

    def _open_text(package, resource, encoding="utf-8", errors="strict"):
        try:
            return _orig_open_text(package, resource, encoding=encoding, errors=errors)
        except (FileNotFoundError, TypeError):
            for root in (bundle_dir, os.path.join(exe_dir, "_internal"), exe_dir):
                pkg_path = os.path.join(root, *package.split("."))
                resource_path = os.path.join(pkg_path, resource)
                if os.path.exists(resource_path):
                    return open(resource_path, encoding=encoding, errors=errors)
            return _orig_open_text(package, resource, encoding=encoding, errors=errors)

    def _open_binary(package, resource):
        try:
            return _orig_open_binary(package, resource)
        except (FileNotFoundError, TypeError):
            for root in (bundle_dir, os.path.join(exe_dir, "_internal"), exe_dir):
                pkg_path = os.path.join(root, *package.split("."))
                resource_path = os.path.join(pkg_path, resource)
                if os.path.exists(resource_path):
                    return open(resource_path, "rb")
            return _orig_open_binary(package, resource)

    _ir.open_text = _open_text
    _ir.open_binary = _open_binary


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{url}/healthz", timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def _run_server(fastapi_app, port: int) -> None:
    import uvicorn
    uvicorn.run(fastapi_app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    _setup_bundle_paths()

    # Direct import required so PyInstaller traces and bundles the app package
    from app.server import app as fastapi_app

    port = 8000
    url = f"http://127.0.0.1:{port}"

    # uvicorn runs in background; PyWebView owns the main thread
    server_thread = threading.Thread(
        target=_run_server, args=(fastapi_app, port), daemon=True
    )
    server_thread.start()

    print("Starting Auctus Agent...")
    if not _wait_for_server(url):
        print("ERROR: Server failed to start within 30 seconds.")
        sys.exit(1)

    try:
        import webview  # noqa: F401 — triggers pywebview bundle inclusion
        window = webview.create_window(
            title="Auctus Agent",
            url=f"{url}?desktop=1",
            width=1400,
            height=900,
            min_size=(900, 600),
            resizable=True,
            confirm_close=True,
            text_select=True,
        )
        webview.start(debug=False, http_server=False, private_mode=False)
    except ImportError:
        # pywebview not bundled — fall back to system browser
        import webbrowser
        webbrowser.open(url)
        server_thread.join()


if __name__ == "__main__":
    main()
