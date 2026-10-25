import shutil
import subprocess
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from cipher_studio.core.pipeline import CipherSettings, ExportSettings, process_file  # noqa: E402
from cipher_studio.web.server import create_app  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg não instalado")
CONFIG = {"grid": "32x18", "resolution": "720p", "preset": "veryfast"}


@pytest.fixture(scope="module")
def encoded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("web")
    src, enc = tmp / "src.mp4", tmp / "enc.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
                   check=True)
    process_file("encode", str(src), str(enc), "senha", CipherSettings(),
                 ExportSettings(resolution="720p", preset="veryfast"))
    return enc


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path))


def _upload(client, path):
    res = client.put(f"/api/uploads?name={path.name}", content=path.read_bytes())
    assert res.status_code == 200
    return "upload:" + res.json()["id"]


def test_info_lists_options_and_tutorial(client):
    data = client.get("/api/info").json()
    assert data["defaults"]["grid"] == "32x18"
    assert set(data["formats"]) == {"mp4", "webm"}
    assert data["tutorial"]


def test_rejects_non_youtube_urls(client):
    res = client.post("/api/sessions", json={"source": "http://169.254.169.254/x", "password": "x"})
    assert res.status_code == 400
    res = client.post("/api/jobs", json={"mode": "encode", "source": "https://youtu.be/abc", "password": "x"})
    assert res.status_code == 400


@needs_ffmpeg
@pytest.mark.parametrize("fmt,magic", [("mp4", b"ftyp"), ("webm", b"\x1a\x45\xdf\xa3")])
def test_live_stream_decodes_and_verifies(client, encoded, fmt, magic):
    source = _upload(client, encoded)
    session = client.post("/api/sessions", json={"source": source, "password": "senha", "config": CONFIG,
                                                 "display_width": 1280}).json()
    assert (session["width"], session["height"]) == (1280, 720)
    for start in (0, 5.5):    # 5,5 s: o áudio recomeça no bloco de 5 s e é cortado no instante pedido
        res = client.get(f"/api/sessions/{session['id']}/stream?start={start}&fmt={fmt}")
        assert res.status_code == 200
        assert magic in res.content[:64]
        assert len(res.content) > 10_000
    assert client.get(f"/api/sessions/{session['id']}").json()["verdict"] is True
    assert client.delete(f"/api/sessions/{session['id']}").status_code == 200
    assert client.get(f"/api/sessions/{session['id']}").status_code == 404


@needs_ffmpeg
def test_decode_job_and_download(client, encoded):
    source = _upload(client, encoded)
    job = client.post("/api/jobs", json={"mode": "decode", "source": source, "password": "senha",
                                         "output_name": "../saida", "config": CONFIG}).json()
    assert job["output_name"] == "saida.mp4"
    deadline = time.time() + 120
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.3)
        job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    assert job["result"]["decode_ok"] is True
    res = client.get(job["result"]["download"])
    assert res.status_code == 200 and b"ftyp" in res.content[:64]


COOKIES = ("# Netscape HTTP Cookie File\n"
           ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n"
           ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\tdef\n")


def test_youtube_cookies_upload_and_use(client):
    from cipher_studio.core import media

    assert client.get("/api/info").json()["youtube_cookies"]["configured"] is False
    assert client.put("/api/youtube-cookies", content=b"isto nao e um cookies.txt").status_code == 400
    res = client.put("/api/youtube-cookies", content=COOKIES.encode())
    assert res.json() == {"configured": True, "source": "upload"}
    args = media._youtube_auth_args()
    assert args[0] == "--cookies" and "SID\tabc" in open(args[1]).read()
    media._cleanup_auth_args(args)
    assert client.delete("/api/youtube-cookies").json()["configured"] is False
    assert media._youtube_auth_args() == []


def test_youtube_cookies_pasted_text_variants(client):
    """Texto colado: tabulações viram espaços, CRLF, e a exportação em JSON do Cookie-Editor."""
    import json
    from cipher_studio.core import media

    spaced = COOKIES.replace("\t", "  ").replace("\n", "\r\n")
    exported = json.dumps([{"domain": ".youtube.com", "name": "SID", "value": "abc", "path": "/",
                            "secure": True, "httpOnly": True, "expirationDate": 1893456000.5}])
    header = "Cookie: YSC=x1; SID=abc; PREF=f6=80&tz=Europe.Lisbon"
    for text in (spaced, exported, header):
        assert client.put("/api/youtube-cookies", content=text.encode()).json()["configured"] is True
        args = media._youtube_auth_args()
        saved = open(args[1]).read()
        media._cleanup_auth_args(args)
        assert ".youtube.com\tTRUE\t/\tTRUE\t" in saved and "\tSID\tabc" in saved, saved
    assert "\tPREF\tf6=80&tz=Europe.Lisbon" in saved
    assert client.put("/api/youtube-cookies", content=b'[{"foo": 1}]').status_code == 400
    client.delete("/api/youtube-cookies")


def test_youtube_cookies_from_env_base64(tmp_path, monkeypatch):
    import base64
    from cipher_studio.core import media

    monkeypatch.setenv("CIPHER_YT_COOKIES", base64.b64encode(COOKIES.encode()).decode())
    try:
        client = TestClient(create_app(tmp_path))
        assert client.get("/api/info").json()["youtube_cookies"] == {"configured": True, "source": "env"}
    finally:
        media.set_youtube_cookies(None)


@needs_ffmpeg
def test_seek_into_silence_keeps_audio_scheme(tmp_path):
    """Um salto que cai num bloco de silêncio não pode trocar o formato do áudio (v3 lido como v2)."""
    import numpy as np
    from cipher_studio.core import media
    from cipher_studio.web import live

    src, enc, quiet = tmp_path / "src.mp4", tmp_path / "enc.mp4", tmp_path / "quiet.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=20",
                    "-f", "lavfi", "-i", "anoisesrc=d=20:c=pink:r=48000:a=0.3,lowpass=f=3000",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)], check=True)
    process_file("encode", str(src), str(enc), "senha", CipherSettings(),
                 ExportSettings(resolution="720p", preset="veryfast"))
    # Silêncio digital no bloco 10–15 s do vídeo publicado (como uma pausa no YouTube).
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(enc), "-c:v", "copy",
                    "-af", "volume=0:enable='between(t,10,15)'", "-c:a", "aac", "-b:a", "256k", str(quiet)],
                   check=True)
    session = live.open_session(str(quiet), "senha", CipherSettings(),
                                ExportSettings(resolution="720p", preset="veryfast"), 640)

    rate = 20000

    def pcm(path):
        reader = media.AudioReader(str(path), rate)
        parts = []
        while (part := reader.read(rate)) is not None:
            parts.append(part)
        reader.close()
        return np.concatenate(parts)[:, 0]

    def audio_of(start):
        stream = session.open_stream(start, "webm")
        path = tmp_path / f"out_{start}.webm"
        with open(path, "wb") as out:
            while chunk := stream.read():
                out.write(chunk)
        return pcm(path)

    ref = pcm(src)
    audio_of(0)                                   # o fluxo do início detecta o formato
    assert session.audio_scheme == "v3"
    after = audio_of(10.0)[6 * rate:9 * rate]     # 16–19 s, depois do silêncio
    expected = ref[16 * rate:19 * rate]
    corr = np.dot(after, expected) / (np.linalg.norm(after) * np.linalg.norm(expected) + 1e-9)
    assert corr > 0.8, corr
    session.close()


@needs_ffmpeg
def test_seek_realigns_audio_blocks(tmp_path):
    """Num salto o FFmpeg pode entregar o Opus alguns ms fora do ponto pedido; o servidor reencaixa."""
    import numpy as np
    from cipher_studio.core import media
    from cipher_studio.web import live

    src, enc, opus = tmp_path / "src.mp4", tmp_path / "enc.mp4", tmp_path / "opus.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=20",
                    "-f", "lavfi", "-i", "anoisesrc=d=20:c=pink:r=48000:a=0.3,lowpass=f=3000",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)], check=True)
    process_file("encode", str(src), str(enc), "senha", CipherSettings(),
                 ExportSettings(resolution="720p", preset="veryfast"))
    # Áudio em Opus, como o que o player recebe do YouTube.
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(enc), "-c:v", "copy",
                    "-c:a", "libopus", "-b:a", "160k", str(opus)], check=True)
    session = live.open_session(str(opus), "senha", CipherSettings(),
                                ExportSettings(resolution="720p", preset="veryfast"), 640)
    rate = 24000

    def pcm(path):
        reader = media.AudioReader(str(path), rate)
        parts = []
        while (part := reader.read(rate)) is not None:
            parts.append(part)
        reader.close()
        return np.concatenate(parts)[:, 0]

    ref = pcm(src)

    def similarity(start):
        stream = session.open_stream(start, "webm")
        path = tmp_path / f"out_{start}.webm"
        with open(path, "wb") as out:
            while chunk := stream.read():
                out.write(chunk)
        got = pcm(path)[rate:6 * rate]
        expected = ref[round((start + 1) * rate):round((start + 1) * rate) + len(got)]
        return np.dot(got, expected) / (np.linalg.norm(got) * np.linalg.norm(expected) + 1e-9)

    baseline = similarity(0)
    assert baseline > 0.9
    assert similarity(12.0) > baseline - 0.03
    session.close()


def test_find_block_start():
    import numpy as np
    from cipher_studio.core.audio import BLOCK_SAMPLES, CORE_BLOCK, CHANNELS, encode_block_v3, find_block_start

    rng = np.random.default_rng(1)
    blocks = [encode_block_v3(rng.standard_normal((CORE_BLOCK, CHANNELS)) * 0.1, np.arange(20)) for _ in range(2)]
    stream = np.concatenate(blocks)
    for offset in (0, 37, 1200):
        padded = np.concatenate([rng.standard_normal((offset, CHANNELS)) * 0.1, stream])
        assert find_block_start(padded, 2400) == offset
    assert find_block_start(np.zeros((BLOCK_SAMPLES + 2400, CHANNELS)), 2400) is None


@needs_ffmpeg
def test_plain_video_plays_without_password(client, tmp_path):
    """Vídeo comum (não cifrado): sem senha, imagem repassada como está."""
    src = tmp_path / "comum.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    source = _upload(client, src)
    assert client.post("/api/sessions", json={"source": source, "password": ""}).status_code == 400
    session = client.post("/api/sessions", json={"source": source, "plain": True, "config": CONFIG,
                                                 "display_width": 1920}).json()
    assert session["plain"] is True
    assert (session["width"], session["height"]) == (640, 360)   # não amplia além do original
    for start in (0, 2.5):
        res = client.get(f"/api/sessions/{session['id']}/stream?start={start}&fmt=webm")
        assert res.status_code == 200
        assert len(res.content) > 10_000
    assert client.get(f"/api/sessions/{session['id']}").json()["verdict"] is None
    client.delete(f"/api/sessions/{session['id']}")


def test_tv_shortcut_login_sets_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("CIPHER_WEB_PASSWORD", "acesso")
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/info").status_code == 401
    # A página /tv e o login ficam abertos: são eles que fazem a entrada pelo atalho.
    page = client.get("/tv")
    assert page.status_code == 200 and "/api/login" in page.text
    assert client.post("/api/login", json={"password": "errada"}).status_code == 401
    assert client.get("/api/info").status_code == 401
    res = client.post("/api/login", json={"password": "acesso"})
    assert res.status_code == 200 and "httponly" in res.headers["set-cookie"].lower()
    info = client.get("/api/info")
    assert info.status_code == 200 and info.json()["access_protected"] is True
    # Cookie forjado não passa; o HTTP Basic continua funcionando.
    other = TestClient(create_app(tmp_path))
    other.cookies.set("cipher_auth", "x" * 64)
    assert other.get("/api/info").status_code == 401
    assert other.get("/api/info", auth=("u", "acesso")).status_code == 200


def test_tv_login_without_site_password(client):
    assert client.post("/api/login", json={"password": ""}).status_code == 200
    assert client.get("/api/info").json()["access_protected"] is False
