"""Small first-party UI for the base MarkItDown HTML converter; runs only in its container."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urlsplit

from markitdown import MarkItDown

CONVERTER = MarkItDown(enable_plugins=False)
INDEX = Path(__file__).with_name("index.html").read_bytes()


class App(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Uploaded text never enters session logs.

    def reply(self, status, content, content_type="application/json; charset=utf-8"):
        body = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ready": True})
        elif self.path == "/" or self.path.startswith("/?"):
            self.reply(200, INDEX, "text/html; charset=utf-8")
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/convert":
            self.reply(404, {"error": "Not found"})
            return
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.reply(403, {"error": "Please use this page to convert your document"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 400_000:
                self.reply(413, {"error": "Keep the input under 200 KB"})
                return
            body = json.loads(self.rfile.read(length))
            text = body["html"]
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 200_000:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"error": "Please paste some HTML first"})
            return
        try:
            result = CONVERTER.convert_stream(BytesIO(text.encode()), file_extension=".html").text_content
            self.reply(200, {"markdown": result})
        except Exception:
            self.reply(422, {"error": "This HTML could not be converted"})


if __name__ == "__main__":
    print("MarkItDown HTML playground ready", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 3000), App).serve_forever()
