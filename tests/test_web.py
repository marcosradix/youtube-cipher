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


def test_youtube_cookies_from_env_base64(tmp_path, monkeypatch):
    import base64
    from cipher_studio.core import media

    monkeypatch.setenv("CIPHER_YT_COOKIES", base64.b64encode(COOKIES.encode()).decode())
    try:
        client = TestClient(create_app(tmp_path))
        assert client.get("/api/info").json()["youtube_cookies"] == {"configured": True, "source": "env"}
    finally:
        media.set_youtube_cookies(None)
