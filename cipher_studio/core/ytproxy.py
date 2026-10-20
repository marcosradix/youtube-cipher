"""Proxy HTTP local para os fluxos do googlevideo.

O FFmpeg pede o arquivo inteiro numa única requisição (Range: bytes=N-). Fora de
conexões residenciais o googlevideo costuma recusar isso (403/5XX) e só aceita
pedaços de até ~10 MB via parâmetro `range=` — é assim que o próprio yt-dlp baixa.
Este proxy expõe cada URL como um arquivo comum em 127.0.0.1, com suporte a Range,
e busca os bytes no YouTube em pedaços, repetindo pedaços que falharem.
"""

from __future__ import annotations

import http.client
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK = 2 * 1024 * 1024
RETRIES = 6
TTL = 6 * 3600

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_entries: dict[str, tuple[str, dict[str, str], int, float]] = {}


def _content_length(url: str, headers: dict[str, str]) -> int | None:
    clen = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("clen")
    if clen and clen[0].isdigit():
        return int(clen[0])
    req = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            total = res.headers.get("Content-Range", "").rpartition("/")[2]
            return int(total) if total.isdigit() else None
    except (OSError, ValueError):
        return None


class _ClientGone(Exception):
    """O FFmpeg fechou a conexão (pulou para outro ponto ou terminou)."""


def _copy(url: str, headers: dict[str, str], start: int, end: int, out) -> int:
    """Copia os bytes [start, end] para `out` e devolve quantos foram copiados.

    O googlevideo às vezes corta a resposta no meio (IncompleteRead) ou responde 503;
    o que chegou é aproveitado e o chamador continua do ponto em que parou.
    """
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}range={start}-{end}", headers=headers)
    error: Exception | None = None
    for attempt in range(RETRIES):
        written, cut = 0, False
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                while True:
                    try:
                        buf = res.read(64 * 1024)
                    except http.client.IncompleteRead as exc:
                        buf, cut, error = exc.partial, True, exc
                    if not buf:
                        break
                    try:
                        out.write(buf)
                    except OSError as exc:
                        raise _ClientGone from exc
                    written += len(buf)
                    if cut:
                        break
        except urllib.error.HTTPError as exc:
            error = exc
            if exc.code < 500 and exc.code != 429:
                break
        except (OSError, http.client.HTTPException) as exc:
            error = exc
        if written:
            return written
        time.sleep(0.5 * 2 ** attempt)
    raise OSError(f"googlevideo recusou os bytes {start}-{end}: {error}")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _entry(self):
        with _lock:
            entry = _entries.get(self.path.strip("/"))
        if entry is None:
            self.send_error(404)
        return entry

    def _range(self, total: int) -> tuple[int, int] | None:
        value = self.headers.get("Range", "")
        if not value.startswith("bytes="):
            return None
        first, _, last = value[6:].split(",")[0].partition("-")
        start = int(first) if first.strip() else max(0, total - int(last))
        end = int(last) if first.strip() and last.strip() else total - 1
        return start, min(end, total - 1)

    def _headers(self, total: int):
        rng = self._range(total)
        start, end = rng or (0, total - 1)
        if start >= total:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        self.send_response(206 if rng else 200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        return start, end

    def do_HEAD(self):
        entry = self._entry()
        if entry:
            self._headers(entry[2])

    def do_GET(self):
        entry = self._entry()
        if not entry:
            return
        url, headers, total, _ = entry
        span = self._headers(total)
        if span is None:
            return
        pos, end = span
        try:
            while pos <= end:
                pos += _copy(url, headers, pos, min(pos + CHUNK - 1, end), self.wfile)
        except _ClientGone:
            self.close_connection = True
        except OSError as exc:
            print(f"[ytproxy] {exc}", file=sys.stderr, flush=True)
            self.close_connection = True


def _ensure_server() -> ThreadingHTTPServer:
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _server


def proxied(url: str, headers: dict[str, str] | None = None) -> str:
    """Devolve uma URL local equivalente a `url`; sem tamanho conhecido, devolve a original."""
    headers = dict(headers or {})
    total = _content_length(url, headers)
    if not total:
        return url
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        server = _ensure_server()
        for key in [k for k, e in _entries.items() if now - e[3] > TTL]:
            del _entries[key]
        _entries[token] = (url, headers, total, now)
    return f"http://127.0.0.1:{server.server_address[1]}/{token}"
