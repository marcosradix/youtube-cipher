"""Entrada e saída de mídia via FFmpeg/ffprobe/yt-dlp, sempre por pipes.

Os quadros trafegam como RGB cru entre o FFmpeg e o NumPy: não há arquivo de
vídeo intermediário, então o vídeo sofre uma única compressão (H.264 final).
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from .audio import AUDIO_RATE, CHANNELS

# O Python do python.org no macOS não usa os certificados do sistema, e o yt-dlp
# falha com CERTIFICATE_VERIFY_FAILED. Aponta para o pacote do certifi (herdado
# pelos subprocessos) quando o usuário não configurou outro.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass


class MediaError(RuntimeError):
    pass


# ------------------------------------------------------------------
# Ferramentas externas
# ------------------------------------------------------------------

def _popen_flags() -> dict:
    # Evita abrir uma janela de console para cada processo no Windows.
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise MediaError("FFmpeg não encontrado no PATH. Instale-o (ex.: brew install ffmpeg / winget install ffmpeg).")
    return path


def ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise MediaError("ffprobe não encontrado no PATH (acompanha o FFmpeg).")
    return path


def ytdlp_command() -> list[str]:
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    raise MediaError("yt-dlp não encontrado. Instale com: pip install -U yt-dlp")


def check_tools() -> dict[str, bool]:
    status = {}
    for name, finder in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path), ("yt-dlp", ytdlp_command)):
        try:
            finder()
            status[name] = True
        except MediaError:
            status[name] = False
    return status


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def is_youtube_url(source: str) -> bool:
    return is_url(source) and re.search(r"(youtube\.com|youtu\.be)/", source) is not None


# ------------------------------------------------------------------
# Metadados
# ------------------------------------------------------------------

@dataclass
class MediaInfo:
    width: int
    height: int
    fps: str          # racional aceito pelo FFmpeg, ex. "30000/1001"
    duration: float   # segundos (0 se desconhecido)
    has_audio: bool

    @property
    def fps_float(self) -> float:
        return float(Fraction(self.fps))

    @property
    def estimated_frames(self) -> int:
        return max(1, int(self.duration * self.fps_float))


def _valid_rate(rate: str | None) -> str | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        return rate if Fraction(rate) > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def probe(source: str) -> MediaInfo:
    cmd = [ffprobe_path(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", source]
    result = subprocess.run(cmd, capture_output=True, text=True, **_popen_flags())
    if result.returncode != 0:
        raise MediaError(f"Não foi possível ler o vídeo de entrada:\n{result.stderr.strip()[-600:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("O arquivo não contém uma faixa de vídeo.")

    width, height = int(video["width"]), int(video["height"])
    # Vídeos de celular guardam a rotação como metadado; o FFmpeg já gira os quadros.
    rotation = 0
    for side in video.get("side_data_list", []):
        rotation = int(side.get("rotation", rotation) or 0)
    rotation = rotation or int(video.get("tags", {}).get("rotate", 0) or 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width

    fps = _valid_rate(video.get("avg_frame_rate")) or _valid_rate(video.get("r_frame_rate")) or "30"
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return MediaInfo(width, height, fps, duration, has_audio)


# ------------------------------------------------------------------
# YouTube (yt-dlp)
# ------------------------------------------------------------------

YT_QUALITY_HEIGHTS = {"Máxima": None, "2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720}


def youtube_format_args(quality: str, kind: str) -> list[str]:
    """kind: "merged" (download vídeo+áudio) ou "stream" (player: áudio m4a busca mais rápido).

    `-S res:N` usa o MENOR lado do quadro, então funciona também para Shorts verticais.
    """
    selector = {"merged": "bv*+ba/b", "stream": "bv*+ba[ext=m4a]/bv*+ba/b"}[kind]
    args = ["-f", selector]
    height = YT_QUALITY_HEIGHTS.get(quality)
    if height:
        args += ["-S", f"res:{height}"]
    return args


@dataclass
class StreamSource:
    """Entradas do FFmpeg para o player: URLs diretas permitem pular para qualquer instante (-ss)."""
    info: MediaInfo
    video: str
    audio: str | None
    headers: dict[str, str] | None = None


def resolve_youtube_streams(url: str, quality: str) -> StreamSource:
    cmd = ytdlp_command() + ["-J", "--no-playlist", "--no-warnings", *youtube_format_args(quality, "stream"), url]
    result = subprocess.run(cmd, capture_output=True, text=True, **_popen_flags())
    if result.returncode != 0:
        raise MediaError(f"yt-dlp não conseguiu acessar o vídeo:\n{result.stderr.strip()[-600:]}")
    info = json.loads(result.stdout)
    formats = info.get("requested_formats") or [info]
    video = next((f for f in formats if f.get("vcodec") not in (None, "none")), None)
    audio = next((f for f in formats if f.get("acodec") not in (None, "none")), None)
    if video is None or not video.get("width") or not video.get("height"):
        raise MediaError("O YouTube não informou um fluxo de vídeo utilizável.")
    fps = video.get("fps") or 30
    media_info = MediaInfo(int(video["width"]), int(video["height"]), str(Fraction(fps).limit_denominator(1001)),
                           float(info.get("duration") or 0), audio is not None)
    return StreamSource(media_info, video["url"], audio["url"] if audio else None, video.get("http_headers"))


def local_streams(path: str) -> StreamSource:
    info = probe(path)
    return StreamSource(info, path, path if info.has_audio else None)


def _terminate_on(event: threading.Event | None, proc: subprocess.Popen) -> None:
    if event is None:
        return

    def watch():
        while proc.poll() is None:
            if event.wait(0.2):
                proc.terminate()
                return

    threading.Thread(target=watch, daemon=True).start()


def download_youtube(url: str, quality: str, dest_dir: Path, cancel: threading.Event | None = None,
                     on_progress=None) -> Path:
    """Baixa vídeo+áudio na melhor qualidade permitida (VP9/AV1 inclusos) em .mkv."""
    cmd = ytdlp_command() + [
        "--no-playlist", "--no-warnings", "--newline",
        "--progress-template", "download:PCT %(progress._percent_str)s",
        *youtube_format_args(quality, "merged"),
        "--merge-output-format", "mkv",
        "-o", str(dest_dir / "source.%(ext)s"), url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", **_popen_flags())
    _terminate_on(cancel, proc)
    tail: list[str] = []
    for line in proc.stdout:
        match = re.search(r"PCT\s*([\d.]+)%", line)
        if match and on_progress:
            on_progress(float(match.group(1)) / 100)
        elif line.strip():
            tail = (tail + [line.strip()])[-8:]
    proc.wait()
    if cancel is not None and cancel.is_set():
        raise MediaError("Download cancelado.")
    if proc.returncode != 0:
        raise MediaError("Falha no download pelo yt-dlp:\n" + "\n".join(tail))
    files = [p for p in dest_dir.glob("source.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise MediaError("O yt-dlp terminou mas nenhum arquivo foi gerado.")
    return files[0]


# ------------------------------------------------------------------
# Leitura e escrita por pipe
# ------------------------------------------------------------------

class _FFmpegReader:
    """Base: FFmpeg lendo de arquivo ou URL, opcionalmente a partir de `start` segundos."""

    def __init__(self, source: str, output_args: list[str], start: float = 0.0,
                 headers: dict[str, str] | None = None):
        self._stderr = tempfile.TemporaryFile()
        input_args = []
        if is_url(source):
            input_args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            if headers:
                input_args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        if start > 0:
            input_args += ["-ss", f"{start:.3f}"]
        cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", *input_args, "-i", source,
               *output_args, "pipe:1"]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=self._stderr, **_popen_flags())

    def _read_exact(self, n_bytes: int) -> bytes:
        return self._proc.stdout.read(n_bytes)

    def error_text(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read().decode("utf-8", "replace").strip()[-600:]

    def interrupt(self) -> None:
        """Encerra o FFmpeg; uma leitura bloqueada em outra thread retorna vazia em seguida."""
        if self._proc.poll() is None:
            self._proc.kill()

    def close(self) -> None:
        self.interrupt()
        self._proc.wait()
        if self._proc.stdout:
            self._proc.stdout.close()
        self._stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FrameReader(_FFmpegReader):
    """Entrega quadros RGB (altura × largura × 3, uint8) já redimensionados por `vf`."""

    def __init__(self, source: str, width: int, height: int, vf: str, start: float = 0.0,
                 headers: dict[str, str] | None = None):
        self.width, self.height = width, height
        self._frame_bytes = width * height * 3
        super().__init__(source, ["-map", "0:v:0", "-an", "-sn", "-vf", vf,
                                  "-f", "rawvideo", "-pix_fmt", "rgb24"], start, headers)

    def read(self) -> np.ndarray | None:
        data = self._read_exact(self._frame_bytes)
        if len(data) < self._frame_bytes:
            return None
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)


class AudioReader(_FFmpegReader):
    """Entrega áudio float32 estéreo em blocos de até N amostras."""

    def __init__(self, source: str, rate: int = AUDIO_RATE, start: float = 0.0,
                 headers: dict[str, str] | None = None):
        self.rate = rate
        super().__init__(source, ["-map", "0:a:0", "-vn", "-ac", str(CHANNELS), "-ar", str(rate),
                                  "-f", "f32le"], start, headers)

    def read(self, n_samples: int) -> np.ndarray | None:
        frame_size = 4 * CHANNELS
        data = self._read_exact(n_samples * frame_size)
        usable = len(data) - len(data) % frame_size
        if usable == 0:
            return None
        return np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, CHANNELS)


class VideoWriter:
    """Recebe quadros RGB pelo stdin e gera o MP4 final (H.264 + AAC) em uma única passada."""

    def __init__(self, dest: str, width: int, height: int, fps: str, crf: int, preset: str,
                 audio_source: str | None = None, shortest: bool = False):
        self._stderr = tempfile.TemporaryFile()
        cmd = [ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-framerate", fps, "-i", "pipe:0"]
        if audio_source:
            cmd += ["-i", audio_source, "-map", "0:v:0", "-map", "1:a:0?"]
        cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        if audio_source:
            cmd += ["-c:a", "aac", "-b:a", "256k", "-ar", "48000"]
        if shortest:
            cmd += ["-shortest"]
        cmd += ["-movflags", "+faststart", dest]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                      stderr=self._stderr, **_popen_flags())

    def _error_text(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read().decode("utf-8", "replace").strip()[-600:]

    def write(self, frame: np.ndarray) -> None:
        try:
            self._proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            self._proc.wait()
            raise MediaError(f"O FFmpeg encerrou durante a gravação:\n{self._error_text()}")

    def finish(self) -> None:
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        code = self._proc.wait()
        error = self._error_text()
        self._stderr.close()
        if code != 0:
            raise MediaError(f"O FFmpeg falhou ao gerar o MP4:\n{error}")

    def abort(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        self._stderr.close()
