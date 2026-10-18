"""Configurações persistentes do usuário (JSON na pasta pessoal)."""
import json
from pathlib import Path

from .core.pipeline import CipherSettings, ExportSettings

CONFIG_PATH = Path.home() / ".youtube_cipher_studio.json"

DEFAULT_CONFIG = {
    "grid": "32x18",
    "invert_colors": True,
    "scramble_audio": True,
    "resolution": "1080p",
    "crf": 20,
    "preset": "medium",
    "deblock": True,
    "yt_quality": "1080p",
    "volume": 1.0,
    "timeline_style": "pill",
    "buffer_seconds": 60,
}


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **{k: v for k, v in data.items() if k in DEFAULT_CONFIG}}
    except (OSError, ValueError):
        return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8")


def to_settings(config: dict) -> tuple[CipherSettings, ExportSettings]:
    cipher = CipherSettings(config["grid"], bool(config["invert_colors"]), bool(config["scramble_audio"]))
    export = ExportSettings(config["resolution"], int(config["crf"]), config["preset"],
                            bool(config["deblock"]), config["yt_quality"],
                            int(config["buffer_seconds"]))
    return cipher, export
