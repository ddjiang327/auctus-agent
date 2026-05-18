"""
PyInstaller entry point for Auctus Agent.
Runs the FastAPI server. The launcher (bat/command) handles browser opening.
"""
import sys
import os
import hashlib
import traceback


def _log(message: str) -> None:
    try:
        log_dir = os.path.join(os.path.dirname(sys.executable), "logs") if getattr(sys, "frozen", False) else "logs"
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "startup.log"), "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def _setup_bundle_paths():
    if not getattr(sys, "frozen", False):
        return

    bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
    exe_dir = os.path.dirname(sys.executable)
    os.chdir(exe_dir)
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

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

        _orig = _tiktoken_load.read_file_cached

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
            return _orig(blobpath, expected_hash)

        _tiktoken_load.read_file_cached = _read_file_cached
    except Exception:
        pass

    import importlib.resources as _ir

    _orig_open_text = _ir.open_text
    _orig_open_binary = _ir.open_binary

    def _open_text(package, resource, encoding="utf-8", errors="strict"):
        try:
            return _orig_open_text(package, resource, encoding=encoding, errors=errors)
        except (FileNotFoundError, TypeError):
            for root in (bundle_dir, os.path.join(exe_dir, "_internal"), exe_dir):
                p = os.path.join(root, *package.split("."), resource)
                if os.path.exists(p):
                    return open(p, encoding=encoding, errors=errors)
            return _orig_open_text(package, resource, encoding=encoding, errors=errors)

    def _open_binary(package, resource):
        try:
            return _orig_open_binary(package, resource)
        except (FileNotFoundError, TypeError):
            for root in (bundle_dir, os.path.join(exe_dir, "_internal"), exe_dir):
                p = os.path.join(root, *package.split("."), resource)
                if os.path.exists(p):
                    return open(p, "rb")
            return _orig_open_binary(package, resource)

    _ir.open_text = _open_text
    _ir.open_binary = _open_binary


def main() -> None:
    try:
        _log("Auctus Agent starting")
        _setup_bundle_paths()

        _log("Importing FastAPI app")
        from app.server import app as fastapi_app

        import uvicorn
        _log("Starting uvicorn on 127.0.0.1:8000")
        uvicorn.run(
            fastapi_app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            log_config=None,
            access_log=False,
        )
    except SystemExit:
        raise
    except Exception:
        _log("Fatal startup failure:")
        _log(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
