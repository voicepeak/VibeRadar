"""Runs inside a disposable Docker relay. The host holds all real credentials.

Only exact package hosts during installation and one fixed LLM endpoint are reachable.
The trial container is attached solely to an internal Docker network.
"""
import http.client
import ipaddress
import json
import os
import secrets
import select
import socket
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlencode


def public_dns_address(host):
    """Resolve proxy Fake-IP through a fixed HTTPS resolver, keeping public-only targets."""
    connection = http.client.HTTPSConnection("cloudflare-dns.com", timeout=15)
    try:
        connection.request("GET", "/dns-query?" + urlencode({"name": host, "type": "A"}),
                           headers={"Accept": "application/dns-json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Public DNS resolver failed")
        result = json.loads(response.read(65536))
        addresses = [x["data"] for x in result.get("Answer", []) if x.get("type") == 1]
        if result.get("Status") != 0 or not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
            raise ValueError("Private destination blocked")
        return addresses[0]
    finally:
        connection.close()


@lru_cache(maxsize=32)
def public_address(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(x[4][0]) for x in addresses]
    fake_net = ipaddress.ip_network("198.18.0.0/15")
    if ips and all(ip in fake_net for ip in ips):
        # A TUN proxy may synthesize benchmark-range addresses. Never connect to them.
        return public_dns_address(host)
    if not ips or any(not ip.is_global for ip in ips):
        raise ValueError("Private destination blocked")
    return str(next((ip for ip in ips if ip.version == 4), ips[0]))


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Authorization and user prompts never enter logs.

    def allowed(self, host):
        # The host switches this file atomically before starting the runtime.
        return host in json.loads(Path("/policy/hosts.json").read_text())

    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        if port != "443" or not self.allowed(host):
            self.send_error(403)
            return
        try:
            with socket.create_connection((public_address(host), 443), timeout=15) as upstream:
                self.send_response(200)
                self.end_headers()
                sockets = [self.connection, upstream]
                while True:
                    ready, _, _ = select.select(sockets, [], [], 30)
                    if not ready:
                        break
                    for src in ready:
                        data = src.recv(65536)
                        if not data:
                            return
                        (upstream if src is self.connection else self.connection).sendall(data)
        except (OSError, ValueError):
            self.close_connection = True

    def do_GET(self):
        self.send_error(403)  # Package sources must use HTTPS CONNECT.

    def reject_post(self, status):
        # Closing with unread request bytes can reset TCP on Windows and lose the
        # error response. Drain only a bounded body, with a short read deadline.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= 500_000 and not self.headers.get("Transfer-Encoding"):
            previous = self.connection.gettimeout()
            try:
                self.connection.settimeout(1)
                self.rfile.read(length)
            except OSError:
                pass
            finally:
                self.connection.settimeout(previous)
        self.send_error(status)

    def do_POST(self):
        if self.path not in ("/llm/v1/chat/completions", "/llm/v1/messages"):
            self.reject_post(403)
            return
        presented = self.headers.get("authorization", "").removeprefix("Bearer ") or self.headers.get("x-api-key", "")
        if not presented or not secrets.compare_digest(presented, os.environ["VR_VIRTUAL_KEY"]):
            self.reject_post(401)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 1 or length > 500_000:
            self.send_error(413)
            return
        # Strip all client headers and route to the fixed per-session proxy.
        protocol = "messages" if self.path.endswith("messages") else "chat/completions"
        connection = http.client.HTTPConnection("host.docker.internal", int(os.environ["VR_PORT"]), timeout=120)
        try:
            connection.request("POST", "/api/trial-proxy/" + protocol, self.rfile.read(length),
                {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["VR_VIRTUAL_KEY"]})
            response = connection.getresponse()
            body = response.read(2_000_000)
            self.send_response(response.status)
            content_type = response.getheader("Content-Type", "application/json")
            self.send_header("Content-Type", "text/event-stream" if content_type.startswith("text/event-stream") else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.send_error(502)
        finally:
            connection.close()


class AppIngress(BaseHTTPRequestHandler):
    """Loopback-published ingress to one fixed app; never a caller-selected proxy."""

    def log_message(self, *args):
        pass

    def forward(self):
        if not self.path.startswith("/") or self.path.startswith("//"):
            self.send_error(400)
            return
        host, port = os.environ["VR_APP_HOST"], int(os.environ["VR_APP_PORT"])
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.websocket(host, port)
            return
        if self.headers.get("Transfer-Encoding"):
            self.send_error(400)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > 10_000_000:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in {"connection", "transfer-encoding", "proxy-authorization", "proxy-connection"}}
        connection = http.client.HTTPConnection(host, port, timeout=120)
        began = False
        try:
            connection.request(self.command, self.path, body, headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            began = True
            if self.command != "HEAD":
                while data := response.read1(65536):
                    self.wfile.write(data)
                    self.wfile.flush()
        except OSError:
            if not began:
                self.send_error(502)
        finally:
            connection.close()

    def websocket(self, host, port):
        # Upgrade is tunneled only to this session's app, including its handshake.
        headers = [(k, v) for k, v in self.headers.items()
                   if k.lower() not in {"proxy-authorization", "proxy-connection"}]
        request = self.command + " " + self.path + " HTTP/1.1\r\n"
        request += "".join(k+": "+v+"\r\n" for k, v in headers) + "\r\n"
        try:
            with socket.create_connection((host, port), timeout=15) as upstream:
                upstream.sendall(request.encode("latin-1"))
                while True:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 120)
                    if not ready:
                        break
                    for src in ready:
                        data = src.recv(65536)
                        if not data:
                            return
                        (upstream if src is self.connection else self.connection).sendall(data)
        except OSError:
            self.close_connection = True

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward


if __name__ == "__main__":
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 8080), AppIngress).serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 3128), Relay).serve_forever()
