from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = {
    "language": "en",
    "permission_scope": "full_computer",
    "terminal_access": "disabled",
    "calendar_access": "disabled",
    "hosted_region": "auto",
    "hosted_base_url": "http://120.24.223.0",
}


def json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[mock-ui] {self.address_string()} {fmt % args}")

    def send_json(self, data: object, status: int = 200) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            body = (ROOT / "app" / "ui.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/sw.js":
            self.send_response(404)
            self.end_headers()
            return
        if path == "/api/version":
            self.send_json({"name": "Auctus Agent", "version": "mock-ui", "channel": "dev"})
            return
        if path == "/api/onboarding":
            self.send_json(
                {
                    "completed": False,
                    "mode": "hosted_api",
                    "system_language": STATE["language"],
                    "system_language_configured": True,
                    "permission_scope": STATE["permission_scope"],
                    "terminal_access": STATE["terminal_access"],
                    "calendar_access": STATE["calendar_access"],
                    "hosted_region": STATE["hosted_region"],
                    "hosted_base_url": STATE["hosted_base_url"],
                    "persona": "professional",
                    "model": "deepseek/deepseek-chat",
                }
            )
            return
        if path == "/api/model":
            self.send_json(
                {
                    "model": "deepseek/deepseek-chat",
                    "available": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"],
                }
            )
            return
        if path == "/api/api-keys":
            self.send_json({"items": [], "providers": ["deepseek", "openai", "anthropic", "auctus_hosted"]})
            return
        if path == "/api/language":
            self.send_json({"language": STATE["language"], "configured": True})
            return
        if path == "/api/permission-scope":
            self.send_json({"scope": STATE["permission_scope"], "label": "Full computer", "available": []})
            return
        if path == "/api/terminal-access":
            self.send_json({"access": STATE["terminal_access"], "label": "Disabled", "available": []})
            return
        if path == "/api/calendar-access":
            self.send_json({"access": STATE["calendar_access"], "label": "Disabled", "available": []})
            return
        if path == "/api/folders":
            self.send_json({"path": str(Path.home()), "parent": None, "items": [], "truncated": False})
            return
        self.send_json({"detail": f"not found: {path}"}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        data = self.read_json()
        if path == "/api/language":
            STATE["language"] = data.get("language") or "en"
            self.send_json({"language": STATE["language"]})
            return
        if path == "/api/permission-scope":
            STATE["permission_scope"] = data.get("scope") or "full_computer"
            self.send_json({"scope": STATE["permission_scope"], "label": "Full computer"})
            return
        if path == "/api/terminal-access":
            STATE["terminal_access"] = data.get("access") or "disabled"
            self.send_json({"access": STATE["terminal_access"], "label": "Disabled"})
            return
        if path == "/api/calendar-access":
            STATE["calendar_access"] = data.get("access") or "disabled"
            self.send_json({"access": STATE["calendar_access"], "label": "Disabled"})
            return
        if path == "/api/hosted-region":
            STATE["hosted_region"] = data.get("region") or "auto"
            self.send_json({"region": STATE["hosted_region"], "label": "Auto", "state": STATE})
            return
        if path == "/api/onboarding":
            self.send_json({"completed": True, "mode": data.get("mode", "hosted_api"), "route": "proxy"})
            return
        if path == "/api/hosted-login":
            self.hosted_login(data)
            return
        self.send_json({"detail": f"not found: {path}"}, 404)

    def hosted_login(self, data: dict) -> None:
        base_url = (data.get("base_url") or "http://120.24.223.0").rstrip("/")
        email = data.get("email") or ""
        password = data.get("password") or ""
        form = urllib.parse.urlencode({"username": email, "password": password}).encode()
        try:
            login_req = urllib.request.Request(
                f"{base_url}/auth/login",
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(login_req, timeout=20) as resp:
                login = json.loads(resp.read().decode("utf-8"))
            token = login["access_token"]
            auth = {"Authorization": f"Bearer {token}"}
            key_req = urllib.request.Request(
                f"{base_url}/api-keys/",
                data=json_bytes({"name": "Auctus Agent"}),
                headers={**auth, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(key_req, timeout=20) as resp:
                key_data = json.loads(resp.read().decode("utf-8"))
            self.send_json(
                {
                    "token": token,
                    "email": email,
                    "api_key": key_data["key"],
                    "base_url": base_url,
                    "region": data.get("region") or "auto",
                    "billing": {"balance": {"amount": 0}, "subscription": None},
                }
            )
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail")
            except Exception:
                detail = str(e)
            self.send_json({"detail": detail or str(e)}, e.code)
        except Exception as e:
            self.send_json({"detail": f"{type(e).__name__}: {e}"}, 502)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8014), Handler)
    print("Mock Auctus UI: http://127.0.0.1:8014/?setup=1")
    server.serve_forever()
