"""Player ao vivo: decodifica vídeo e áudio em memória, sem gravar nada no disco.

Arquitetura por sessão (cada salto fora do trecho carregado abre uma nova):

    FFmpeg (vídeo) -> produtor: desembaralha, suaviza, comprime em JPEG -> buffer de quadros
    FFmpeg (áudio) -> produtor: 48->24 kHz, desembaralha blocos de 5 s   -> buffer de áudio
    consumidor: exibe cada quadro quando o relógio (áudio) chega ao seu instante

Os produtores trabalham à frente da reprodução — inclusive com o vídeo pausado —
até encher o buffer configurado. Se o buffer esvaziar, áudio e vídeo param
juntos e só retomam com alguns segundos carregados (sem tocar aos trancos).
Saltos para dentro do trecho já carregado são instantâneos.
"""
import io
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
from PIL import Image

from . import media
from .audio import AUDIO_LABEL, AUDIO_RATE, BLOCK_SAMPLES, CHANNELS, GROUP_SIZE, HOST_RATE, BlockAssembler, \
    BoundaryDeclicker, HalfbandResampler, transform_audio_block
from .keys import derive_key, permutation
from .pipeline import CipherSettings, ExportSettings
from .video import DecodeChecker, deblock, decode_geometry, transform_frame

BLOCK_SECONDS = BLOCK_SAMPLES / AUDIO_RATE
START_BUFFER = 1.0       # segundos carregados para começar a tocar
REBUFFER = 3.0           # segundos carregados para retomar após o buffer esvaziar
MAX_BUFFER_BYTES = 800 * 1024 * 1024
JPEG_QUALITY = 90
FADE_SECONDS = 0.01      # rampa de volume em pausas, buffer vazio e saltos


@dataclass
class PlayerState:
    position: float = 0.0
    duration: float = 0.0
    buffered: float = 0.0    # instante (s) até onde áudio e vídeo já estão carregados
    paused: bool = False
    buffering: bool = True
    ended: bool = False
    status: str = ""


class PlaybackClock:
    """Avança pelas amostras de áudio tocadas; sem áudio (ou quando ele acaba), pelo relógio de parede."""

    def __init__(self, sample_rate: int | None):
        self._rate = sample_rate
        self.latency = 0.0
        self._lock = threading.Lock()
        self._paused = False
        self.reset(0.0, 0)

    def reset(self, base: float, gen: int) -> None:
        with self._lock:
            self._gen = gen
            self._base = base
            self._samples = 0
            self._wall = self._rate is None
            self._wall_offset = base
            self._wall_start: float | None = None
            self._paused_at: float | None = None

    def add_samples(self, n: int, gen: int) -> None:
        with self._lock:
            if gen == self._gen and not self._wall:
                self._samples += n

    def _audio_time(self) -> float:
        return self._base + max(0.0, self._samples / self._rate - self.latency)

    def switch_to_wall(self, gen: int) -> None:
        """O áudio acabou antes do vídeo: segue pelo relógio de parede a partir daqui."""
        with self._lock:
            if gen == self._gen and not self._wall:
                self._wall_offset = self._audio_time()
                self._wall = True
                self._wall_start = time.perf_counter()
                self._paused_at = self._wall_start if self._paused else None

    def kick(self) -> None:
        """No modo parede, o relógio só começa a andar quando o primeiro quadro aparece."""
        with self._lock:
            if self._wall and self._wall_start is None:
                self._wall_start = time.perf_counter()
                self._paused_at = self._wall_start if self._paused else None

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            if self._wall_start is None:
                return
            now = time.perf_counter()
            if paused and self._paused_at is None:
                self._paused_at = now
            elif not paused and self._paused_at is not None:
                self._wall_start += now - self._paused_at
                self._paused_at = None

    def time(self) -> float:
        with self._lock:
            if not self._wall:
                return self._audio_time()
            if self._wall_start is None:
                return self._wall_offset
            now = self._paused_at if self._paused_at is not None else time.perf_counter()
            return self._wall_offset + (now - self._wall_start)


class LivePlayer:
    def __init__(self, source: str, password: str, cipher: CipherSettings, export: ExportSettings,
                 display_width: Callable[[], int],
                 on_frame: Callable[[Image.Image], None],
                 on_done: Callable[[Exception | None], None]):
        self.source = source
        self.password = password
        self.cipher = cipher
        self.export = export
        self.display_width = display_width
        self.on_frame = on_frame
        self.on_done = on_done
        self.volume = 1.0
        self.buffer_seconds = float(export.buffer_seconds)

        self._state = PlayerState()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._user_paused = False
        self._stalled = True
        self._hold = True            # pausa efetiva (usuário ou buffer vazio): o callback de áudio fica em silêncio
        self._wake = threading.Event()
        self._seek_lock = threading.Lock()
        self._seek_req: float | None = None
        self._seg_readers: list[media._FFmpegReader] = []
        self._reopen_marks: list[float] = []
        self._gen = 0
        self._epoch = 0              # muda a cada salto dentro do buffer

        # Buffers da sessão atual.
        self._vlock = threading.Lock()
        self._frames: deque[tuple[float, bytes]] = deque()
        self._frames_bytes = 0
        self._video_until = 0.0
        self._video_eof = False
        self._alock = threading.Lock()
        self._audio_chunks: deque[tuple[float, np.ndarray]] = deque()
        self._current: np.ndarray | None = None
        self._gain = 0.0             # ganho da rampa de entrada/saída do áudio
        self._current_t0 = 0.0
        self._current_pos = 0
        self._audio_until = 0.0
        self._resume_threshold = START_BUFFER
        self._audio_eof = True
        self._producer_error: Exception | None = None

        self._clock = PlaybackClock(None)
        self._stream = None
        self._out_rate = AUDIO_RATE
        self._upsample = False
        self._verdict: bool | None = None
        self._video_maps: dict[str, np.ndarray] = {}

    # ---------------- controle (chamado pela interface) ----------------

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._interrupt_readers()

    def snapshot(self) -> PlayerState:
        with self._state_lock:
            return replace(self._state)

    def toggle_pause(self) -> bool:
        if self.snapshot().ended:
            self._user_paused = False
            self._apply_hold()
            self.seek(0)
            return False
        self._user_paused = not self._user_paused
        self._apply_hold()
        return self._user_paused

    def seek(self, seconds: float) -> None:
        duration = self.snapshot().duration
        if duration > 0:
            seconds = min(seconds, max(0.0, duration - 0.5))
        seconds = max(0.0, seconds)
        if self._seek_in_buffer(seconds):
            return
        # Captura os leitores da sessão atual junto com o pedido: se o player já tiver
        # aberto a sessão nova quando a interrupção acontecer, ela não pode ser atingida.
        with self._seek_lock:
            self._seek_req = seconds
            readers = list(self._seg_readers)
        self._update(position=seconds, buffering=True, ended=False, buffered=seconds)
        for reader in readers:
            reader.interrupt()
        self._wake.set()

    def _request_reopen(self, gen: int, ended_at: float) -> bool:
        """Um fluxo terminou antes do fim do vídeo (ex.: conexão caiu): reabre do ponto atual.

        Se o mesmo ponto de término já provocou uma reabertura, é o fim real daquela
        faixa (ex.: áudio original mais curto que o vídeo) e retorna False.
        """
        if any(abs(ended_at - mark) < 1.0 for mark in self._reopen_marks):
            return False
        self._reopen_marks.append(ended_at)
        with self._seek_lock:
            if gen == self._gen and self._seek_req is None and not self._stop.is_set():
                self._seek_req = self.snapshot().position
        return True

    def seek_relative(self, delta: float) -> None:
        self.seek(self.snapshot().position + delta)

    # ---------------- estado ----------------

    def _update(self, **changes) -> None:
        with self._state_lock:
            for name, value in changes.items():
                setattr(self._state, name, value)

    def _apply_hold(self) -> None:
        self._hold = self._user_paused or self._stalled
        self._clock.set_paused(self._hold)
        self._update(paused=self._user_paused, buffering=self._stalled)

    def _buffered_until(self) -> float:
        video = math.inf if self._video_eof else self._video_until
        audio = math.inf if self._audio_eof else self._audio_until
        until = min(video, audio)
        return self._state.duration if math.isinf(until) else until

    def _check_buffer(self) -> None:
        """Para tudo quando o buffer esvazia; retoma só com folga suficiente."""
        now = self._clock.time()
        ahead = self._buffered_until() - now
        finished = self._video_eof and self._audio_eof
        if self._stalled:
            if finished or ahead >= self._resume_threshold:
                self._stalled = False
                self._apply_hold()
        elif not finished and ahead <= 0.05:
            self._stalled = True
            self._resume_threshold = REBUFFER
            self._apply_hold()
        self._update(buffered=self._buffered_until())

    def _interrupt_readers(self) -> None:
        with self._seek_lock:
            readers = list(self._seg_readers)
        for reader in readers:
            reader.interrupt()

    def _seek_pending(self) -> bool:
        return self._seek_req is not None

    def _take_seek(self) -> float:
        with self._seek_lock:
            value, self._seek_req = self._seek_req, None
        return value if value is not None else 0.0

    # ---------------- salto dentro do buffer (instantâneo) ----------------

    def _seek_in_buffer(self, target: float) -> bool:
        state = self.snapshot()
        if state.ended or self._seek_pending() or not (state.position <= target <= self._buffered_until() - 0.5):
            return False
        with self._alock, self._vlock:
            while self._frames and self._frames[0][0] < target - 0.02:
                _, data = self._frames.popleft()
                self._frames_bytes -= len(data)
            if not self._frames and not self._video_eof:
                return False
            # Áudio: descarta o que vem antes do alvo e posiciona dentro do pedaço que o contém.
            chunks = deque(self._audio_chunks)
            if self._current is not None and self._current_pos < len(self._current):
                start = self._current_t0 + self._current_pos / self._out_rate
                chunks.appendleft((start, self._current[self._current_pos:]))
            while chunks and chunks[0][0] + len(chunks[0][1]) / self._out_rate <= target:
                chunks.popleft()
            if chunks:
                t0, data = chunks.popleft()
                offset = max(0, int(round((target - t0) * self._out_rate)))
                self._current, self._current_t0, self._current_pos = data[offset:], t0 + offset / self._out_rate, 0
            else:
                self._current = None
            self._audio_chunks = chunks
            self._gain = 0.0
            self._epoch += 1
            self._clock.reset(target, self._gen)
            self._clock.set_paused(self._hold)
        self._update(position=target, ended=False)
        return True

    # ---------------- execução ----------------

    def _run(self) -> None:
        error = None
        try:
            self._play()
        except Exception as exc:  # noqa: BLE001 — qualquer falha vira mensagem na interface
            if not self._stop.is_set():
                error = exc
        finally:
            self._stop.set()
            self._interrupt_readers()
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:  # noqa: BLE001
                    pass
            self.on_done(error)

    def _play(self) -> None:
        youtube = media.is_youtube_url(self.source)
        self._update(status="Derivando chave da senha...")
        self._key = derive_key(self.password)
        self._update(status="Consultando o YouTube..." if youtube else "Lendo o arquivo...")
        self._src = (media.resolve_youtube_streams(self.source, self.export.yt_quality) if youtube
                     else media.local_streams(self.source))
        self._update(duration=self._src.info.duration)
        _, self._inv_audio = permutation(GROUP_SIZE, self._key, AUDIO_LABEL)
        if self._src.audio:
            self._open_audio_output()

        start = 0.0
        while not self._stop.is_set():
            outcome = self._play_segment(start)
            if outcome == "ended":
                self._update(ended=True, buffering=False, position=self._src.info.duration or self.snapshot().position)
                while not self._stop.is_set() and not self._seek_pending():
                    self._wake.wait(0.2)
                    self._wake.clear()
            if self._stop.is_set():
                break
            start = self._take_seek()

    def _open_audio_output(self) -> None:
        import sounddevice as sd  # importado aqui: falha só se o player for usado sem PortAudio

        self._out_rate = AUDIO_RATE if self.cipher.scramble_audio else HOST_RATE
        try:
            stream = sd.OutputStream(samplerate=self._out_rate, channels=CHANNELS, dtype="float32",
                                     callback=self._audio_callback, latency="high")
        except Exception:  # noqa: BLE001 — placa de som sem suporte a 24 kHz
            if self._out_rate != AUDIO_RATE:
                raise
            self._upsample = True
            self._out_rate = HOST_RATE
            stream = sd.OutputStream(samplerate=HOST_RATE, channels=CHANNELS, dtype="float32",
                                     callback=self._audio_callback, latency="high")
        self._clock = PlaybackClock(int(stream.samplerate))
        self._clock.latency = float(stream.latency)
        self._clock.set_paused(self._hold)
        stream.start()
        self._stream = stream

    def _new_session(self, start: float) -> int:
        with self._alock, self._vlock:
            self._gen += 1
            self._frames.clear()
            self._frames_bytes = 0
            self._video_until = start
            self._video_eof = False
            self._audio_chunks.clear()
            self._current = None
            self._current_pos = 0
            self._gain = 0.0
            self._audio_until = start
            self._audio_eof = self._stream is None
            self._producer_error = None
            self._clock.reset(start, self._gen)
        self._stalled = True
        self._resume_threshold = START_BUFFER
        self._apply_hold()
        return self._gen

    def _play_segment(self, start: float) -> str:
        gen = self._new_session(start)
        info = self._src.info
        cols = decode_geometry(info.width, info.height, self.cipher.grid).cols
        # Decodifica já no tamanho da tela (blocos menores = menos trabalho por quadro).
        geom = decode_geometry(info.width, info.height, self.cipher.grid,
                               max_block=max(2, self.display_width() // cols))
        if geom.label not in self._video_maps:
            self._video_maps[geom.label] = permutation(geom.n_blocks, self._key, geom.label)[1]

        self._update(position=start, ended=False, buffered=start)
        readers: list[media._FFmpegReader] = []
        threads: list[threading.Thread] = []
        try:
            if self._stream is not None:
                scramble = self.cipher.scramble_audio
                # Só blocos inteiros de 5 s podem ser desembaralhados: começa no início do bloco.
                audio_start = math.floor(start / BLOCK_SECONDS) * BLOCK_SECONDS if scramble else start
                audio_reader = media.AudioReader(self._src.audio, HOST_RATE, audio_start, self._src.headers)
                readers.append(audio_reader)
                threads.append(threading.Thread(target=self._audio_producer,
                                                args=(audio_reader, gen, start, audio_start), daemon=True))

            vf = f"fps={info.fps},scale={geom.width}:{geom.height}:flags=area,setsar=1"
            video_reader = media.FrameReader(self._src.video, geom.width, geom.height, vf, start, self._src.headers)
            readers.append(video_reader)
            threads.append(threading.Thread(target=self._video_producer,
                                            args=(video_reader, gen, geom, start, info.fps_float), daemon=True))
            with self._seek_lock:
                self._seg_readers = readers
            for thread in threads:
                thread.start()
            if self._seek_pending() or self._stop.is_set():
                return "seek"
            return self._consume(geom, info.fps_float)
        finally:
            with self._seek_lock:
                self._seg_readers = []
            self._gen += 1  # invalida os produtores desta sessão
            for reader in readers:
                reader.interrupt()
            for thread in threads:
                thread.join(timeout=2)
            for reader in readers:
                reader.close()

    def _buffer_full(self, until: float) -> bool:
        return until - self._clock.time() > self.buffer_seconds

    def _keep_producing(self, gen: int) -> bool:
        return gen == self._gen and not self._stop.is_set() and not self._seek_pending()

    def _video_producer(self, reader: media.FrameReader, gen: int, geom, start: float, fps: float) -> None:
        checker = DecodeChecker(geom) if self._verdict is None else None
        check_every = max(1, round(fps))
        inv_map = self._video_maps[geom.label]
        idx = 0
        try:
            while self._keep_producing(gen):
                if self._buffer_full(self._video_until) or self._frames_bytes > MAX_BUFFER_BYTES:
                    time.sleep(0.02)
                    continue
                frame = reader.read()
                if frame is None:
                    if not self._keep_producing(gen):
                        return  # interrompido por salto/parada: não é o fim do vídeo
                    duration = self._src.info.duration
                    if idx == 0 and not (duration and start >= duration - 1):
                        raise media.MediaError(f"Não foi possível ler o vídeo:\n{reader.error_text()}")
                    # O áudio embaralhado é completado até o bloco de 5 s: a imagem pode
                    # legitimamente acabar até 5 s antes da duração total.
                    slack = BLOCK_SECONDS + 1 if self.cipher.scramble_audio else 1.5
                    if duration and self._video_until < duration - slack \
                            and self._request_reopen(gen, self._video_until):
                        return
                    break
                decoded = transform_frame(frame, geom, inv_map, self.cipher.invert_colors)
                if checker is not None and idx % check_every == 0:
                    checker.add(frame, decoded)
                    if checker.done:
                        self._verdict = checker.verdict()
                        checker = None
                if self.export.deblock:
                    decoded = deblock(decoded, geom)
                out = io.BytesIO()
                Image.fromarray(decoded).save(out, "JPEG", quality=JPEG_QUALITY)
                pts = start + idx / fps
                idx += 1
                with self._vlock:
                    if gen != self._gen:
                        return
                    self._frames.append((pts, out.getvalue()))
                    self._frames_bytes += out.tell()
                    self._video_until = pts + 1 / fps
            else:
                return  # sessão encerrada (salto/parada) antes do fim do arquivo
            with self._vlock:
                if gen == self._gen:
                    self._video_eof = True
        except Exception as exc:  # noqa: BLE001
            if gen == self._gen:
                self._producer_error = exc

    def _audio_producer(self, reader: media.AudioReader, gen: int, start: float, audio_start: float) -> None:
        scramble = self.cipher.scramble_audio
        down = HalfbandResampler("down") if scramble else None
        up = HalfbandResampler("up") if (scramble and self._upsample) else None
        declicker = BoundaryDeclicker() if scramble else None
        assembler = BlockAssembler()
        rate = AUDIO_RATE if scramble else HOST_RATE

        def unscramble(block: np.ndarray) -> np.ndarray:
            return declicker.process(transform_audio_block(block, self._inv_audio))
        skip = round((start - audio_start) * rate)
        t = audio_start

        def deliver(samples: np.ndarray) -> bool:
            nonlocal skip, t
            if skip:
                cut = min(skip, len(samples))
                samples, skip = samples[cut:], skip - cut
                t += cut / rate
            if not len(samples):
                return True
            if up is not None:
                samples = up.process(samples)
            samples = np.ascontiguousarray(samples, dtype=np.float32)
            while self._keep_producing(gen) and self._buffer_full(self._audio_until):
                time.sleep(0.02)
            with self._alock:
                if gen != self._gen:
                    return False
                self._audio_chunks.append((t, samples))
                t += len(samples) / self._out_rate
                self._audio_until = t
            return True

        try:
            while self._keep_producing(gen):
                chunk = reader.read(BLOCK_SAMPLES * 2 if scramble else HOST_RATE // 10)
                if chunk is None:
                    if not self._keep_producing(gen):
                        return  # interrompido por salto/parada
                    break
                if not scramble:
                    if not deliver(chunk):
                        return
                    continue
                for block in assembler.push(down.process(chunk)):
                    if not deliver(unscramble(block)):
                        return
            else:
                return  # sessão encerrada antes do fim do arquivo
            # Entrega o que ainda está retido no filtro e no último bloco parcial.
            if scramble:
                for block in assembler.push(down.flush()):
                    deliver(unscramble(block))
                last = assembler.remainder()
                if last is not None:
                    deliver(unscramble(last))
                deliver(declicker.flush())
            duration = self._src.info.duration
            if duration and self._audio_until < duration - 1.5 and self._request_reopen(gen, self._audio_until):
                return
            with self._alock:
                if gen == self._gen:
                    self._audio_eof = True
        except Exception as exc:  # noqa: BLE001
            if gen == self._gen:
                self._producer_error = exc

    def _audio_callback(self, outdata, frames, _time_info, _status) -> None:
        # Pausa, buffer vazio e saltos usam rampas de 10 ms: cortar o som no meio
        # da onda também produz um clique.
        hold = self._hold
        if hold and self._gain == 0.0:
            outdata.fill(0)
            return
        written = 0
        with self._alock:
            gen = self._gen
            while written < frames:
                if self._current is None or self._current_pos >= len(self._current):
                    if not self._audio_chunks:
                        break
                    self._current_t0, self._current = self._audio_chunks.popleft()
                    self._current_pos = 0
                take = min(frames - written, len(self._current) - self._current_pos)
                outdata[written:written + take] = self._current[self._current_pos:self._current_pos + take]
                self._current_pos += take
                written += take
            exhausted = written < frames and self._audio_eof
        outdata[written:] = 0
        step = 1.0 / (FADE_SECONDS * self._out_rate) * np.arange(1, frames + 1, dtype=np.float32)
        gains = np.maximum(0.0, self._gain - step) if hold else np.minimum(1.0, self._gain + step)
        self._gain = float(gains[-1])
        outdata *= (gains * self.volume)[:, None]
        self._clock.add_samples(written, gen)
        if exhausted:
            self._clock.switch_to_wall(gen)  # o áudio acabou antes do vídeo

    def _consume(self, geom, fps: float) -> str:
        frame_dur = 1.0 / fps
        dropped = 0
        last_status = 0.0
        last_shown_wall = 0.0

        while not self._stop.is_set():
            if self._seek_pending():
                return "seek"
            if self._producer_error is not None:
                raise self._producer_error
            self._check_buffer()
            with self._vlock:
                item = self._frames[0] if self._frames else None
                epoch = self._epoch
                if item is None and self._video_eof:
                    break
            if item is None:
                time.sleep(0.01)
                continue
            pts, data = item
            now = self._clock.time()
            if now < pts:
                time.sleep(min(0.005, pts - now))
                continue
            with self._vlock:
                if self._epoch != epoch or not self._frames:
                    continue
                self._frames.popleft()
                self._frames_bytes -= len(data)
                has_next = bool(self._frames)
            # Muito atrasado? Descarta (mas nunca congela a imagem por mais de 0,25 s).
            if has_next and now > pts + 2 * frame_dur and time.perf_counter() - last_shown_wall < 0.25:
                dropped += 1
                continue
            self.on_frame(Image.open(io.BytesIO(data)))
            self._clock.kick()
            last_shown_wall = time.perf_counter()
            self._update(position=pts)

            if last_shown_wall - last_status > 1.0:
                last_status = last_shown_wall
                verdict = {True: " · ✓ decodificação verificada",
                           False: " · ⚠ imagem ainda embaralhada: confira senha, grade e inversão de cor",
                           None: ""}[self._verdict]
                ahead = max(0.0, self._buffered_until() - pts)
                self._update(status=f"{geom.width}x{geom.height} @ {fps:.2f} fps · buffer {ahead:.0f}s · "
                                    f"descartados: {dropped}{verdict}")

        # Deixa o áudio restante terminar de tocar (no máximo alguns segundos).
        deadline = time.perf_counter() + 6
        while not self._stop.is_set() and not self._seek_pending() and time.perf_counter() < deadline:
            with self._alock:
                pending = bool(self._audio_chunks) or (
                    self._current is not None and self._current_pos < len(self._current))
            if not pending or self._hold:
                break
            time.sleep(0.05)
        return "seek" if self._seek_pending() else "ended"
