"""A minimal local fake SMTP server for tests (Issue #327).

Speaks just enough plain SMTP for `smtplib` to complete a real
conversation over a loopback socket — greeting, EHLO, AUTH PLAIN, MAIL,
RCPT, DATA, QUIT — and records what it received. No TLS (STARTTLS/SSL are
covered against injected smtplib doubles) and no external network.

Replies are configurable to exercise 4xx/5xx paths; `silent=True` accepts
the connection but never greets, to exercise the client timeout.
"""

import base64
import socketserver
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReceivedMessage:
    mail_from: str
    rcpt_to: list[str]
    data: bytes


@dataclass
class FakeSmtpConfig:
    username: Optional[str] = None
    password: Optional[str] = None
    rcpt_reply: str = "250 OK"
    data_reply: str = "250 OK queued"
    silent: bool = False
    received: list[ReceivedMessage] = field(default_factory=list)
    auth_attempts: list[tuple[str, str]] = field(default_factory=list)


class _Handler(socketserver.StreamRequestHandler):
    server: "FakeSmtpServer"

    def _send(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        config = self.server.config
        if config.silent:
            self.server.silent_connected.set()
            self.server.release.wait(30)
            return
        self._send("220 fake.test ESMTP")
        mail_from = ""
        rcpt_to: list[str] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._send("250-fake.test")
                self._send("250 AUTH PLAIN")
            elif verb == "AUTH":
                parts = line.split(" ")
                decoded = base64.b64decode(parts[2]).split(b"\0") if len(parts) > 2 else []
                user = decoded[1].decode() if len(decoded) > 2 else ""
                password = decoded[2].decode() if len(decoded) > 2 else ""
                config.auth_attempts.append((user, password))
                if (user, password) == (config.username, config.password):
                    self._send("235 2.7.0 Authentication successful")
                else:
                    self._send(f"535 5.7.8 Authentication failed for {user}:{password}")
            elif verb == "MAIL":
                mail_from = line.split(":", 1)[1].strip().strip("<>")
                rcpt_to = []
                self._send("250 OK")
            elif verb == "RCPT":
                rcpt_to.append(line.split(":", 1)[1].strip().strip("<>"))
                self._send(config.rcpt_reply)
            elif verb == "DATA":
                self._send("354 End data with <CR><LF>.<CR><LF>")
                chunks = []
                while True:
                    data_line = self.rfile.readline()
                    if data_line in (b".\r\n", b""):
                        break
                    chunks.append(data_line)
                reply = config.data_reply
                if reply.startswith("2"):
                    config.received.append(ReceivedMessage(mail_from, rcpt_to, b"".join(chunks)))
                self._send(reply)
            elif verb == "RSET":
                self._send("250 OK")
            elif verb == "QUIT":
                self._send("221 Bye")
                return
            else:
                self._send("502 Command not implemented")


class FakeSmtpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: FakeSmtpConfig) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.config = config
        self.silent_connected = threading.Event()
        self.release = threading.Event()
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def __enter__(self) -> "FakeSmtpServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=10)
