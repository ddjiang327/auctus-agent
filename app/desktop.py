#!/usr/bin/env python3
"""
Auctus Agent Desktop Shell
==========================
Native desktop application wrapper using PyWebView.

This module provides a native desktop window that hosts the Auctus Agent
web UI, eliminating the need for users to open a browser.

Features:
- Auto-starts FastAPI backend on a random available port
- Native window with system title bar and icons
- Graceful shutdown when window closes
- Single instance enforcement
- Dark mode support

Usage:
    python -m app.desktop
    # or
    python app/desktop.py
"""
from __future__ import annotations

import os
import sys
import time
import socket
import signal
import atexit
import subprocess
import threading
from pathlib import Path
from typing import Optional

# Platform-specific imports
if sys.platform != 'win32':
    import fcntl

import webview

# Ensure we're in the project root
os.chdir(Path(__file__).parent.parent)

# Configuration
MIN_PORT = 8000
MAX_PORT = 9000
HEALTH_CHECK_INTERVAL = 0.5
MAX_HEALTH_CHECK_ATTEMPTS = 60  # 30 seconds total

class AuctusDesktop:
    """Desktop application controller."""

    def __init__(self):
        self.port: Optional[int] = None
        self.server_process: Optional[subprocess.Popen] = None
        self.window: Optional[webview.Window] = None
        self._shutdown_event = threading.Event()

    def find_free_port(self, start: int = MIN_PORT, end: int = MAX_PORT) -> int:
        """Find an available port in the given range."""
        for port in range(start, end):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(('127.0.0.1', port)) != 0:
                    return port
        raise RuntimeError(f"No free port found in range {start}-{end}")

    def is_server_ready(self) -> bool:
        """Check if the FastAPI server is responding."""
        if not self.port:
            return False
        try:
            import urllib.request
            with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/healthz',
                timeout=1
            ) as response:
                return response.status == 200
        except Exception:
            return False

    def start_backend(self) -> None:
        """Start the FastAPI backend server."""
        self.port = self.find_free_port()
        print(f"[Desktop] Starting backend on port {self.port}...")

        # Determine Python executable
        if sys.platform == 'win32':
            python_exe = '.venv\\Scripts\\python.exe'
        else:
            python_exe = '.venv/bin/python'

        if not Path(python_exe).exists():
            python_exe = sys.executable

        # Start uvicorn in a subprocess
        env = os.environ.copy()
        env['AUCTUS_DESKTOP_MODE'] = '1'

        self.server_process = subprocess.Popen(
            [
                python_exe, '-m', 'uvicorn',
                'app.server:app',
                '--host', '127.0.0.1',
                '--port', str(self.port),
                '--log-level', 'warning'
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )

        # Wait for server to be ready
        print("[Desktop] Waiting for server to start...")
        for attempt in range(MAX_HEALTH_CHECK_ATTEMPTS):
            if self.is_server_ready():
                print(f"[Desktop] Server ready at http://127.0.0.1:{self.port}")
                return
            time.sleep(HEALTH_CHECK_INTERVAL)

        # Server failed to start
        self.shutdown()
        raise RuntimeError("Server failed to start within timeout")

    def create_window(self) -> None:
        """Create the native desktop window."""
        if not self.port:
            raise RuntimeError("Backend not started")

        url = f'http://127.0.0.1:{self.port}?desktop=1'

        # Window configuration
        window_config = {
            'title': 'Auctus Agent',
            'url': url,
            'width': 1400,
            'height': 900,
            'min_size': (900, 600),
            'resizable': True,
            'fullscreen': False,
            'frameless': False,
            'easy_drag': True,
            'on_top': False,
            'confirm_close': True,
            'text_select': True,
        }

        # Platform-specific adjustments
        if sys.platform == 'darwin':  # macOS
            window_config['title'] = 'Auctus Agent'
        elif sys.platform == 'win32':  # Windows
            window_config['title'] = 'Auctus Agent'

        self.window = webview.create_window(**window_config)

        # Set up window close handler
        self.window.events.closing += self.on_window_closing

    def on_window_closing(self) -> None:
        """Handle window close event."""
        print("[Desktop] Window closing, shutting down...")
        self._shutdown_event.set()
        self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shutdown the backend server."""
        print("[Desktop] Shutting down...")

        # Terminate server process
        if self.server_process and self.server_process.poll() is None:
            print("[Desktop] Stopping backend server...")
            if sys.platform == 'win32':
                self.server_process.terminate()
            else:
                self.server_process.send_signal(signal.SIGTERM)

            # Wait for graceful shutdown
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("[Desktop] Force killing backend...")
                self.server_process.kill()

        print("[Desktop] Shutdown complete.")

    def check_single_instance(self) -> bool:
        """Check if another instance is already running."""
        # Simple lock file approach
        lock_file = Path.home() / '.auctus_agent_desktop.lock'

        try:
            if sys.platform == 'win32':
                # Windows: use file existence with PID check
                if lock_file.exists():
                    try:
                        pid = int(lock_file.read_text().strip())
                        # Check if process is still running
                        import ctypes
                        kernel32 = ctypes.windll.kernel32
                        handle = kernel32.OpenProcess(1, False, pid)
                        if handle:
                            kernel32.CloseHandle(handle)
                            print("[Desktop] Another instance is already running.")
                            return False
                    except (ValueError, OSError):
                        pass
                    # Stale lock file, remove it
                    lock_file.unlink()

                # Create new lock file
                lock_file.write_text(str(os.getpid()))
                atexit.register(lambda: lock_file.unlink() if lock_file.exists() else None)
                return True
            else:
                # Unix: use fcntl for file locking
                self._lock_fd = open(lock_file, 'w')
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._lock_fd.write(str(os.getpid()))
                    self._lock_fd.flush()
                    atexit.register(self._release_lock)
                    return True
                except (IOError, OSError):
                    print("[Desktop] Another instance is already running.")
                    return False

        except Exception as e:
            print(f"[Desktop] Warning: Could not check single instance: {e}")
            return True  # Continue anyway

    def _release_lock(self) -> None:
        """Release the lock file on Unix."""
        try:
            if hasattr(self, '_lock_fd') and self._lock_fd:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
        except Exception:
            pass

    def run(self) -> None:
        """Run the desktop application."""
        print("=" * 50)
        print("  Auctus Agent Desktop")
        print("=" * 50)
        print()

        # Check single instance
        if not self.check_single_instance():
            print("[Desktop] Exiting - another instance is running.")
            sys.exit(1)

        # Register shutdown handler
        atexit.register(self.shutdown)
        signal.signal(signal.SIGTERM, lambda *args: self.shutdown())
        signal.signal(signal.SIGINT, lambda *args: self.shutdown())

        try:
            # Start backend
            self.start_backend()

            # Create window
            self.create_window()

            # Start webview
            print("[Desktop] Starting window...")
            webview.start(
                debug=False,
                http_server=False,
                private_mode=False
            )

        except Exception as e:
            print(f"[Desktop] Error: {e}")
            self.shutdown()
            sys.exit(1)


def main():
    """Entry point."""
    desktop = AuctusDesktop()
    desktop.run()


if __name__ == '__main__':
    main()
