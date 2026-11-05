"""A proxy that connects nowhere, for the no-telemetry tests: it writes down where each request asked to go.

A library's own HTTP client, native code included, reaches the network through the proxy its
environment names, where Python's audit hooks never see it. Pointed here instead, each such
request is answered 403 and written down with the host it named, so a test can fail on it.
"""

from __future__ import annotations

import socket
import threading
from typing import Any
from urllib.parse import urlsplit

#: Where a process pointed here still goes directly: this computer.
THIS_COMPUTER = "127.0.0.1,localhost,::1"


def target_host(request_line: str) -> str:
    """The host a proxy request names: ``CONNECT host:443 HTTP/1.1``, or ``GET http://host/ HTTP/1.1``."""

    parts = request_line.split(" ")
    if len(parts) < 2:
        return request_line
    method, target = parts[0], parts[1]
    if method.upper() == "CONNECT":
        host = target.rsplit(":", 1)[0] if ":" in target else target
        return host.strip("[]")
    return urlsplit(target).hostname or target


class RecordingProxy:
    """A proxy on this computer's loopback address that refuses every request and keeps a note of it."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self.address = f"http://127.0.0.1:{self._server.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def environment(self) -> dict[str, str]:
        """The variables that send a process's requests here, in both spellings, and this computer's past it."""

        proxies = {name: self.address for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
        names = {**proxies, "NO_PROXY": THIS_COMPUTER}
        return {**names, **{name.lower(): value for name, value in names.items()}}

    def close(self) -> None:
        self._server.close()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(10)
            received = b""
            try:
                while b"\r\n" not in received and len(received) < 65536:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    received += chunk
            except OSError:
                pass
            line = received.split(b"\r\n", 1)[0].decode("latin-1")
            if not line:
                return  # a connection that asked for nothing reached nowhere
            with self._lock:
                self.requests.append({"event": "proxy", "request": line, "host": target_host(line)})
            try:
                connection.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
