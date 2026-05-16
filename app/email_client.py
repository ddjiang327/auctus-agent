"""IMAP/SMTP email client: read, send, delete, and archive emails."""
from __future__ import annotations

import email as email_lib
import imaplib
import re
import smtplib
from email.header import decode_header as _raw_decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Union


def _decode_str(raw: Union[str, bytes, None]) -> str:
    if raw is None:
        return ""
    parts = _raw_decode_header(raw)
    result = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            result.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(fragment))
    return "".join(result)


def _extract_body(msg) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
        return "\n".join(parts)
    payload = msg.get_payload(decode=True)
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return ""


def _connect(account: dict) -> Union[imaplib.IMAP4_SSL, imaplib.IMAP4]:
    host = account["imap_host"]
    port = int(account.get("imap_port", 993))
    ssl = bool(account.get("imap_ssl", True))
    username = account.get("username") or account.get("email_address", "")
    password = account.get("password", "")
    conn = imaplib.IMAP4_SSL(host, port) if ssl else imaplib.IMAP4(host, port)
    conn.login(username, password)
    return conn


def _safe_logout(conn) -> None:
    try:
        conn.logout()
    except Exception:
        pass


def test_connection(account: dict) -> dict:
    try:
        conn = _connect(account)
        status, _ = conn.select("INBOX", readonly=True)
        _safe_logout(conn)
        if status == "OK":
            return {"ok": True}
        return {"ok": False, "error": f"INBOX select returned: {status}"}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"connection error: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def list_inbox(account: dict, limit: int = 20, folder: str = "INBOX") -> list[dict]:
    limit = min(max(int(limit), 1), 50)
    conn = _connect(account)
    try:
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            return []
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            return []
        uids = data[0].split()
        # most recent first
        uids = uids[-limit:][::-1]
        results = []
        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            results.append({
                "uid": uid.decode(),
                "subject": _decode_str(msg.get("Subject")),
                "from": _decode_str(msg.get("From")),
                "to": _decode_str(msg.get("To")),
                "date": msg.get("Date", ""),
            })
        return results
    finally:
        _safe_logout(conn)


def search_emails(account: dict, query: str, limit: int = 10) -> list[dict]:
    limit = min(max(int(limit), 1), 30)
    query = re.sub(r'["\r\n]', " ", query).strip()
    conn = _connect(account)
    try:
        conn.select("INBOX", readonly=True)
        # IMAP SEARCH OR: match subject OR from field
        criteria = f'OR SUBJECT "{query}" FROM "{query}"'
        status, data = conn.search(None, criteria)
        if status != "OK" or not data[0]:
            return []
        uids = data[0].split()[-limit:][::-1]
        results = []
        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            results.append({
                "uid": uid.decode(),
                "subject": _decode_str(msg.get("Subject")),
                "from": _decode_str(msg.get("From")),
                "to": _decode_str(msg.get("To")),
                "date": msg.get("Date", ""),
            })
        return results
    finally:
        _safe_logout(conn)


def get_email_content(account: dict, uid: str, max_body_chars: int = 8000) -> dict:
    conn = _connect(account)
    try:
        conn.select("INBOX", readonly=True)
        status, msg_data = conn.fetch(uid.encode(), "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            return {"error": "email not found"}
        raw = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw)
        body = _extract_body(msg)
        if len(body) > max_body_chars:
            body = body[:max_body_chars] + "\n\n[正文已截断]"
        return {
            "uid": uid,
            "subject": _decode_str(msg.get("Subject")),
            "from": _decode_str(msg.get("From")),
            "to": _decode_str(msg.get("To")),
            "date": msg.get("Date", ""),
            "body": body,
        }
    finally:
        _safe_logout(conn)


def send_email(account: dict, to: str, subject: str, body: str, cc: str = "") -> dict:
    host = account["smtp_host"]
    port = int(account.get("smtp_port", 465))
    ssl = bool(account.get("smtp_ssl", True))
    username = account.get("username") or account.get("email_address", "")
    password = account.get("password", "")
    from_addr = account.get("email_address", username)

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(body, "plain", "utf-8"))

    recipients = [a.strip() for a in to.split(",") if a.strip()]
    if cc:
        recipients += [a.strip() for a in cc.split(",") if a.strip()]

    try:
        if ssl:
            with smtplib.SMTP_SSL(host, port) as smtp:
                smtp.login(username, password)
                smtp.sendmail(from_addr, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(username, password)
                smtp.sendmail(from_addr, recipients, msg.as_string())
        return {"ok": True, "to": to, "subject": subject}
    except smtplib.SMTPException as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"connection error: {e}"}


def delete_email(account: dict, uid: str, folder: str = "INBOX") -> dict:
    conn = _connect(account)
    try:
        status, _ = conn.select(folder)
        if status != "OK":
            return {"ok": False, "error": f"cannot select folder: {folder}"}
        conn.store(uid.encode(), "+FLAGS", r"(\Deleted)")
        conn.expunge()
        return {"ok": True, "uid": uid, "action": "deleted"}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


# Archive folder names by provider (derived from smtp_host)
_ARCHIVE_FOLDERS = {
    "smtp.gmail.com": "[Gmail]/All Mail",
    "smtp.office365.com": "Archive",
    "smtp.mail.me.com": "Archive",
    "smtp.mail.yahoo.com": "Archive",
    "smtp.qq.com": "Archive",
    "smtp.163.com": "Archive",
}


def archive_email(account: dict, uid: str, archive_folder: str = "") -> dict:
    if not archive_folder:
        smtp_host = account.get("smtp_host", "")
        archive_folder = _ARCHIVE_FOLDERS.get(smtp_host, "Archive")

    conn = _connect(account)
    try:
        status, _ = conn.select("INBOX")
        if status != "OK":
            return {"ok": False, "error": "cannot select INBOX"}
        # Copy to archive folder
        status, _ = conn.copy(uid.encode(), archive_folder)
        if status != "OK":
            # Try creating the folder then copying
            conn.create(archive_folder)
            status, _ = conn.copy(uid.encode(), archive_folder)
            if status != "OK":
                return {"ok": False, "error": f"cannot copy to {archive_folder}"}
        # Delete from INBOX
        conn.store(uid.encode(), "+FLAGS", r"(\Deleted)")
        conn.expunge()
        return {"ok": True, "uid": uid, "action": "archived", "folder": archive_folder}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)
