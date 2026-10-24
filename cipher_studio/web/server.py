"""Servidor da versão web (FastAPI): mesma funcionalidade do app desktop, pelo navegador.

- /api/sessions: player ao vivo (decodifica aqui e entrega MP4/WebM fragmentado, ver live.py)
- /api/jobs: codificar/decodificar arquivos completos, com progresso e download do resultado
- /api/uploads: vídeos enviados pelo navegador (o servidor não enxerga os arquivos do usuário)

A senha trafega até o servidor, que é quem decodifica: publique sempre atrás de HTTPS.
Com CIPHER_WEB_PASSWORD definido, todo acesso pede usuário/senha (HTTP Basic).
"""
import asyncio
import base64
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..config import DEFAULT_CONFIG, to_settings
from ..core import media
from ..core.pipeline import Cancelled, process_file
from ..core.video import GRID_PRESETS, RESOLUTION_PRESETS
from ..gui.tutorial import SECTIONS
from . import live

SESSION_IDLE_SECONDS = 3 * 3600
RETENTION_SECONDS = float(os.environ.get("CIPHER_WEB_RETENTION_HOURS", "24")) * 3600
MAX_UPLOAD_BYTES = int(float(os.environ.get("CIPHER_WEB_MAX_UPLOAD_MB", "4096")) * 1024 * 1024)
DIST_DIR = Path(os.environ.get("CIPHER_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))
PRESETS = ["veryfast", "fast", "medium", "slow"]
BUFFER_CHOICES = [30, 60, 120, 300]


class ApiError(HTTPException):
    def __init__(self, status: int, message: str):
        super().__init__(status_code=status, detail=message)


# ------------------------------------------------------------------
# Estado do servidor
# ------------------------------------------------------------------

@dataclass
class Upload:
    id: str
    name: str
    path: Path
    size: int
    created: float = field(default_factory=time.time)


@dataclass
class Job:
    id: str
    mode: str
    output_name: str
    output: Path
    describe: str
    status: str = "running"          # running | done | error | cancelled
    text: str = "Aguardando início..."
    fraction: float | None = 0.0
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        return {"id": self.id, "mode": self.mode, "status": self.status, "text": self.text,
                "fraction": self.fraction, "log": self.log[-200:], "result": self.result, "error": self.error,
                "elapsed": (self.finished or time.time()) - self.started, "describe": self.describe,
                "output_name": self.output_name}


class State:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        (work_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (work_dir / "jobs").mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.uploads: dict[str, Upload] = {}
        self.jobs: dict[str, Job] = {}
        self.sessions: dict[str, live.LiveSession] = {}

    def cleanup(self) -> None:
        now_mono, now = time.monotonic(), time.time()
        with self.lock:
            idle = [s for s in self.sessions.values() if now_mono - s.last_used > SESSION_IDLE_SECONDS]
            for s in idle:
                del self.sessions[s.id]
            old_uploads = [u for u in self.uploads.values() if now - u.created > RETENTION_SECONDS]
            for u in old_uploads:
                del self.uploads[u.id]
            old_jobs = [j for j in self.jobs.values()
                        if j.finished is not None and now - j.finished > RETENTION_SECONDS]
            for j in old_jobs:
                del self.jobs[j.id]
        for s in idle:
            s.close()
        for u in old_uploads:
            u.path.unlink(missing_ok=True)
        for j in old_jobs:
            shutil.rmtree(j.output.parent, ignore_errors=True)


# ------------------------------------------------------------------
# Modelos das requisições
# ------------------------------------------------------------------

class SessionRequest(BaseModel):
    source: str                 # URL do YouTube ou "upload:<id>"
    password: str = ""
    config: dict = {}
    display_width: int = 1920
    plain: bool = False         # vídeo comum (não cifrado): dispensa a senha


class JobRequest(BaseModel):
    mode: str                   # encode | decode
    source: str
    password: str
    output_name: str = ""
    config: dict = {}


def _settings(config: dict):
    merged = {**DEFAULT_CONFIG, **{k: v for k, v in config.items() if k in DEFAULT_CONFIG}}
    if merged["grid"] not in GRID_PRESETS:
        raise ApiError(400, f"Grade desconhecida: {merged['grid']}")
    if merged["resolution"] not in [*RESOLUTION_PRESETS, "Auto"]:
        raise ApiError(400, f"Resolução desconhecida: {merged['resolution']}")
    if merged["preset"] not in PRESETS:
        raise ApiError(400, f"Velocidade desconhecida: {merged['preset']}")
    if merged["yt_quality"] not in media.YT_QUALITY_HEIGHTS:
        raise ApiError(400, f"Qualidade desconhecida: {merged['yt_quality']}")
    merged["crf"] = min(30, max(10, int(merged["crf"])))
    return to_settings(merged)


def _cookies_from_json(text: bytes) -> bytes | None:
    """Converte a exportação em JSON (extensões tipo Cookie-Editor) para o formato Netscape."""
    try:
        items = json.loads(text)
    except ValueError:
        return None
    if isinstance(items, dict):
        items = items.get("cookies")
    if not isinstance(items, list):
        return None
    lines = ["# Netscape HTTP Cookie File"]
    for item in items:
        if not isinstance(item, dict) or not item.get("domain") or not item.get("name"):
            return None
        domain = str(item["domain"])
        prefix = "#HttpOnly_" if item.get("httpOnly") else ""
        expires = item.get("expirationDate") or item.get("expires") or 0
        lines.append("\t".join([
            prefix + domain, "TRUE" if domain.startswith(".") else "FALSE", str(item.get("path") or "/"),
            "TRUE" if item.get("secure") else "FALSE", str(int(float(expires))),
            str(item["name"]), str(item.get("value", "")),
        ]))
    return "\n".join(lines).encode()


def _cookies_from_header(text: bytes) -> bytes | None:
    """Converte o cabeçalho Cookie copiado do navegador ("nome=valor; nome=valor") para o formato Netscape."""
    if text[:7].lower() == b"cookie:":
        text = text[7:]
    pairs = [part.strip().split(b"=", 1) for part in text.split(b";") if part.strip()]
    if not pairs or any(len(pair) != 2 or not pair[0] or b" " in pair[0] for pair in pairs):
        return None
    expires = str(int(time.time()) + 365 * 24 * 3600).encode()
    lines = [b"\t".join([b".youtube.com", b"TRUE", b"/", b"TRUE", expires, name.strip(), value.strip()])
             for name, value in pairs]
    return b"# Netscape HTTP Cookie File\n" + b"\n".join(lines)


def _parse_cookies(data: bytes) -> bytes:
    """Aceita cookies.txt (formato Netscape), puro ou em base64, a exportação em JSON ou o cabeçalho Cookie.

    Texto colado no navegador às vezes chega com espaços no lugar das tabulações; isso também é aceito.
    """
    text = data.strip().lstrip(b"\xef\xbb\xbf")
    if b"\t" not in text:
        try:
            text = base64.b64decode(text, validate=True).strip()
        except ValueError:
            pass
    if text[:1] in (b"[", b"{"):
        text = _cookies_from_json(text) or text
    elif b"\t" not in text and b"\n" not in text and b"=" in text:
        text = _cookies_from_header(text) or text
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or (line.startswith(b"#") and not line.startswith(b"#HttpOnly_")):
            continue
        if b"\t" not in line:
            line = b"\t".join(line.split(None, 6))
        lines.append(line)
    rows = [line.split(b"\t") for line in lines]
    if not rows or any(len(row) != 7 for row in rows) or not any(b"youtube.com" in row[0] for row in rows):
        raise ApiError(400, "Cookies inválidos: envie ou cole o cookies.txt (formato Netscape), o JSON "
                            "exportado ou o cabeçalho Cookie, com o YouTube aberto e logado.")
    return b"# Netscape HTTP Cookie File\n" + b"\n".join(lines) + b"\n"


def _safe_name(name: str, default: str) -> str:
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", Path(name or "").name).strip(" .")
    return name or default


# ------------------------------------------------------------------
# Aplicação
# ------------------------------------------------------------------

def create_app(work_dir: Path | None = None) -> FastAPI:
    state = State(work_dir or Path(tempfile.mkdtemp(prefix="cipher_web_")))
    app = FastAPI(title="YouTube Cipher Studio Web", version=__version__, docs_url=None, redoc_url=None)
    app.state.cipher = state
    access_password = os.environ.get("CIPHER_WEB_PASSWORD")
    cookies_path = state.work_dir / "youtube_cookies.txt"
    cookies_source = None
    if os.environ.get("CIPHER_YT_COOKIES"):
        # Cookies na variável de ambiente (texto ou base64): sobrevivem a reinícios e novos deploys.
        try:
            cookies_path.write_bytes(_parse_cookies(os.environ["CIPHER_YT_COOKIES"].encode()))
            cookies_path.chmod(0o600)
            media.set_youtube_cookies(str(cookies_path))
            cookies_source = "env"
        except ApiError as exc:
            print(f"Aviso: CIPHER_YT_COOKIES ignorada — {exc.detail}")

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if access_password:
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                    ok = hmac.compare_digest(given.encode(), access_password.encode())
                except (ValueError, UnicodeDecodeError):
                    ok = False
            if not ok:
                return Response("Acesso restrito.", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="YouTube Cipher Studio"'})
        return await call_next(request)

    def janitor():
        while True:
            time.sleep(600)
            state.cleanup()

    threading.Thread(target=janitor, daemon=True).start()

    def resolve_source(source: str, allow_url: bool = True) -> str:
        source = source.strip()
        if source.startswith("upload:"):
            upload = state.uploads.get(source[7:])
            if upload is None or not upload.path.exists():
                raise ApiError(404, "O arquivo enviado não está mais no servidor. Envie-o novamente.")
            return str(upload.path)
        if not allow_url:
            raise ApiError(400, "Para codificar, envie um arquivo de vídeo.")
        # Só links do YouTube: o servidor não deve abrir URLs arbitrárias.
        if not media.is_youtube_url(source):
            raise ApiError(400, "Informe um link do YouTube ou envie um arquivo de vídeo.")
        return source

    # ---------------- informações ----------------

    @app.get("/api/info")
    def info():
        return {
            "version": __version__,
            "tools": media.check_tools(),
            "defaults": DEFAULT_CONFIG,
            "grids": list(GRID_PRESETS),
            "resolutions": [*RESOLUTION_PRESETS, "Auto"],
            "presets": PRESETS,
            "yt_qualities": list(media.YT_QUALITY_HEIGHTS),
            "buffer_choices": BUFFER_CHOICES,
            "formats": {name: spec["mime"] for name, spec in live.FORMATS.items()},
            "tutorial": [{"title": title, "body": body} for title, body in SECTIONS],
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "youtube_cookies": youtube_cookies_status(),
        }

    # ---------------- cookies do YouTube ----------------

    def youtube_cookies_status() -> dict:
        configured = media.youtube_cookies_path() is not None
        return {"configured": configured, "source": cookies_source if configured else None}

    @app.put("/api/youtube-cookies")
    async def set_cookies(request: Request):
        nonlocal cookies_source
        data = await request.body()
        if len(data) > 1024 * 1024:
            raise ApiError(413, "Arquivo de cookies grande demais.")
        cookies_path.write_bytes(_parse_cookies(data))
        cookies_path.chmod(0o600)
        media.set_youtube_cookies(str(cookies_path))
        cookies_source = "upload"
        return youtube_cookies_status()

    @app.delete("/api/youtube-cookies")
    def delete_cookies():
        nonlocal cookies_source
        cookies_path.unlink(missing_ok=True)
        media.set_youtube_cookies(None)
        cookies_source = None
        return youtube_cookies_status()

    # ---------------- uploads ----------------

    @app.put("/api/uploads")
    async def upload(request: Request, name: str = "video.mp4"):
        upload_id = uuid.uuid4().hex
        safe = _safe_name(name, "video.mp4")
        path = state.work_dir / "uploads" / f"{upload_id}{Path(safe).suffix.lower()[:8]}"
        size = 0
        try:
            with open(path, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ApiError(413, f"Arquivo maior que o limite de {MAX_UPLOAD_BYTES // 2**20} MB.")
                    out.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if size == 0:
            path.unlink(missing_ok=True)
            raise ApiError(400, "Arquivo vazio.")
        item = Upload(upload_id, safe, path, size)
        with state.lock:
            state.uploads[upload_id] = item
        return {"id": upload_id, "name": safe, "size": size}

    # ---------------- player ----------------

    @app.post("/api/sessions")
    async def create_session(req: SessionRequest):
        if not req.password and not req.plain:
            raise ApiError(400, "Informe a senha secreta.")
        cipher, export = _settings(req.config)
        source = resolve_source(req.source)
        try:
            session = await asyncio.to_thread(live.open_session, source, req.password, cipher, export,
                                              req.display_width, req.plain)
        except (media.MediaError, ValueError) as exc:
            raise ApiError(400, str(exc))
        with state.lock:
            state.sessions[session.id] = session
        return session.describe()

    def get_session(session_id: str) -> live.LiveSession:
        session = state.sessions.get(session_id)
        if session is None:
            raise ApiError(404, "Sessão encerrada. Clique em ▶ Assistir novamente.")
        session.last_used = time.monotonic()
        return session

    @app.get("/api/sessions/{session_id}")
    def session_status(session_id: str):
        session = get_session(session_id)
        data = session.describe()
        stream = session._stream
        data["error"] = stream.error if stream is not None else None
        return data

    @app.get("/api/sessions/{session_id}/stream")
    async def session_stream(session_id: str, start: float = 0.0, fmt: str = "mp4"):
        session = get_session(session_id)
        if fmt not in live.FORMATS:
            raise ApiError(400, f"Formato desconhecido: {fmt}")
        try:
            stream = await asyncio.to_thread(session.open_stream, start, fmt)
        except media.MediaError as exc:
            raise ApiError(400, str(exc))

        async def body():
            try:
                while True:
                    chunk = await asyncio.to_thread(stream.read)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(stream.close)

        mime = live.FORMATS[fmt]["mime"].split(";")[0]
        return StreamingResponse(body(), media_type=mime,
                                 headers={"Cache-Control": "no-store", "X-Stream-Start": f"{stream.start_time:.3f}"})

    @app.delete("/api/sessions/{session_id}")
    @app.post("/api/sessions/{session_id}/close")       # navigator.sendBeacon ao fechar a página
    def close_session(session_id: str):
        with state.lock:
            session = state.sessions.pop(session_id, None)
        if session is not None:
            session.close()
        return {"ok": True}

    # ---------------- conversão de arquivos ----------------

    @app.post("/api/jobs")
    def create_job(req: JobRequest):
        if req.mode not in ("encode", "decode"):
            raise ApiError(400, "Operação inválida.")
        if not req.password:
            raise ApiError(400, "Informe a senha secreta.")
        cipher, export = _settings(req.config)
        source = resolve_source(req.source, allow_url=req.mode == "decode")
        default = "video_encoded.mp4" if req.mode == "encode" else "video_decoded.mp4"
        name = _safe_name(req.output_name, default)
        if not name.lower().endswith(".mp4"):
            name += ".mp4"
        job_id = uuid.uuid4().hex
        job_dir = state.work_dir / "jobs" / job_id
        job_dir.mkdir(parents=True)
        job = Job(job_id, req.mode, name, job_dir / name, cipher.describe())
        job.log.append(f"{'Codificando' if req.mode == 'encode' else 'Decodificando'}: "
                       f"{state.uploads[req.source[7:]].name if req.source.startswith('upload:') else source}")
        job.log.append(f"Parâmetros: {cipher.describe()}")
        with state.lock:
            state.jobs[job_id] = job

        def progress(text, fraction=None):
            if job.cancel.is_set():
                return
            job.text = text
            if fraction is not None:
                job.fraction = fraction

        def worker():
            try:
                result = process_file(req.mode, source, str(job.output), req.password, cipher, export,
                                      progress, job.cancel)
            except Cancelled:
                job.status, job.error = "cancelled", "Operação cancelada pelo usuário."
                job.text, job.fraction = job.error, 0.0
            except Exception as exc:  # noqa: BLE001 — qualquer falha vira mensagem na interface
                job.status, job.error = "error", str(exc)
                job.text, job.fraction = str(exc).splitlines()[0] if str(exc) else "Erro", 0.0
                job.log.append(f"Erro: {exc}")
            else:
                g = result.geometry
                job.result = {"width": g.width, "height": g.height, "block": g.block,
                              "decode_ok": result.decode_ok, "warnings": result.warnings,
                              "size": job.output.stat().st_size, "download": f"/api/jobs/{job_id}/download"}
                job.log.append(f"Concluído: {name} ({g.width}x{g.height}, blocos de {g.block} px)")
                for warning in result.warnings:
                    job.log.append(f"Aviso: {warning}")
                job.status, job.text, job.fraction = "done", "Concluído!", 1.0
            finally:
                job.finished = time.time()

        threading.Thread(target=worker, daemon=True).start()
        return job.to_dict()

    def get_job(job_id: str) -> Job:
        job = state.jobs.get(job_id)
        if job is None:
            raise ApiError(404, "Tarefa não encontrada.")
        return job

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        return get_job(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = get_job(job_id)
        job.cancel.set()
        job.text = "Cancelando operação..."
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str):
        job = get_job(job_id)
        if job.status != "done" or not job.output.exists():
            raise ApiError(404, "O arquivo ainda não está pronto.")
        return FileResponse(job.output, media_type="video/mp4", filename=job.output_name)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    # ---------------- interface (React compilado) ----------------

    if (DIST_DIR / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            target = (DIST_DIR / path).resolve()
            if path and target.is_file() and DIST_DIR.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(DIST_DIR / "index.html")
    else:
        @app.get("/")
        def no_frontend():
            return Response("Interface não compilada. Rode: cd web && npm install && npm run build",
                            media_type="text/plain; charset=utf-8")

    return app


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
