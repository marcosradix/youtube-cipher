"""Linha de comando: python -m cipher_studio [gui|web|encode|decode] ...

A senha é lida da variável de ambiente CIPHER_PASSWORD ou pedida no terminal
(nunca como argumento, para não ficar no histórico do shell).
"""
import argparse
import getpass
import os
import sys
import threading

from .config import load_config, to_settings
from .core import media
from .core.pipeline import Cancelled, process_file
from .core.video import GRID_PRESETS, RESOLUTION_PRESETS


def _read_password(confirm: bool) -> str:
    password = os.environ.get("CIPHER_PASSWORD")
    if password:
        return password
    password = getpass.getpass("Senha secreta: ")
    if confirm and getpass.getpass("Confirme a senha: ") != password:
        sys.exit("As senhas não conferem.")
    return password


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cipher_studio", description="YouTube Cipher Studio")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="abre a interface gráfica (padrão)")
    web = sub.add_parser("web", help="inicia a versão web (abra o endereço no navegador)")
    web.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para aceitar outras máquinas")
    web.add_argument("--port", type=int, default=8000)
    for name in ("encode", "decode"):
        p = sub.add_parser(name, help=f"{'codifica' if name == 'encode' else 'decodifica'} um vídeo")
        p.add_argument("input", help="arquivo de vídeo (ou URL do YouTube no decode)")
        p.add_argument("output", help="arquivo .mp4 de saída")
        p.add_argument("--grid", choices=list(GRID_PRESETS))
        p.add_argument("--no-invert", action="store_true", help="desliga a inversão de cor")
        p.add_argument("--no-audio-scramble", action="store_true", help="mantém o áudio original")
        p.add_argument("--crf", type=int)
        p.add_argument("--preset")
        if name == "encode":
            p.add_argument("--resolution", choices=[*RESOLUTION_PRESETS, "Auto"])
        else:
            p.add_argument("--yt-quality", choices=list(media.YT_QUALITY_HEIGHTS))
            p.add_argument("--no-deblock", action="store_true")
    args = parser.parse_args(argv)

    if args.command in (None, "gui"):
        from .gui.app import run
        run()
        return
    if args.command == "web":
        from .web.server import run as run_web
        print(f"YouTube Cipher Studio Web em http://{args.host}:{args.port}")
        run_web(args.host, args.port)
        return

    cipher, export = to_settings(load_config())
    if args.grid:
        cipher.grid = args.grid
    cipher.invert_colors = cipher.invert_colors and not args.no_invert
    cipher.scramble_audio = cipher.scramble_audio and not args.no_audio_scramble
    export.crf = args.crf or export.crf
    export.preset = args.preset or export.preset
    if args.command == "encode":
        export.resolution = args.resolution or export.resolution
    else:
        export.yt_quality = args.yt_quality or export.yt_quality
        export.deblock = export.deblock and not args.no_deblock

    password = _read_password(confirm=args.command == "encode")

    def progress(text, fraction):
        pct = f"{fraction * 100:5.1f}%" if fraction is not None else "      "
        print(f"\r{pct}  {text[:90]:<90}", end="", flush=True)

    cancel = threading.Event()
    try:
        result = process_file(args.command, args.input, args.output, password, cipher, export, progress, cancel)
    except KeyboardInterrupt:
        cancel.set()
        sys.exit("\nCancelado.")
    except Cancelled:
        sys.exit("\nCancelado.")
    except (media.MediaError, ValueError) as exc:
        sys.exit(f"\nErro: {exc}")
    print(f"\nSalvo em {result.output} ({result.geometry.width}x{result.geometry.height}) — {cipher.describe()}")
    for warning in result.warnings:
        print(f"Aviso: {warning}")


if __name__ == "__main__":
    main()
