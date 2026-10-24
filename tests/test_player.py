"""Player do app desktop: mesmos cuidados do player web (formato do áudio, reencaixe, vídeo comum)."""
import shutil
import subprocess
import time

import numpy as np
import pytest

from cipher_studio.core import media
from cipher_studio.core.audio import AUDIO_LABEL, AUDIO_LABEL_V3, GROUP_SIZE, HOST_RATE
from cipher_studio.core.keys import derive_key, permutation
from cipher_studio.core.pipeline import CipherSettings, ExportSettings, process_file
from cipher_studio.core.player import LivePlayer

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg não instalado")
EXPORT = ExportSettings(resolution="720p", preset="veryfast")


def _make(path, seconds, audio="anoisesrc=d={d}:c=pink:r=48000:a=0.3,lowpass=f=3000", size="320x180"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", audio.format(d=seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def _player(path, plain=False):
    player = LivePlayer(str(path), "senha", CipherSettings(), EXPORT, lambda: 640,
                        on_frame=lambda _f: None, on_done=lambda _e: None, plain=plain)
    player._src = media.local_streams(str(path))
    if not plain:
        key = derive_key("senha")
        _, player._inv_v2 = permutation(GROUP_SIZE, key, AUDIO_LABEL)
        _, player._inv_v3 = permutation(GROUP_SIZE, key, AUDIO_LABEL_V3)
    player.buffer_seconds = 1e9
    return player


def _audio_from(player, start):
    """Roda só o produtor de áudio de um trecho e devolve o som entregue (48 kHz, canal esquerdo)."""
    player._gen += 1
    player._audio_chunks.clear()
    player._audio_until = start
    audio_start, lead = player._audio_window(start)
    reader = media.AudioReader(player._src.audio, HOST_RATE, audio_start - lead, player._src.headers)
    try:
        player._audio_producer(reader, player._gen, start, audio_start, lead)
    finally:
        reader.close()
    assert player._producer_error is None
    return np.concatenate([c for _, c in player._audio_chunks])[:, 0]


def _pcm(path):
    reader = media.AudioReader(str(path), HOST_RATE)
    parts = []
    while (part := reader.read(HOST_RATE)) is not None:
        parts.append(part)
    reader.close()
    return np.concatenate(parts)[:, 0]


def _corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)


@pytest.fixture(scope="module")
def encoded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("player")
    src, enc = tmp / "src.mp4", tmp / "enc.mp4"
    _make(src, 20)
    process_file("encode", str(src), str(enc), "senha", CipherSettings(), EXPORT)
    return tmp, src, enc


def test_seek_into_silence_keeps_audio_scheme(encoded):
    """Um salto que cai num bloco de silêncio não pode trocar o formato do áudio (v3 lido como v2)."""
    tmp, src, enc = encoded
    quiet = tmp / "quiet.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(enc), "-c:v", "copy",
                    "-af", "volume=0:enable='between(t,10,15)'", "-c:a", "aac", "-b:a", "256k", str(quiet)],
                   check=True)
    player = _player(quiet)
    _audio_from(player, 0)                        # o trecho do início detecta o formato
    assert player._audio_scheme == "v3"
    after = _audio_from(player, 10.0)[6 * HOST_RATE:9 * HOST_RATE]     # 16–19 s, depois do silêncio
    assert _corr(after, _pcm(src)[16 * HOST_RATE:19 * HOST_RATE]) > 0.8


def test_seek_realigns_audio_blocks(encoded):
    """Num salto o FFmpeg pode entregar o Opus alguns ms fora do ponto pedido; o player reencaixa."""
    tmp, src, enc = encoded
    opus = tmp / "opus.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(enc), "-c:v", "copy",
                    "-c:a", "libopus", "-b:a", "160k", str(opus)], check=True)
    ref = _pcm(src)
    player = _player(opus)

    def similarity(start):
        got = _audio_from(player, start)[HOST_RATE:6 * HOST_RATE]
        at = round((start + 1) * HOST_RATE)
        return _corr(got, ref[at:at + len(got)])

    baseline = similarity(0)
    assert baseline > 0.9
    assert similarity(12.0) > baseline - 0.03


def test_plain_video_passes_through(tmp_path):
    src = tmp_path / "comum.mp4"
    _make(src, 4, audio="sine=frequency=440:duration={d}", size="640x360")
    player = _player(src, plain=True)
    assert player.cipher.scramble_audio is False and player.export.deblock is False
    assert _corr(_audio_from(player, 0)[:3 * HOST_RATE], _pcm(src)[:3 * HOST_RATE]) > 0.99

    frames = []
    done = []
    player = LivePlayer(str(src), "", CipherSettings(), EXPORT, lambda: 1920,
                        on_frame=frames.append, on_done=done.append, plain=True)
    player._open_audio_output = lambda: None     # sem placa de som: relógio de parede
    player.start()
    deadline = time.monotonic() + 20
    while not done and time.monotonic() < deadline:
        time.sleep(0.05)
        if frames:
            player.stop()
    assert frames and frames[0].size == (640, 360)   # sem ampliar além do original
    assert done == [None]
