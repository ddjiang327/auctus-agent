#!/usr/bin/env python3
"""
Auctus Agent Desktop Shell - Enhanced Version
==============================================
Native desktop application with system tray, global hotkeys, and notifications.

Features:
- System tray icon with context menu
- Global hotkey to show/hide window (Cmd/Ctrl+Shift+A)
- Native system notifications
- File drag & drop support
- Minimize to tray on close

Usage:
    python -m app.desktop_enhanced
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
from typing import Optional, Callable
from queue import Queue

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
MAX_HEALTH_CHECK_ATTEMPTS = 60

# Global hotkey configuration
HOTKEY_MODIFIERS = {
    'darwin': '<cmd>+<shift>+a',
    'win32': '<ctrl>+<shift>+a',
    'linux': '<ctrl>+<shift>+a'
}


class SystemTray:
    """System tray icon manager using pystray."""
    
    def __init__(self, app: 'AuctusDesktopEnhanced'):
        self.app = app
        self.tray = None
        self._running = False
        
    def create_icon(self) -> None:
        """Create the system tray icon."""
        try:
            import pystray
            from PIL import Image, ImageDraw
            
            # Create a simple icon (16x16)
            icon_image = self._create_icon_image()
            
            # Create menu
            menu = pystray.Menu(
                pystray.MenuItem("Show Window", self._show_window, default=True),
                pystray.MenuItem("Hide Window", self._hide_window),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Status: Running", lambda: None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit_app),
            )
            
            self.tray = pystray.Icon(
                "auctus_agent",
                icon_image,
                "Auctus Agent",
                menu
            )
            
        except ImportError:
            print("[Tray] pystray not installed. System tray disabled.")
            self.tray = None
    
    def _create_icon_image(self):
        """Create a simple tray icon image."""
        from PIL import Image, ImageDraw
        
        # Create a 64x64 icon
        size = 64
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw a simple "A" letter
        draw.ellipse([4, 4, size-4, size-4], fill=(66, 133, 244, 255))
        draw.text((18, 12), "A", fill=(255, 255, 255, 255))
        
        return image
    
    def _show_window(self) -> None:
        """Show the main window."""
        if self.app.window:
            self.app.window.show()
    
    def _hide_window(self) -> None:
        """Hide the main window."""
        if self.app.window:
            self.app.window.hide()
    
    def _quit_app(self) -> None:
        """Quit the application."""
        self._running = False
        if self.tray:
            self.tray.stop()
        self.app.shutdown()
    
    def run(self) -> None:
        """Run the tray icon in a separate thread."""
        if self.tray:
            self._running = True
            self.tray.run()
    
    def stop(self) -> None:
        """Stop the tray icon."""
        if self.tray:
            self.tray.stop()
    
    def update_status(self, status: str) -> None:
        """Update the tray icon status."""
        if self.tray:
            self.tray.title = f"Auctus Agent - {status}"


class GlobalHotkey:
    """Global hotkey manager using pynput."""
    
    def __init__(self, app: 'AuctusDesktopEnhanced'):
        self.app = app
        self.listener = None
        self._running = False
        
    def start(self) -> None:
        """Start listening for global hotkeys."""
        try:
            from pynput import keyboard
            
            hotkey = HOTKEY_MODIFIERS.get(sys.platform, '<ctrl>+<shift>+a')
            
            def on_activate():
                """Called when hotkey is pressed."""
                print("[Hotkey] Global hotkey pressed!")
                if self.app.window:
                    if self.app.window.visible:
                        self.app.window.hide()
                    else:
                        self.app.window.show()
            
            # Parse hotkey string
            keys = hotkey.replace('<', '').replace('>', '').split('+')
            
            # Create hotkey listener
            self.listener = keyboard.GlobalHotKeys({
                '+'.join(keys): on_activate
            })
            
            self.listener.start()
            self._running = True
            print(f"[Hotkey] Listening for: {hotkey}")
            
        except ImportError:
            print("[Hotkey] pynput not installed. Global hotkeys disabled.")
        except Exception as e:
            print(f"[Hotkey] Failed to start: {e}")
    
    def stop(self) -> None:
        """Stop the hotkey listener."""
        if self.listener:
            self.listener.stop()
            self._running = False


class NotificationManager:
    """Native system notification manager."""
    
    def __init__(self):
        self._enabled = True
        
    def send(self, title: str, message: str, timeout: int = 5) -> None:
        """Send a system notification."""
        if not self._enabled:
            return
            
        try:
            from plyer import notification
            
            notification.notify(
                title=title,
                message=message,
                app_name="Auctus Agent",
                timeout=timeout
            )
            print(f"[Notification] {title}: {message}")
            
        except ImportError:
            print("[Notification] plyer not installed. Notifications disabled.")
        except Exception as e:
            print(f"[Notification] Failed: {e}")
    
    def send_task_complete(self, task_name: str) -> None:
        """Send a task completion notification."""
        self.send(
            "Task Complete",
            f"Your task '{task_name}' has finished processing."
        )
    
    def send_error(self, error_message: str) -> None:
        """Send an error notification."""
        self.send(
            "Error",
            error_message
        )


class AuctusDesktopEnhanced:
    """Enhanced desktop application with tray, hotkeys, and notifications."""
    
    def __init__(self):
        self.port: Optional[int] = None
        self.server_process: Optional[subprocess.Popen] = None
        self.window: Optional[webview.Window] = None
        self._shutdown_event = threading.Event()
        
        # Enhanced features
        self.tray: Optional[SystemTray] = None
        self.hotkey: Optional[GlobalHotkey] = None
        self.notifications: Optional[NotificationManager] = None
        
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
            'confirm_close': False,  # We handle close ourselves
            'text_select': True,
        }

        self.window = webview.create_window(**window_config)

        # Set up window event handlers
        self.window.events.closing += self.on_window_closing
        self.window.events.shown += self.on_window_shown
        self.window.events.minimized += self.on_window_minimized

    def on_window_closing(self) -> None:
        """Handle window close event - minimize to tray instead of closing."""
        if self.tray and self.tray.tray:
            # Minimize to tray
            print("[Desktop] Minimizing to tray...")
            self.window.hide()
            self.notifications.send(
                "Auctus Agent",
                "Running in background. Use tray icon or hotkey to restore."
            )
            return False  # Prevent actual close
        else:
            # No tray, actually close
            self.shutdown()

    def on_window_shown(self) -> None:
        """Handle window shown event."""
        if self.tray:
            self.tray.update_status("Active")

    def on_window_minimized(self) -> None:
        """Handle window minimized event."""
        if self.tray and self.tray.tray:
            self.window.hide()
            self.tray.update_status("Background")

    def init_enhanced_features(self) -> None:
        """Initialize enhanced features (tray, hotkeys, notifications)."""
        # Initialize notifications
        self.notifications = NotificationManager()
        
        # Initialize system tray
        self.tray = SystemTray(self)
        self.tray.create_icon()
        
        # Initialize global hotkeys
        self.hotkey = GlobalHotkey(self)
        
        print("[Desktop] Enhanced features initialized")

    def start_enhanced_features(self) -> None:
        """Start enhanced features in background threads."""
        # Start tray in background thread
        if self.tray and self.tray.tray:
            tray_thread = threading.Thread(target=self.tray.run, daemon=True)
            tray_thread.start()
            print("[Desktop] System tray started")
        
        # Start hotkey listener
        if self.hotkey:
            self.hotkey.start()
        
        # Send startup notification
        if self.notifications:
            self.notifications.send(
                "Auctus Agent Started",
                "Press Cmd/Ctrl+Shift+A to toggle window."
            )

    def shutdown(self) -> None:
        """Gracefully shutdown all components."""
        print("[Desktop] Shutting down...")

        # Stop enhanced features
        if self.hotkey:
            self.hotkey.stop()
        
        if self.tray:
            self.tray.stop()

        # Terminate server process
        if self.server_process and self.server_process.poll() is None:
            print("[Desktop] Stopping backend server...")
            if sys.platform == 'win32':
                self.server_process.terminate()
            else:
                self.server_process.send_signal(signal.SIGTERM)

            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("[Desktop] Force killing backend...")
                self.server_process.kill()

        print("[Desktop] Shutdown complete.")

    def check_single_instance(self) -> bool:
        """Check if another instance is already running."""
        lock_file = Path.home() / '.auctus_agent_desktop.lock'

        try:
            if sys.platform == 'win32':
                if lock_file.exists():
                    try:
                        pid = int(lock_file.read_text().strip())
                        import ctypes
                        kernel32 = ctypes.windll.kernel32
                        handle = kernel32.OpenProcess(1, False, pid)
                        if handle:
                            kernel32.CloseHandle(handle)
                            print("[Desktop] Another instance is already running.")
                            return False
                    except (ValueError, OSError):
                        pass
                    lock_file.unlink()

                lock_file.write_text(str(os.getpid()))
                atexit.register(lambda: lock_file.unlink() if lock_file.exists() else None)
                return True
            else:
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
            return True

    def _release_lock(self) -> None:
        """Release the lock file on Unix."""
        try:
            if hasattr(self, '_lock_fd') and self._lock_fd:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
        except Exception:
            pass

    def run(self) -> None:
        """Run the enhanced desktop application."""
        print("=" * 50)
        print("  Auctus Agent Desktop (Enhanced)")
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
            # Initialize enhanced features
            self.init_enhanced_features()
            
            # Start backend
            self.start_backend()

            # Create window
            self.create_window()

            # Start enhanced features
            self.start_enhanced_features()

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
    desktop = AuctusDesktopEnhanced()
    desktop.run()


if __name__ == '__main__':
    main()
