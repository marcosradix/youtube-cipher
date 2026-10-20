import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cipher_studio.core import ytproxy

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg não instalado")


class FakeGooglevideo(BaseHTTPRequestHandler):
    """Como o googlevideo em servidores: só aceita pedaços via `range=` e às vezes falha."""

    data = b""
    calls = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        cls = type(self)
        cls.calls += 1
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "range" not in query or cls.calls % 5 == 0:
            self.send_error(503)
            return
        start, end = map(int, query["range"][0].split("-"))
        if end - start + 1 > ytproxy.CHUNK:
            self.send_error(403)
            return
        body = cls.data[start:end + 1]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # Como o googlevideo real: às vezes corta a resposta no meio.
        self.wfile.write(body[:20_000] if cls.calls % 3 == 0 else body)
        self.close_connection = True


@pytest.fixture()
def upstream(monkeypatch):
    monkeypatch.setattr(ytproxy, "CHUNK", 64 * 1024)
    monkeypatch.setattr(ytproxy.time, "sleep", lambda s: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGooglevideo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _url(server, size):
    return f"http://127.0.0.1:{server.server_address[1]}/videoplayback?id=1&clen={size}"


def test_proxy_serves_ranges_in_chunks(upstream):
    FakeGooglevideo.data = bytes(range(256)) * 1000
    local = ytproxy.proxied(_url(upstream, len(FakeGooglevideo.data)), {"User-Agent": "x"})
    assert local.startswith("http://127.0.0.1:")
    assert urllib.request.urlopen(local).read() == FakeGooglevideo.data
    req = urllib.request.Request(local, headers={"Range": "bytes=100000-"})
    with urllib.request.urlopen(req) as res:
        assert res.status == 206
        assert res.headers["Content-Range"] == f"bytes 100000-255999/{len(FakeGooglevideo.data)}"
        assert res.read() == FakeGooglevideo.data[100000:]


@needs_ffmpeg
def test_ffmpeg_reads_and_seeks_through_proxy(upstream, tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=320x180:rate=30:duration=4", "-c:v", "libx264", str(src)], check=True)
    FakeGooglevideo.data = src.read_bytes()
    local = ytproxy.proxied(_url(upstream, len(FakeGooglevideo.data)))
    for start in ("0", "3"):
        out = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", start, "-i", local,
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True)
        assert len(out.stdout) >= 320 * 180 * 3 * 25
