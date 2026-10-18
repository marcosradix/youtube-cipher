"""Codificação e decodificação de arquivos completos (usado pela GUI e pela CLI)."""
import os
import tempfile
import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import media
from .audio import AUDIO_LABEL, AUDIO_RATE, BLOCK_SAMPLES, CHANNELS, GROUP_SIZE, HOST_RATE, BlockAssembler, \
    HalfbandResampler, to_int16, transform_audio_block
from .keys import derive_key, permutation
from .video import DecodeChecker, Geometry, decode_geometry, deblock, encode_geometry, transform_frame


@dataclass
class CipherSettings:
    """Parâmetros da cifra — precisam ser IGUAIS no encode e no decode."""
    grid: str = "32x18"
    invert_colors: bool = True
    scramble_audio: bool = True

    def describe(self) -> str:
        cols, rows = self.grid.split("x")
        return (f"grade {cols}×{rows} · inversão de cor {'ligada' if self.invert_colors else 'desligada'}"
                f" · áudio {'embaralhado' if self.scramble_audio else 'original'}")


@dataclass
class ExportSettings:
    resolution: str = "1080p"   # preset ou "Auto"
    crf: int = 16
    preset: str = "medium"
    deblock: bool = True
    yt_quality: str = "1080p"
    buffer_seconds: int = 60    # player: quanto carregar à frente da reprodução


@dataclass
class ProcessResult:
    output: str
    geometry: Geometry
    decode_ok: bool | None = None   # só no decode: None = não foi possível avaliar
    warnings: list[str] = field(default_factory=list)


class Cancelled(Exception):
    pass


ProgressFn = Callable[[str, float | None], None]


def _noop(_text: str, _fraction: float | None = None) -> None:
    pass


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _process_audio(source: str, wav_path: Path, key: bytes, mode: str, cancel, progress: ProgressFn,
                   duration: float, span: tuple[float, float]) -> None:
    """Encode: lê a 24 kHz, embaralha e grava a 48 kHz. Decode: lê a 48 kHz, desembaralha e grava a 24 kHz.

    A conversão 24 <-> 48 kHz usa o HalfbandResampler (e não o do FFmpeg) para
    preservar os graves, que a inversão espectral coloca logo abaixo de 12 kHz.
    """
    perm, inv = permutation(GROUP_SIZE, key, AUDIO_LABEL)
    index_map = perm if mode == "encode" else inv
    encode = mode == "encode"
    in_rate, out_rate = (AUDIO_RATE, HOST_RATE) if encode else (HOST_RATE, AUDIO_RATE)
    resampler = HalfbandResampler("up" if encode else "down")
    assembler = BlockAssembler()
    done_samples = 0
    total = max(1.0, duration * in_rate)
    with media.AudioReader(source, in_rate) as reader, wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(out_rate)

        def emit(blocks):
            for block in blocks:
                out = transform_audio_block(block, index_map)
                if encode:
                    out = resampler.process(out)
                wav.writeframes(to_int16(out).tobytes())

        while True:
            _check_cancel(cancel)
            chunk = reader.read(BLOCK_SAMPLES * (1 if encode else 2))
            if chunk is None:
                break
            emit(assembler.push(chunk if encode else resampler.process(chunk)))
            done_samples += len(chunk)
            fraction = min(1.0, done_samples / total)
            progress(f"Processando áudio: {int(fraction * 100)}%", span[0] + (span[1] - span[0]) * fraction)
        if done_samples == 0:
            raise media.MediaError(f"Não foi possível extrair o áudio:\n{reader.error_text()}")
        if not encode:
            emit(assembler.push(resampler.flush()))
        # O último bloco é completado com silêncio: nada do final do áudio é descartado.
        last = assembler.remainder()
        if last is not None:
            emit([last])
        if encode:
            wav.writeframes(to_int16(resampler.flush()).tobytes())


def process_file(mode: str, source: str, output: str, password: str,
                 cipher: CipherSettings, export: ExportSettings,
                 progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> ProcessResult:
    """mode = "encode" ou "decode". `source` pode ser arquivo local ou (no decode) URL do YouTube."""
    if mode not in ("encode", "decode"):
        raise ValueError(mode)
    progress = progress or _noop
    if os.path.abspath(source) == os.path.abspath(output):
        raise media.MediaError("O arquivo de saída não pode ser o mesmo da entrada.")

    with tempfile.TemporaryDirectory(prefix="cipher_studio_") as tmp:
        tmp_dir = Path(tmp)
        start = 0.0

        if media.is_url(source):
            if mode == "encode":
                raise media.MediaError("Para codificar, selecione um arquivo de vídeo local.")
            progress("Baixando vídeo via yt-dlp...", 0.0)
            source = str(media.download_youtube(
                source, export.yt_quality, tmp_dir, cancel,
                lambda f: progress(f"Baixando vídeo via yt-dlp: {int(f * 100)}%", 0.25 * f)))
            start = 0.25

        _check_cancel(cancel)
        progress("Lendo informações do vídeo...", start)
        info = media.probe(source)

        progress("Derivando chave da senha (scrypt)...", start)
        key = derive_key(password)

        if mode == "encode":
            geom = encode_geometry(info.width, info.height, cipher.grid, export.resolution)
            # Mantém a proporção original com tarjas pretas e força taxa de quadros constante.
            vf = (f"fps={info.fps},scale={geom.width}:{geom.height}:force_original_aspect_ratio=decrease"
                  f":force_divisible_by=2:flags=lanczos,pad={geom.width}:{geom.height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
        else:
            geom = decode_geometry(info.width, info.height, cipher.grid)
            vf = f"fps={info.fps},scale={geom.width}:{geom.height}:flags=bicubic,setsar=1"

        perm, inv = permutation(geom.n_blocks, key, geom.label)
        video_map = perm if mode == "encode" else inv
        result = ProcessResult(output, geom)

        audio_source = None
        if info.has_audio:
            if cipher.scramble_audio:
                wav_path = tmp_dir / "audio.wav"
                _process_audio(source, wav_path, key, mode, cancel, progress, info.duration, (start, start + 0.1))
                audio_source = str(wav_path)
            else:
                audio_source = source
            start += 0.1

        # No encode o áudio NÃO pode ser cortado (-shortest): o último bloco de 5 s
        # embaralhado contém trechos de todo o bloco, inclusive o silêncio de preenchimento.
        writer = media.VideoWriter(output, geom.width, geom.height, info.fps, export.crf, export.preset,
                                   audio_source, shortest=(mode == "decode"))
        checker = DecodeChecker(geom) if mode == "decode" else None
        check_every = max(1, round(info.fps_float))
        total_frames = info.estimated_frames
        frame_idx = 0
        try:
            with media.FrameReader(source, geom.width, geom.height, vf) as reader:
                while True:
                    _check_cancel(cancel)
                    frame = reader.read()
                    if frame is None:
                        break
                    out = transform_frame(frame, geom, video_map, cipher.invert_colors)
                    if checker is not None:
                        if not checker.done and frame_idx % check_every == 0:
                            checker.add(frame, out)
                        if export.deblock:
                            out = deblock(out, geom)
                    writer.write(out)
                    frame_idx += 1
                    if frame_idx % 10 == 0:
                        fraction = min(1.0, frame_idx / total_frames)
                        progress(f"Processando vídeo: quadro {frame_idx} de ~{total_frames} ({int(fraction * 100)}%)",
                                 start + (0.99 - start) * fraction)
                if frame_idx == 0:
                    raise media.MediaError(f"Nenhum quadro foi lido do vídeo:\n{reader.error_text()}")
            progress("Finalizando MP4 (H.264)...", 0.99)
            writer.finish()
        except BaseException:
            writer.abort()
            if os.path.exists(output):
                os.remove(output)
            raise

        if checker is not None:
            result.decode_ok = checker.verdict()
            if result.decode_ok is False:
                result.warnings.append(
                    "A imagem decodificada ainda parece embaralhada. Confira a senha, a grade, "
                    "a inversão de cor e se este vídeo foi mesmo gerado por este programa.")
        progress("Concluído!", 1.0)
        return result
