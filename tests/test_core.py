import shutil
import subprocess
import wave

import numpy as np
import pytest

from cipher_studio.core import media
from cipher_studio.core.audio import BLOCK_SAMPLES, GROUP_SIZE, BlockAssembler, HalfbandResampler, \
    transform_audio_block
from cipher_studio.core.keys import derive_key, permutation
from cipher_studio.core.pipeline import CipherSettings, ExportSettings, process_file
from cipher_studio.core.video import (DecodeChecker, Geometry, decode_geometry, deblock, encode_geometry,
                                      transform_frame)

KEY = derive_key("senha-de-teste")


def test_permutation_is_deterministic_and_inverse():
    perm, inv = permutation(576, KEY, "video:32x18")
    perm2, _ = permutation(576, KEY, "video:32x18")
    assert np.array_equal(perm, perm2)
    assert sorted(perm.tolist()) == list(range(576))
    assert np.array_equal(perm[inv], np.arange(576))
    # Vetor fixo: se mudar, vídeos já publicados deixariam de decodificar.
    assert perm[:8].tolist() == FIXED_PERM_PREFIX


def test_permutation_depends_on_password_and_label():
    perm, _ = permutation(576, KEY, "video:32x18")
    assert not np.array_equal(perm, permutation(576, derive_key("outra"), "video:32x18")[0])
    assert not np.array_equal(perm, permutation(576, KEY, "video:18x32")[0])


@pytest.mark.parametrize("w,h,grid,res,expected", [
    (1920, 1080, "32x18", "1080p", (1920, 1080)),
    (1280, 720, "32x18", "1080p", (1920, 1080)),
    (1080, 1920, "32x18", "1080p", (1080, 1920)),   # Shorts vertical
    (1920, 1080, "80x45", "720p", (1280, 720)),
    (1440, 1080, "64x36", "Auto", (1920, 1080)),
])
def test_encode_geometry(w, h, grid, res, expected):
    g = encode_geometry(w, h, grid, res)
    assert (g.width, g.height) == expected


@pytest.mark.parametrize("w,h", [(640, 360), (854, 480), (1280, 720), (1920, 1080), (3840, 2160)])
def test_decode_geometry_same_grid_any_resolution(w, h):
    g = decode_geometry(w, h, "32x18")
    assert (g.cols, g.rows) == (32, 18)
    assert g.width % 2 == 0 and g.height % 2 == 0


def test_frame_roundtrip():
    g = Geometry(32, 18, 10)
    perm, inv = permutation(g.n_blocks, KEY, g.label)
    frame = np.random.default_rng(1).integers(0, 256, (g.height, g.width, 3), dtype=np.uint8)
    enc = transform_frame(frame, g, perm, True)
    assert not np.array_equal(enc, frame)
    assert np.array_equal(transform_frame(enc, g, inv, True), frame)


def _natural_image(g: Geometry) -> np.ndarray:
    y, x = np.mgrid[0:g.height, 0:g.width].astype(np.float32)
    img = 128 + 60 * np.sin(x / 37) * np.cos(y / 23) + 40 * np.sin((x + y) / 11)
    img += np.random.default_rng(0).normal(0, 3, img.shape)
    return np.clip(np.stack([img, img * 0.8, 255 - img], axis=2), 0, 255).astype(np.uint8)


def test_decode_checker_detects_wrong_password():
    g = Geometry(32, 18, 20)
    perm, inv = permutation(g.n_blocks, KEY, g.label)
    _, wrong_inv = permutation(g.n_blocks, derive_key("errada"), g.label)
    enc = transform_frame(_natural_image(g), g, perm, True)
    ok, bad = DecodeChecker(g), DecodeChecker(g)
    for _ in range(6):
        ok.add(enc, transform_frame(enc, g, inv, True))
        bad.add(enc, transform_frame(enc, g, wrong_inv, True))
    assert ok.verdict() is True
    assert bad.verdict() is False


def test_deblock_only_touches_seams():
    g = Geometry(4, 3, 8)
    frame = np.random.default_rng(2).integers(0, 256, (g.height, g.width, 3), dtype=np.uint8)
    out = deblock(frame, g)
    assert np.array_equal(out[2:6, 2:6], frame[2:6, 2:6])


def test_audio_roundtrip_is_exact():
    perm, inv = permutation(GROUP_SIZE, KEY, "audio")
    block = np.random.default_rng(3).uniform(-1, 1, (BLOCK_SAMPLES, 2)).astype(np.float32)
    enc = transform_audio_block(block, perm)
    assert not np.allclose(enc, block)
    assert np.array_equal(transform_audio_block(enc, inv), block)


def test_spectral_inversion_moves_tone():
    t = np.arange(BLOCK_SAMPLES) / 24000
    tone = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    block = np.stack([tone, tone], axis=1)
    enc = transform_audio_block(block, np.arange(GROUP_SIZE))
    peak = np.argmax(np.abs(np.fft.rfft(enc[:, 0]))) * 24000 / BLOCK_SAMPLES
    assert abs(peak - 11000) < 2


@pytest.mark.parametrize("freq", [440, 11800])
def test_halfband_roundtrip_keeps_tone_aligned(freq):
    # 11,8 kHz é onde a inversão espectral coloca os graves da voz (~200 Hz): precisa sobreviver intacto.
    t = np.arange(48000) / 24000
    tone = np.stack([np.sin(2 * np.pi * freq * t)] * 2, axis=1).astype(np.float32)
    up, down = HalfbandResampler("up"), HalfbandResampler("down")
    chunks = [up.process(tone[i:i + 7000]) for i in range(0, len(tone), 7000)] + [up.flush()]
    at48 = np.concatenate(chunks)
    assert len(at48) == 2 * len(tone)
    back = np.concatenate([down.process(at48[i:i + 9000]) for i in range(0, len(at48), 9000)] + [down.flush()])
    assert len(back) == len(tone)
    middle = slice(3000, -3000)   # ignora as bordas do sinal (transitório do filtro)
    assert np.max(np.abs(back[middle] - tone[middle])) < 0.01


def test_block_assembler_pads_last_block():
    asm = BlockAssembler()
    blocks = asm.push(np.ones((BLOCK_SAMPLES + 10, 2), np.float32))
    assert len(blocks) == 1 and blocks[0].shape == (BLOCK_SAMPLES, 2)
    last = asm.remainder()
    assert last.shape == (BLOCK_SAMPLES, 2) and last[:10].all() and not last[10:].any()


# ------------------------------------------------------------------
# Ponta a ponta com FFmpeg real (inclui a recompressão H.264/AAC)
# ------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg não instalado")


def _make_source(path, size="1280x720", seconds=6):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", str(path)], check=True)


def _frame_at(path, w, h, t=2.0):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)


def _psnr(a, b):
    return 10 * np.log10(255 ** 2 / np.mean((a - b) ** 2))


def _dominant_freq(path):
    wav = path.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000",
                    str(wav)], check=True)
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    x = x[48000:48000 * 4]
    return np.argmax(np.abs(np.fft.rfft(x))) * 48000 / len(x)


@needs_ffmpeg
def test_end_to_end_encode_reencode_decode(tmp_path):
    src, enc, yt, dec = (tmp_path / n for n in ("src.mp4", "enc.mp4", "yt.mp4", "dec.mp4"))
    _make_source(src)
    cipher = CipherSettings("32x18", True, True)
    export = ExportSettings(resolution="720p", crf=16, preset="veryfast")

    process_file("encode", str(src), str(enc), "senha", cipher, export)
    info = media.probe(str(enc))
    assert (info.width, info.height) == (1280, 720)
    assert info.duration >= 6.0  # áudio completado até o bloco de 5 s, nada cortado

    # Simula o YouTube: recompressão com perdas e entrega em OUTRA resolução (480p).
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(enc), "-vf", "scale=854:480",
                    "-c:v", "libx264", "-crf", "23", "-c:a", "aac", "-b:a", "128k", str(yt)], check=True)

    result = process_file("decode", str(yt), str(dec), "senha", cipher, export)
    assert result.decode_ok is True
    g = result.geometry
    original = _frame_at(src, g.width, g.height)
    assert _psnr(_frame_at(dec, g.width, g.height), original) > 24
    assert _psnr(_frame_at(enc, g.width, g.height), original) < 12
    assert abs(_dominant_freq(dec) - 440) < 5
    assert abs(_dominant_freq(enc) - 440) > 1000

    wrong = process_file("decode", str(yt), str(tmp_path / "wrong.mp4"), "errada", cipher, export)
    assert wrong.decode_ok is False
    assert wrong.warnings


FIXED_PERM_PREFIX = [154, 107, 91, 235, 47, 39, 516, 480]


def test_playback_clock_follows_audio_and_ignores_old_sessions():
    from cipher_studio.core.player import PlaybackClock
    clock = PlaybackClock(24000)
    clock.reset(30.0, gen=2)                 # salto para 30 s
    assert clock.time() == 30.0              # sem áudio tocado, o relógio não anda (buffer)
    clock.add_samples(24000, gen=1)          # amostras de uma sessão antiga são ignoradas
    assert clock.time() == 30.0
    clock.add_samples(12000, gen=2)
    assert abs(clock.time() - 30.5) < 1e-9
    clock.switch_to_wall(gen=2)              # áudio acabou: segue pelo relógio de parede
    assert clock.time() >= 30.5


def _stream_declick(x):
    from cipher_studio.core.audio import BoundaryDeclicker
    dk = BoundaryDeclicker()
    out = np.concatenate([dk.process(x[i:i + 7000]) for i in range(0, len(x), 7000)] + [dk.flush()])
    return out, dk.repaired


def test_declicker_leaves_clean_audio_untouched():
    t = np.arange(BLOCK_SAMPLES * 2) / 24000
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    x = np.stack([tone, tone], axis=1)
    out, repaired = _stream_declick(x)
    assert len(out) == len(x) and repaired == 0
    assert np.array_equal(out, x)


def test_declicker_removes_click_only_at_slice_boundary():
    from cipher_studio.core.audio import SLICE_SAMPLES
    t = np.arange(BLOCK_SAMPLES * 2) / 24000
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    x = np.stack([tone, tone], axis=1)
    boundary, middle = SLICE_SAMPLES * 7, SLICE_SAMPLES * 9 + SLICE_SAMPLES // 2
    x[boundary] += 0.5          # clique na fronteira: deve sumir
    x[middle] += 0.5            # transiente fora da fronteira: é conteúdo, não mexe
    out, repaired = _stream_declick(x)
    assert repaired == 1
    assert abs(out[boundary, 0] - tone[boundary]) < 0.05
    assert out[middle, 0] == x[middle, 0]


def test_v3_block_roundtrip_and_detection():
    from cipher_studio.core.audio import CORE_BLOCK, decode_block_v3, detect_scheme, encode_block_v3, AUDIO_LABEL_V3
    perm, inv = permutation(GROUP_SIZE, KEY, AUDIO_LABEL_V3)
    rng = np.random.default_rng(5)
    block = rng.uniform(-0.5, 0.5, (CORE_BLOCK, 2)).astype(np.float32)
    enc = encode_block_v3(block, perm)
    assert enc.shape == (BLOCK_SAMPLES, 2)                       # mesma duração no fluxo
    assert np.array_equal(decode_block_v3(enc, inv), block)
    assert detect_scheme(enc) == "v3"
    p2, _ = permutation(GROUP_SIZE, KEY, "audio")
    assert detect_scheme(transform_audio_block(rng.uniform(-0.5, 0.5, (BLOCK_SAMPLES, 2)), p2)) == "v2"


def test_v3_cut_points_are_silent():
    # O corte entre fatias (início/fim de cada fatia no fluxo) acontece no silêncio.
    from cipher_studio.core.audio import CORE_BLOCK, SLICE_SAMPLES, encode_block_v3, AUDIO_LABEL_V3
    perm, _ = permutation(GROUP_SIZE, KEY, AUDIO_LABEL_V3)
    t = np.arange(CORE_BLOCK) / 20000
    tone = np.stack([np.sin(2 * np.pi * 300 * t)] * 2, axis=1)
    enc = encode_block_v3(tone, perm).reshape(GROUP_SIZE, SLICE_SAMPLES, 2)
    assert np.abs(enc[:, 0]).max() < 1e-6 and np.abs(enc[:, -1]).max() < 1e-3


def test_rational_resampler_20k_to_48k():
    from cipher_studio.core.audio import RationalResampler
    t = np.arange(40000) / 20000
    tone = np.stack([np.sin(2 * np.pi * 1000 * t)] * 2, axis=1).astype(np.float32)
    rs = RationalResampler(12, 5)
    out = np.concatenate([rs.process(tone[i:i + 7777]) for i in range(0, len(tone), 7777)] + [rs.flush()])
    assert abs(len(out) - 96000) <= 3
    expected = np.sin(2 * np.pi * 1000 * np.arange(len(out)) / 48000)
    assert np.max(np.abs(out[4000:-4000, 0] - expected[4000:-4000])) < 0.01


def test_stream_encoder_decoder_roundtrip():
    from cipher_studio.core.audio import AUDIO_LABEL_V3, CORE_RATE, StreamDecoder, StreamEncoder
    p3, i3 = permutation(GROUP_SIZE, KEY, AUDIO_LABEL_V3)
    _, i2 = permutation(GROUP_SIZE, KEY, "audio")
    t = np.arange(CORE_RATE * 12) / CORE_RATE                  # 12 s: dois blocos e meio
    tone = np.stack([0.4 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1).astype(np.float32)
    enc = StreamEncoder(p3)
    host = np.concatenate([enc.process(tone[i:i + 30000]) for i in range(0, len(tone), 30000)] + [enc.flush()])
    assert len(host) == 15 * 48000                              # completado até o bloco de 5 s
    dec = StreamDecoder(i2, i3)
    out = np.concatenate([dec.process(host[i:i + 50000]) for i in range(0, len(host), 50000)] + [dec.flush()])
    assert dec.scheme == "v3" and dec.rate == CORE_RATE
    middle = slice(2000, len(tone) - 2000)
    assert np.max(np.abs(out[middle] - tone[middle])) < 0.02
