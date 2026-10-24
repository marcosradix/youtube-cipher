"""Player da versão web: decodifica no servidor e entrega um fluxo de vídeo comum ao navegador.

O navegador não consegue ler os fluxos do YouTube (CORS) nem rodar o yt-dlp, então
a decodificação acontece aqui, com o mesmo núcleo do app desktop:

    FFmpeg (vídeo) -> desembaralha, suaviza ------------------> encoder (stdin)
    FFmpeg (áudio) -> 48->20/24 kHz, desembaralha blocos de 5 s -> encoder (pipe extra)
    encoder (H.264+AAC em MP4 fragmentado, ou VP8+Opus em WebM) -> resposta HTTP

O navegador recebe os fragmentos por Media Source Extensions e controla o buffer:
quando ele para de ler, os pipes enchem e toda a cadeia espera (inclusive a leitura
do YouTube). Cada salto para fora do trecho carregado abre um fluxo novo a partir do
instante pedido; os carimbos de tempo começam em zero e o navegador os desloca.

No modo "vídeo comum" (plain) a mesma cadeia só repassa imagem e som, sem desembaralhar:
serve para assistir qualquer vídeo do YouTube no player do app, sem senha.
"""
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace

import numpy as np

from ..core import media
from ..core.audio import AUDIO_LABEL, AUDIO_LABEL_V3, BLOCK_SAMPLES, AUDIO_RATE, CHANNELS, CORE_RATE, \
    GROUP_SIZE, HOST_RATE, HalfbandResampler, RationalResampler, StreamDecoder, find_block_start
from ..core.keys import derive_key, permutation
from ..core.pipeline import CipherSettings, ExportSettings
from ..core.video import DecodeChecker, Geometry, PlainSize, deblock, decode_geometry, plain_size, transform_frame

BLOCK_SECONDS = BLOCK_SAMPLES / AUDIO_RATE
MAX_DISPLAY_WIDTH = 3840
# Depois de um salto o áudio é lido com esta folga antes do bloco, para achar o início exato dele.
ALIGN_MARGIN = 0.05

# Formatos de saída: o navegador escolhe o primeiro que o MediaSource dele aceita.
FORMATS = {
    "mp4": {
        "mime": 'video/mp4; codecs="avc1.640029,mp4a.40.2"',
        "video": ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "21",
                  "-profile:v", "high", "-pix_fmt", "yuv420p"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "mux": ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof"],
    },
    # Alternativa sem codecs proprietários (ex.: Chromium de distribuições Linux). Áudio em Vorbis:
    # o Opus do FFmpeg em WebM começa antes de zero (pré-skip) e o MSE do Chrome recusa o fluxo.
    "webm": {
        "mime": 'video/webm; codecs="vp8,vorbis"',
        "video": ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "6M",
                  "-crf", "8", "-pix_fmt", "yuv420p"],
        "audio": ["-c:a", "libvorbis", "-q:a", "6"],
        # O MSE do Chrome exige blocos em ordem crescente dentro de cada cluster, entre faixas.
        "mux": ["-f", "webm", "-live", "1", "-cluster_time_limit", "1000", "-max_interleave_delta", "0"],
    },
}


@dataclass
class LiveSession:
    """Vídeo aberto para assistir: fonte resolvida e chave derivada uma única vez."""
    id: str
    source: media.StreamSource
    key: bytes
    cipher: CipherSettings
    export: ExportSettings
    geometry: Geometry | PlainSize
    inv_video: np.ndarray
    inv_v2: np.ndarray
    inv_v3: np.ndarray
    verdict: bool | None = None
    # Formato do áudio (v2/v3), detectado no fluxo que começa em 0 s. Um salto pode cair num
    # trecho de silêncio, onde a detecção erra e decodifica tudo no formato errado (voz com eco).
    audio_scheme: str | None = None
    plain: bool = False         # vídeo comum: repassa imagem e som sem desembaralhar
    last_used: float = field(default_factory=time.monotonic)
    _stream: "LiveStream | None" = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def describe(self) -> dict:
        info, g = self.source.info, self.geometry
        return {"id": self.id, "duration": info.duration, "width": g.width, "height": g.height,
                "fps": info.fps_float, "has_audio": info.has_audio, "verdict": self.verdict,
                "source_width": info.width, "source_height": info.height, "plain": self.plain}

    def open_stream(self, start: float, fmt: str) -> "LiveStream":
        """Abre um fluxo a partir de `start`; o anterior desta sessão é encerrado (o mais novo vence)."""
        stream = LiveStream(self, start, fmt)
        with self._lock:
            previous, self._stream = self._stream, stream
        if previous is not None:
            previous.close()
        self.last_used = time.monotonic()
        stream.start()
        return stream

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()


def open_session(source: str, password: str, cipher: CipherSettings, export: ExportSettings,
                 display_width: int, plain: bool = False) -> LiveSession:
    """`source` = URL do YouTube ou caminho de um arquivo já enviado ao servidor.

    Com `plain`, o vídeo é tocado como está (sem senha nem desembaralhamento)."""
    src = (media.resolve_youtube_streams(source, export.yt_quality) if media.is_youtube_url(source)
           else media.local_streams(source))
    info = src.info
    width = max(160, min(MAX_DISPLAY_WIDTH, int(display_width)))
    if plain:
        size = plain_size(info.width, info.height, width)
        none = np.zeros(0, dtype=np.int64)
        return LiveSession(uuid.uuid4().hex, src, b"", CipherSettings(cipher.grid, False, False),
                           replace(export, deblock=False), size, none, none, none, plain=True)
    key = derive_key(password)
    cols = decode_geometry(info.width, info.height, cipher.grid).cols
    # Como no desktop: decodifica no tamanho da tela (blocos menores = menos trabalho por quadro).
    geom = decode_geometry(info.width, info.height, cipher.grid, max_block=max(2, width // cols))
    inv_video = permutation(geom.n_blocks, key, geom.label)[1]
    _, inv_v2 = permutation(GROUP_SIZE, key, AUDIO_LABEL)
    _, inv_v3 = permutation(GROUP_SIZE, key, AUDIO_LABEL_V3)
    return LiveSession(uuid.uuid4().hex, src, key, cipher, export, geom, inv_video, inv_v2, inv_v3)


class LiveStream:
    """Um fluxo codificado a partir de `start` segundos. Iterar entrega os bytes do contêiner."""

    CHUNK = 64 * 1024

    def __init__(self, session: LiveSession, start: float, fmt: str):
        if fmt not in FORMATS:
            raise ValueError(f"Formato desconhecido: {fmt}")
        self.session = session
        duration = session.source.info.duration
        self.start_time = max(0.0, min(start, duration - 0.5)) if duration > 0 else max(0.0, start)
        self.fmt = fmt
        self._closed = threading.Event()
        self._readers: list[media._FFmpegReader] = []
        self._threads: list[threading.Thread] = []
        self.error: str | None = None

        g = session.geometry
        info = session.source.info
        self._audio = info.has_audio and session.source.audio is not None
        spec = FORMATS[fmt]
        cmd = [media.ffmpeg_path(), "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{g.width}x{g.height}", "-framerate", info.fps,
               "-i", "pipe:0"]
        pass_fds: tuple[int, ...] = ()
        if self._audio:
            # O áudio entra por um segundo pipe (o FFmpeg lê "pipe:N" do descritor N).
            self._audio_r, self._audio_w = os.pipe()
            pass_fds = (self._audio_r,)
            cmd += ["-f", "f32le", "-ar", str(HOST_RATE), "-ac", str(CHANNELS), "-i", f"pipe:{self._audio_r}",
                    "-map", "0:v:0", "-map", "1:a:0"]
        gop = max(1, round(info.fps_float))   # um quadro-chave por segundo = fragmentos de ~1 s
        cmd += [*spec["video"], "-g", str(gop), "-keyint_min", str(gop)]
        if self._audio:
            cmd += [*spec["audio"], "-shortest"]
        cmd += [*spec["mux"], "pipe:1"]
        self._encoder = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, pass_fds=pass_fds)
        if self._audio:
            os.close(self._audio_r)

    # ---------------- ciclo de vida ----------------

    def start(self) -> None:
        s = self.session
        info, src = s.source.info, s.source
        if self._audio:
            scramble = s.cipher.scramble_audio
            # Só blocos inteiros de 5 s podem ser desembaralhados: começa no início do bloco.
            audio_start = math.floor(self.start_time / BLOCK_SECONDS) * BLOCK_SECONDS if scramble \
                else self.start_time
            # Num salto, o FFmpeg pode entregar o áudio alguns ms fora do ponto pedido: lê com folga
            # e reencaixa pelas margens do v3 (o fluxo a partir de 0 s não precisa).
            lead = min(ALIGN_MARGIN, audio_start) if scramble and self.start_time > 0 else 0.0
            reader = media.AudioReader(src.audio, HOST_RATE, audio_start - lead, src.headers)
            self._readers.append(reader)
            self._threads.append(threading.Thread(target=self._audio_worker, args=(reader, audio_start, lead),
                                                  daemon=True))
        g = s.geometry
        vf = f"fps={info.fps},scale={g.width}:{g.height}:flags=area,setsar=1"
        video_reader = media.FrameReader(src.video, g.width, g.height, vf, self.start_time, src.headers)
        self._readers.append(video_reader)
        self._threads.append(threading.Thread(target=self._video_worker, args=(video_reader,), daemon=True))
        for thread in self._threads:
            thread.start()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for reader in self._readers:
            reader.interrupt()
        if self._encoder.poll() is None:
            self._encoder.kill()
        for thread in self._threads:
            thread.join(timeout=2)
        for reader in self._readers:
            reader.close()
        self._encoder.wait()
        for pipe in (self._encoder.stdin, self._encoder.stdout):
            try:
                pipe.close()
            except OSError:
                pass

    def read(self) -> bytes:
        """Próximo pedaço do contêiner; vazio quando o fluxo terminou ou foi encerrado."""
        if self._closed.is_set():
            return b""
        try:
            return self._encoder.stdout.read1(self.CHUNK)
        except (OSError, ValueError):
            return b""

    # ---------------- produtores ----------------

    def _video_worker(self, reader: media.FrameReader) -> None:
        s = self.session
        g = s.geometry
        checker = DecodeChecker(g) if s.verdict is None and not s.plain else None
        check_every = max(1, round(s.source.info.fps_float))
        idx = 0
        try:
            while not self._closed.is_set():
                frame = reader.read()
                if frame is None:
                    if idx == 0 and not self._closed.is_set():
                        self.error = reader.error_text() or "Nenhum quadro foi lido do vídeo."
                    break
                if s.plain:
                    self._encoder.stdin.write(frame.tobytes())
                    idx += 1
                    continue
                decoded = transform_frame(frame, g, s.inv_video, s.cipher.invert_colors)
                if checker is not None and idx % check_every == 0:
                    checker.add(frame, decoded)
                    if checker.done:
                        s.verdict = checker.verdict()
                        checker = None
                if s.export.deblock:
                    decoded = deblock(decoded, g)
                self._encoder.stdin.write(decoded.tobytes())
                idx += 1
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                self._encoder.stdin.close()
            except (OSError, ValueError):
                pass

    def _align(self, chunk: np.ndarray, lead: float, scheme: str | None) -> np.ndarray:
        """Descarta a folga lida antes do bloco; no v3, mede pelo áudio onde o bloco começa de fato."""
        nominal = round(lead * AUDIO_RATE)
        found = None
        if scheme == "v3":
            down = HalfbandResampler("down")
            found = find_block_start(np.concatenate([down.process(chunk), down.flush()]), 2 * nominal)
            if found is not None and found != nominal:
                print(f"[áudio] salto para {self.start_time:.1f} s: reencaixado em "
                      f"{(found - nominal) * 1000 / AUDIO_RATE:+.1f} ms", file=sys.stderr, flush=True)
        return chunk[2 * (nominal if found is None else found):]

    def _audio_worker(self, reader: media.AudioReader, audio_start: float, lead: float = 0.0) -> None:
        s = self.session
        scramble = s.cipher.scramble_audio
        known = None if self.start_time == 0 else s.audio_scheme
        decoder = StreamDecoder(s.inv_v2, s.inv_v3, known) if scramble else None
        to_host = None
        skip = self.start_time - audio_start

        def deliver(samples: np.ndarray, rate: int) -> None:
            nonlocal skip, to_host
            if skip > 0 and len(samples):
                cut = min(len(samples), round(skip * rate))
                samples, skip = samples[cut:], skip - cut / rate
            if not len(samples):
                return
            if rate != HOST_RATE:
                if to_host is None:
                    to_host = (RationalResampler(HOST_RATE // 4000, rate // 4000) if rate == CORE_RATE
                               else HalfbandResampler("up"))
                samples = to_host.process(samples)
            _write_all(self._audio_w, np.ascontiguousarray(samples, dtype=np.float32).tobytes())

        try:
            while not self._closed.is_set():
                chunk = reader.read(HOST_RATE * 5 if scramble else HOST_RATE // 10)
                if chunk is None:
                    break
                if lead > 0:
                    extra = reader.read(round(2 * lead * HOST_RATE))
                    if extra is not None:
                        chunk = np.concatenate([chunk, extra])
                    chunk, lead = self._align(chunk, lead, decoder.scheme), 0.0
                if scramble:
                    deliver(decoder.process(chunk), decoder.rate)
                    if self.start_time == 0 and decoder.scheme:
                        s.audio_scheme = decoder.scheme
                else:
                    deliver(chunk, HOST_RATE)
            if scramble and not self._closed.is_set():
                deliver(decoder.flush(), decoder.rate or CORE_RATE)
                if to_host is not None:
                    _write_all(self._audio_w, to_host.flush().astype(np.float32).tobytes())
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                os.close(self._audio_w)
            except OSError:
                pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
