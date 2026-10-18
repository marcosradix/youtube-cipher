"""Interface gráfica (CustomTkinter).

Regra de threads: só a thread principal toca em widgets. Os workers enfileiram
chamadas em `self._ui_queue`, que a thread principal executa a cada 30 ms.
"""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from .. import __version__
from ..config import DEFAULT_CONFIG, load_config, save_config, to_settings
from ..core import media
from ..core.pipeline import Cancelled, process_file
from ..core.player import LivePlayer
from .player_widgets import BAR_BG, TIMELINE_STYLES, Timeline, VideoCanvas, format_time
from .tutorial import SECTIONS

STYLE_LABELS = {key: label for key, (label, _accent) in TIMELINE_STYLES.items()}
STYLE_KEYS = {label: key for key, label in STYLE_LABELS.items()}

GRID_LABELS = {
    "32x18": "32×18 blocos — robusta (60 px em 1080p)",
    "64x36": "64×36 blocos — equilibrada (30 px em 1080p)",
    "80x45": "80×45 blocos — fina (24 px em 1080p, ideal p/ 4K)",
}
RESOLUTIONS = ["720p", "1080p", "1440p", "2160p", "Auto"]
PRESETS = ["veryfast", "fast", "medium", "slow"]
BUFFER_CHOICES = [30, 60, 120, 300]
VIDEO_TYPES = [("Arquivos de vídeo", "*.mp4 *.mkv *.mov *.avi *.webm"), ("Todos os arquivos", "*.*")]

RED, RED_HOVER = "#c0392b", "#962d22"
GREEN, GREEN_HOVER = "#1f8b4c", "#166537"
MUTED = "#9a9a9a"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


def _format_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class YouTubeCipherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"YouTube Cipher Studio {__version__}")
        self.geometry("1060x780")
        self.minsize(900, 680)

        self.config_data = load_config()
        self._ui_queue: queue.Queue = queue.Queue()

        self.player: LivePlayer | None = None
        self._frame_lock = threading.Lock()
        self._pending_frame: Image.Image | None = None
        self._fullscreen = False
        self._click_job = None

        self.is_converting = False
        self.stop_convert_event = threading.Event()
        self._conv_started = 0.0
        self._last_output = ""

        self._build_ui()
        self._check_dependencies()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._bind_shortcuts()
        self.after(30, self._pump_ui_queue)
        self.after(15, self._pump_video)
        self.after(100, self._pump_player)

    # ------------------------------------------------------------------
    # Infraestrutura de threads
    # ------------------------------------------------------------------

    def _ui(self, fn, *args) -> None:
        """Agenda `fn(*args)` na thread principal (seguro a partir de qualquer thread)."""
        self._ui_queue.put((fn, args))

    def _pump_ui_queue(self) -> None:
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(30, self._pump_ui_queue)

    # ------------------------------------------------------------------
    # Estrutura principal
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.header = header = ctk.CTkFrame(self, height=50, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(header, text="🔒 YouTube Cipher Studio — Codificador, Decodificador & Player ao Vivo",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=20, pady=12)

        self.deps_banner = ctk.CTkLabel(self, text="", fg_color="#5c3d00", corner_radius=6, anchor="w",
                                        justify="left")

        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=10)
        self.tab_watch = self.tabview.add("▶ Assistir")
        self.tab_convert = self.tabview.add("⚙️ Converter")
        self.tab_settings = self.tabview.add("🛠 Configurações")
        self.tab_tutorial = self.tabview.add("📖 Tutorial & Guia Técnico")

        # As configurações vêm primeiro: as outras abas mostram um resumo delas.
        self._build_settings_tab()
        self._build_watch_tab()
        self._build_convert_tab()
        self._build_tutorial_tab()
        self._refresh_summaries()

    def _check_dependencies(self) -> None:
        missing = [name for name, ok in media.check_tools().items() if not ok]
        if not missing:
            return
        hints = {"ffmpeg": "FFmpeg (brew install ffmpeg · winget install ffmpeg · apt install ffmpeg)",
                 "ffprobe": "ffprobe (acompanha o FFmpeg)",
                 "yt-dlp": "yt-dlp (pip install -U yt-dlp) — necessário só para links do YouTube"}
        text = "⚠ Dependências ausentes:\n" + "\n".join(f"  • {hints[m]}" for m in missing)
        self.deps_banner.configure(text=text)
        self.deps_banner.pack(fill="x", padx=15, pady=(10, 0), before=self.tabview, ipady=6, ipadx=10)

    # ------------------------------------------------------------------
    # ABA: ASSISTIR AO VIVO
    # ------------------------------------------------------------------

    def _build_watch_tab(self) -> None:
        self.watch_top = top = ctk.CTkFrame(self.tab_watch)
        top.pack(fill="x", padx=10, pady=8)
        top.columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Link do YouTube ou arquivo:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.watch_source_entry = ctk.CTkEntry(
            top, placeholder_text="Cole a URL do YouTube ou escolha um vídeo codificado...")
        self.watch_source_entry.grid(row=0, column=1, padx=5, pady=8, sticky="ew")
        ctk.CTkButton(top, text="Procurar arquivo", width=140,
                      command=self._browse_watch_file).grid(row=0, column=2, padx=10, pady=8)

        ctk.CTkLabel(top, text="Senha secreta:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
        self.watch_key_entry = ctk.CTkEntry(top, placeholder_text="A mesma senha usada na codificação...", show="*")
        self.watch_key_entry.grid(row=1, column=1, padx=5, pady=8, sticky="ew")
        self.watch_key_entry.bind("<Return>", lambda _e: self.start_live_player())

        buttons = ctk.CTkFrame(top, fg_color="transparent")
        buttons.grid(row=1, column=2, padx=10, pady=8)
        self.btn_play = ctk.CTkButton(buttons, text="▶ Assistir", width=90, fg_color=GREEN,
                                      hover_color=GREEN_HOVER, command=self.start_live_player)
        self.btn_play.pack(side="left", padx=(0, 4))
        self.btn_stop = ctk.CTkButton(buttons, text="⏹ Parar", width=70, fg_color=RED, hover_color=RED_HOVER,
                                      state="disabled", command=self.stop_live_player)
        self.btn_stop.pack(side="left")

        self.watch_summary = ctk.CTkLabel(top, text="", text_color=MUTED, anchor="w")
        self.watch_summary.grid(row=2, column=0, columnspan=3, padx=10, pady=(0, 6), sticky="w")

        # Painel do player: vídeo + barra de controles, tudo em fundo escuro.
        self.player_panel = ctk.CTkFrame(self.tab_watch, fg_color=BAR_BG, corner_radius=10)
        self.player_panel.pack(fill="both", expand=True, padx=10, pady=4)

        self.video_canvas = VideoCanvas(
            self.player_panel,
            "Cole um link do YouTube ou escolha um vídeo codificado\n"
            "e clique em 'Assistir' para decodificar vídeo e áudio em tempo real.")
        self.video_canvas.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.video_canvas.bind("<Button-1>", self._on_video_click)
        self.video_canvas.bind("<Double-Button-1>", self._on_video_double_click)

        self.timeline = Timeline(self.player_panel, on_seek=self._on_timeline_seek,
                                 style=self.config_data["timeline_style"])
        self.timeline.pack(fill="x", padx=6)
        self.video_canvas.accent = self.timeline.accent

        controls = ctk.CTkFrame(self.player_panel, fg_color="transparent")
        controls.pack(fill="x", padx=10, pady=(0, 8))
        button_style = dict(width=40, height=34, fg_color="transparent", hover_color="#2a2a2a",
                            font=ctk.CTkFont(size=16), state="disabled")
        self.btn_back = ctk.CTkButton(controls, text="⟲10", command=lambda: self._seek_relative(-10),
                                      **{**button_style, "width": 52, "font": ctk.CTkFont(size=13)})
        self.btn_back.pack(side="left")
        self.btn_pause = ctk.CTkButton(controls, text="⏸", command=self.toggle_pause,
                                       **{**button_style, "width": 46, "font": ctk.CTkFont(size=20)})
        self.btn_pause.pack(side="left", padx=2)
        self.btn_forward = ctk.CTkButton(controls, text="10⟳", command=lambda: self._seek_relative(10),
                                         **{**button_style, "width": 52, "font": ctk.CTkFont(size=13)})
        self.btn_forward.pack(side="left")
        self.time_label = ctk.CTkLabel(controls, text="00:00 / 00:00",
                                       font=ctk.CTkFont(family="Menlo", size=13))
        self.time_label.pack(side="left", padx=14)

        self.btn_fullscreen = ctk.CTkButton(controls, text="⛶", width=40, height=34, fg_color="transparent",
                                            hover_color="#2a2a2a", font=ctk.CTkFont(size=18),
                                            command=self.toggle_fullscreen)
        self.btn_fullscreen.pack(side="right")
        self.volume_slider = ctk.CTkSlider(controls, from_=0, to=1, width=110, command=self._on_volume)
        self.volume_slider.set(float(self.config_data["volume"]))
        self.volume_slider.pack(side="right", padx=(0, 8))
        ctk.CTkLabel(controls, text="🔊").pack(side="right", padx=4)
        ctk.CTkOptionMenu(controls, variable=self.timeline_style_var, values=list(STYLE_KEYS), width=120,
                          height=28).pack(side="right", padx=12)
        ctk.CTkLabel(controls, text="Estilo:", text_color=MUTED).pack(side="right")

        self.watch_status_label = ctk.CTkLabel(self.tab_watch, text="Status: pronto para reproduzir", anchor="w",
                                               text_color="#aaaaaa")
        self.watch_status_label.pack(fill="x", padx=12, pady=(0, 4))

    def _browse_watch_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=VIDEO_TYPES)
        if path:
            self.watch_source_entry.delete(0, "end")
            self.watch_source_entry.insert(0, path)

    def _on_volume(self, value: float) -> None:
        self.config_data["volume"] = round(float(value), 2)
        if self.player:
            self.player.volume = float(value)

    def _on_timeline_style(self, *_args) -> None:
        style = STYLE_KEYS[self.timeline_style_var.get()]
        self.config_data["timeline_style"] = style
        if hasattr(self, "timeline"):
            self.timeline.set_style(style)
            self.video_canvas.set_accent(self.timeline.accent)

    # ------------------------------------------------------------------
    # ABA: CONVERTER
    # ------------------------------------------------------------------

    def _build_convert_tab(self) -> None:
        form = ctk.CTkFrame(self.tab_convert)
        form.pack(fill="both", expand=True, padx=15, pady=15)
        form.columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Operação:", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=15, pady=10, sticky="w")
        self.convert_mode_var = ctk.StringVar(value="encode")
        mode_frame = ctk.CTkFrame(form, fg_color="transparent")
        mode_frame.grid(row=0, column=1, columnspan=2, padx=10, pady=10, sticky="w")
        ctk.CTkRadioButton(mode_frame, text="🔒 Codificar (preparar para o YouTube)", variable=self.convert_mode_var,
                           value="encode", command=self._on_mode_change).pack(side="left", padx=(0, 25))
        ctk.CTkRadioButton(mode_frame, text="🔓 Decodificar (arquivo ou URL → MP4)", variable=self.convert_mode_var,
                           value="decode", command=self._on_mode_change).pack(side="left")

        ctk.CTkLabel(form, text="Vídeo de origem:").grid(row=1, column=0, padx=15, pady=10, sticky="w")
        self.conv_input_entry = ctk.CTkEntry(form, placeholder_text="Selecione o vídeo no PC "
                                                                    "(no decode, também aceita URL do YouTube)...")
        self.conv_input_entry.grid(row=1, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkButton(form, text="Selecionar vídeo...", width=150,
                      command=self._browse_convert_input).grid(row=1, column=2, padx=15, pady=10)

        ctk.CTkLabel(form, text="Salvar como (.mp4):").grid(row=2, column=0, padx=15, pady=10, sticky="w")
        self.conv_output_entry = ctk.CTkEntry(form, placeholder_text="Onde salvar o arquivo processado...")
        self.conv_output_entry.grid(row=2, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkButton(form, text="Salvar como...", width=150,
                      command=self._browse_convert_output).grid(row=2, column=2, padx=15, pady=10)

        ctk.CTkLabel(form, text="Senha secreta:").grid(row=3, column=0, padx=15, pady=10, sticky="w")
        self.conv_key_entry = ctk.CTkEntry(form, placeholder_text="Digite a senha secreta...", show="*")
        self.conv_key_entry.grid(row=3, column=1, padx=10, pady=10, sticky="ew")
        self.show_pass_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Mostrar senha", variable=self.show_pass_var,
                        command=self._toggle_password_visibility).grid(row=3, column=2, padx=15, pady=10, sticky="w")

        self.confirm_label = ctk.CTkLabel(form, text="Confirmar senha:")
        self.confirm_label.grid(row=4, column=0, padx=15, pady=10, sticky="w")
        self.conv_confirm_entry = ctk.CTkEntry(form, placeholder_text="Repita a senha — sem ela o vídeo "
                                                                      "não pode ser recuperado", show="*")
        self.conv_confirm_entry.grid(row=4, column=1, padx=10, pady=10, sticky="ew")

        self.convert_summary = ctk.CTkLabel(form, text="", text_color=MUTED, anchor="w", justify="left")
        self.convert_summary.grid(row=5, column=0, columnspan=3, padx=15, pady=(4, 0), sticky="ew")

        progress = ctk.CTkFrame(form, fg_color="transparent")
        progress.grid(row=6, column=0, columnspan=3, padx=15, pady=(14, 6), sticky="ew")
        self.conv_status_label = ctk.CTkLabel(progress, text="Aguardando início...", anchor="w")
        self.conv_status_label.pack(fill="x", pady=(0, 6))
        self.conv_progressbar = ctk.CTkProgressBar(progress)
        self.conv_progressbar.pack(fill="x", pady=4)
        self.conv_progressbar.set(0)

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=7, column=0, columnspan=3, pady=8)
        self.btn_start_conv = ctk.CTkButton(actions, text="🚀 Iniciar processamento", height=42, width=220,
                                            font=ctk.CTkFont(size=14, weight="bold"), command=self.start_conversion)
        self.btn_start_conv.pack(side="left", padx=8)
        self.btn_cancel_conv = ctk.CTkButton(actions, text="Cancelar", height=42, width=110, fg_color=RED,
                                             hover_color=RED_HOVER, state="disabled", command=self.cancel_conversion)
        self.btn_cancel_conv.pack(side="left", padx=8)
        self.btn_open_folder = ctk.CTkButton(actions, text="📂 Abrir pasta", height=42, width=130,
                                             state="disabled", command=self._open_output_folder)
        self.btn_open_folder.pack(side="left", padx=8)

        self.conv_log = ctk.CTkTextbox(form, height=110, font=ctk.CTkFont(family="Menlo", size=12))
        self.conv_log.grid(row=8, column=0, columnspan=3, padx=15, pady=(6, 12), sticky="nsew")
        self.conv_log.configure(state="disabled")
        form.rowconfigure(8, weight=1)

    def _on_mode_change(self) -> None:
        encode = self.convert_mode_var.get() == "encode"
        if encode:
            self.confirm_label.grid()
            self.conv_confirm_entry.grid()
        else:
            self.confirm_label.grid_remove()
            self.conv_confirm_entry.grid_remove()
        # Troca o sufixo se o nome de saída foi sugerido automaticamente.
        out = self.conv_output_entry.get()
        old, new = ("_decoded.mp4", "_encoded.mp4") if encode else ("_encoded.mp4", "_decoded.mp4")
        if out.endswith(old):
            self.conv_output_entry.delete(0, "end")
            self.conv_output_entry.insert(0, out[: -len(old)] + new)
        self._refresh_summaries()

    def _toggle_password_visibility(self) -> None:
        char = "" if self.show_pass_var.get() else "*"
        for entry in (self.conv_key_entry, self.conv_confirm_entry, self.watch_key_entry):
            entry.configure(show=char)

    def _browse_convert_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=VIDEO_TYPES)
        if not path:
            return
        self.conv_input_entry.delete(0, "end")
        self.conv_input_entry.insert(0, path)
        if not self.conv_output_entry.get():
            base, _ = os.path.splitext(path)
            suffix = "_encoded.mp4" if self.convert_mode_var.get() == "encode" else "_decoded.mp4"
            self.conv_output_entry.insert(0, base + suffix)

    def _browse_convert_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("Vídeo MP4", "*.mp4")])
        if path:
            self.conv_output_entry.delete(0, "end")
            self.conv_output_entry.insert(0, path)

    def _log(self, text: str) -> None:
        self.conv_log.configure(state="normal")
        self.conv_log.insert("end", f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.conv_log.see("end")
        self.conv_log.configure(state="disabled")

    def _open_output_folder(self) -> None:
        folder = os.path.dirname(os.path.abspath(self._last_output))
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", self._last_output])
        else:
            subprocess.Popen(["xdg-open", folder])

    # ------------------------------------------------------------------
    # ABA: CONFIGURAÇÕES
    # ------------------------------------------------------------------

    def _section(self, parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", padx=8, pady=8)
        frame.columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=15, weight="bold"), text_color="#4da6ff",
                     anchor="w").grid(row=0, column=0, columnspan=2, padx=15, pady=(12, 0), sticky="w")
        if subtitle:
            ctk.CTkLabel(frame, text=subtitle, text_color=MUTED, anchor="w").grid(
                row=1, column=0, columnspan=2, padx=15, pady=(0, 4), sticky="w")
        return frame

    def _row(self, frame, row: int, label: str, widget) -> None:
        ctk.CTkLabel(frame, text=label, anchor="w").grid(row=row, column=0, padx=(15, 20), pady=8, sticky="w")
        widget.grid(row=row, column=1, padx=10, pady=8, sticky="w")

    def _build_settings_tab(self) -> None:
        scroll = ctk.CTkScrollableFrame(self.tab_settings)
        scroll.pack(fill="both", expand=True, padx=6, pady=6)
        cfg = self.config_data

        sec = self._section(scroll, "Parâmetros da cifra",
                            "Precisam ser IGUAIS na codificação e na decodificação de um vídeo.")
        self.grid_var = ctk.StringVar(value=GRID_LABELS.get(cfg["grid"], GRID_LABELS["32x18"]))
        self._row(sec, 2, "Grade de macroblocos:",
                  ctk.CTkOptionMenu(sec, variable=self.grid_var, values=list(GRID_LABELS.values()), width=380))
        self.invert_colors_var = ctk.BooleanVar(value=cfg["invert_colors"])
        self._row(sec, 3, "Ofuscação de cor:",
                  ctk.CTkSwitch(sec, text="Inverter cores (efeito negativo)", variable=self.invert_colors_var))
        self.scramble_audio_var = ctk.BooleanVar(value=cfg["scramble_audio"])
        self._row(sec, 4, "Áudio:", ctk.CTkSwitch(sec, text="Embaralhar áudio (inversão espectral + fatias)",
                                                  variable=self.scramble_audio_var))

        sec = self._section(scroll, "Exportação (codificação)")
        self.resolution_var = ctk.StringVar(value=cfg["resolution"])
        self._row(sec, 2, "Resolução enviada ao YouTube:",
                  ctk.CTkOptionMenu(sec, variable=self.resolution_var, values=RESOLUTIONS, width=160))
        crf_frame = ctk.CTkFrame(sec, fg_color="transparent")
        self.crf_label_val = ctk.CTkLabel(crf_frame, text=f"CRF {cfg['crf']}", width=60)
        self.crf_slider = ctk.CTkSlider(crf_frame, from_=12, to=24, number_of_steps=12, width=250,
                                        command=lambda v: self.crf_label_val.configure(text=f"CRF {int(v)}"))
        self.crf_slider.set(cfg["crf"])
        self.crf_slider.pack(side="left")
        self.crf_label_val.pack(side="left", padx=10)
        self._row(sec, 3, "Qualidade H.264 (12 = máxima, 24 = leve):", crf_frame)
        self.preset_var = ctk.StringVar(value=cfg["preset"])
        self._row(sec, 4, "Velocidade do encoder:",
                  ctk.CTkOptionMenu(sec, variable=self.preset_var, values=PRESETS, width=160))

        sec = self._section(scroll, "Reprodução e decodificação")
        self.yt_quality_var = ctk.StringVar(value=cfg["yt_quality"])
        self._row(sec, 2, "Qualidade máxima do YouTube:",
                  ctk.CTkOptionMenu(sec, variable=self.yt_quality_var, values=list(media.YT_QUALITY_HEIGHTS),
                                    width=160))
        self.deblock_var = ctk.BooleanVar(value=cfg["deblock"])
        self._row(sec, 3, "Pós-processamento:",
                  ctk.CTkSwitch(sec, text="Suavizar emendas entre blocos", variable=self.deblock_var))
        # Compartilhada com o seletor "Estilo" da barra do player.
        self.timeline_style_var = ctk.StringVar(value=STYLE_LABELS.get(cfg["timeline_style"], "Pílula"))
        self.timeline_style_var.trace_add("write", self._on_timeline_style)
        self._row(sec, 4, "Estilo da linha do tempo:",
                  ctk.CTkOptionMenu(sec, variable=self.timeline_style_var, values=list(STYLE_KEYS), width=160))
        self.buffer_var = ctk.StringVar(value=f"{cfg['buffer_seconds']} s")
        self._row(sec, 5, "Carregar à frente (buffer):",
                  ctk.CTkOptionMenu(sec, variable=self.buffer_var, values=[f"{s} s" for s in BUFFER_CHOICES],
                                    width=160))

        buttons = ctk.CTkFrame(scroll, fg_color="transparent")
        buttons.pack(pady=16)
        ctk.CTkButton(buttons, text="💾 Salvar configurações", height=40, width=220,
                      command=self.save_settings).pack(side="left", padx=8)
        ctk.CTkButton(buttons, text="Restaurar padrões", height=40, width=160, fg_color="#555555",
                      hover_color="#444444", command=self.reset_settings).pack(side="left", padx=8)

        for var in (self.grid_var, self.invert_colors_var, self.scramble_audio_var, self.resolution_var):
            var.trace_add("write", lambda *_: self._refresh_summaries())

    def _current_config(self) -> dict:
        grid = next(k for k, v in GRID_LABELS.items() if v == self.grid_var.get())
        return {
            **self.config_data,
            "grid": grid,
            "invert_colors": bool(self.invert_colors_var.get()),
            "scramble_audio": bool(self.scramble_audio_var.get()),
            "resolution": self.resolution_var.get(),
            "crf": int(self.crf_slider.get()),
            "preset": self.preset_var.get(),
            "deblock": bool(self.deblock_var.get()),
            "yt_quality": self.yt_quality_var.get(),
            "volume": round(float(self.volume_slider.get()), 2) if hasattr(self, "volume_slider") else 1.0,
            "timeline_style": STYLE_KEYS[self.timeline_style_var.get()],
            "buffer_seconds": int(self.buffer_var.get().split()[0]),
        }

    def save_settings(self, quiet: bool = False) -> None:
        self.config_data = self._current_config()
        try:
            save_config(self.config_data)
        except OSError as exc:
            if not quiet:
                messagebox.showerror("Configurações", f"Não foi possível salvar:\n{exc}")
            return
        if not quiet:
            messagebox.showinfo("Configurações", "Configurações salvas com sucesso!")

    def reset_settings(self) -> None:
        d = DEFAULT_CONFIG
        self.grid_var.set(GRID_LABELS[d["grid"]])
        self.timeline_style_var.set(STYLE_LABELS[d["timeline_style"]])
        self.buffer_var.set(f"{d['buffer_seconds']} s")
        self.invert_colors_var.set(d["invert_colors"])
        self.scramble_audio_var.set(d["scramble_audio"])
        self.resolution_var.set(d["resolution"])
        self.crf_slider.set(d["crf"])
        self.crf_label_val.configure(text=f"CRF {d['crf']}")
        self.preset_var.set(d["preset"])
        self.yt_quality_var.set(d["yt_quality"])
        self.deblock_var.set(d["deblock"])

    def _refresh_summaries(self) -> None:
        if not hasattr(self, "convert_summary"):
            return
        cipher, export = to_settings(self._current_config())
        self.watch_summary.configure(text=f"Parâmetros: {cipher.describe()}  (altere em 🛠 Configurações)")
        extra = f" · saída {export.resolution}" if self.convert_mode_var.get() == "encode" else ""
        self.convert_summary.configure(text=f"Parâmetros: {cipher.describe()}{extra}")

    # ------------------------------------------------------------------
    # ABA: TUTORIAL
    # ------------------------------------------------------------------

    def _build_tutorial_tab(self) -> None:
        scroll = ctk.CTkScrollableFrame(self.tab_tutorial, corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)
        bodies = []
        for title, body in SECTIONS:
            card = ctk.CTkFrame(scroll, corner_radius=8)
            card.pack(fill="x", padx=8, pady=8)
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=15, weight="bold"), text_color="#4da6ff",
                         anchor="w").pack(fill="x", padx=15, pady=(12, 6))
            label = ctk.CTkLabel(card, text=body, font=ctk.CTkFont(size=13), justify="left", anchor="w",
                                 wraplength=880)
            label.pack(fill="x", padx=15, pady=(0, 14))
            bodies.append(label)

        def rewrap(event):
            for label in bodies:
                label.configure(wraplength=max(400, event.width - 70))

        scroll.bind("<Configure>", rewrap)

    # ------------------------------------------------------------------
    # CONVERSÃO EM SEGUNDO PLANO
    # ------------------------------------------------------------------

    def start_conversion(self) -> None:
        if self.is_converting:
            return
        source = self.conv_input_entry.get().strip()
        output = self.conv_output_entry.get().strip()
        password = self.conv_key_entry.get()
        mode = self.convert_mode_var.get()

        if not source or not output or not password:
            messagebox.showwarning("Campos obrigatórios", "Preencha o vídeo de origem, onde salvar e a senha.")
            return
        if mode == "encode" and password != self.conv_confirm_entry.get():
            messagebox.showwarning("Senha", "A confirmação não confere com a senha.")
            return
        if not media.is_url(source) and not os.path.isfile(source):
            messagebox.showwarning("Arquivo", "O vídeo de origem não foi encontrado.")
            return
        if not output.lower().endswith(".mp4"):
            output += ".mp4"
            self.conv_output_entry.delete(0, "end")
            self.conv_output_entry.insert(0, output)
        if os.path.exists(output) and not messagebox.askyesno("Sobrescrever?", f"{output}\n\njá existe. Substituir?"):
            return

        cipher, export = to_settings(self._current_config())  # instantâneo, lido na thread principal
        self.is_converting = True
        self.stop_convert_event.clear()
        self._conv_started = time.perf_counter()
        self.btn_start_conv.configure(state="disabled")
        self.btn_cancel_conv.configure(state="normal")
        self.btn_open_folder.configure(state="disabled")
        self.conv_progressbar.set(0)
        self._log(f"{'Codificando' if mode == 'encode' else 'Decodificando'}: {source}")
        self._log(f"Parâmetros: {cipher.describe()}")
        threading.Thread(target=self._conversion_worker, args=(mode, source, output, password, cipher, export),
                         daemon=True).start()

    def cancel_conversion(self) -> None:
        if self.is_converting:
            self.stop_convert_event.set()
            self.conv_status_label.configure(text="Cancelando operação...")

    def _set_conv_progress(self, text: str, fraction: float | None) -> None:
        if self.stop_convert_event.is_set():
            return
        if fraction is not None:
            self.conv_progressbar.set(fraction)
            elapsed = time.perf_counter() - self._conv_started
            if 0.03 < fraction < 1:
                text += f"  ·  restante ~{_format_eta(elapsed / fraction - elapsed)}"
        self.conv_status_label.configure(text=text)

    def _conversion_worker(self, mode, source, output, password, cipher, export) -> None:
        progress = lambda text, fraction=None: self._ui(self._set_conv_progress, text, fraction)  # noqa: E731
        try:
            result = process_file(mode, source, output, password, cipher, export, progress, self.stop_convert_event)
        except Cancelled:
            self._ui(self._conversion_finished, None, "Operação cancelada pelo usuário.")
        except Exception as exc:  # noqa: BLE001
            self._ui(self._conversion_finished, None, str(exc))
        else:
            self._ui(self._conversion_finished, result, None)

    def _conversion_finished(self, result, error: str | None) -> None:
        self.is_converting = False
        self.btn_start_conv.configure(state="normal")
        self.btn_cancel_conv.configure(state="disabled")
        elapsed = _format_eta(time.perf_counter() - self._conv_started)
        if error is not None:
            self.conv_progressbar.set(0)
            self.conv_status_label.configure(text=error.splitlines()[0])
            self._log(f"Erro: {error}")
            if not self.stop_convert_event.is_set():
                messagebox.showerror("Erro no processamento", error)
            return

        self._last_output = result.output
        self.btn_open_folder.configure(state="normal")
        g = result.geometry
        self.conv_status_label.configure(text=f"✅ Concluído em {elapsed} — {result.output}")
        self._log(f"Concluído em {elapsed}: {result.output} ({g.width}x{g.height}, blocos de {g.block} px)")
        cipher, _ = to_settings(self._current_config())
        message = f"Vídeo salvo em:\n{result.output}\n\nResolução: {g.width}x{g.height}"
        if self.convert_mode_var.get() == "encode":
            message += (f"\n\nPara decodificar, use a mesma senha e:\n{cipher.describe()}")
        for warning in result.warnings:
            self._log(f"Aviso: {warning}")
        if result.warnings:
            messagebox.showwarning("Concluído com aviso", message + "\n\n⚠ " + "\n".join(result.warnings))
        else:
            messagebox.showinfo("Sucesso", message)

    # ------------------------------------------------------------------
    # PLAYER AO VIVO
    # ------------------------------------------------------------------

    def start_live_player(self) -> None:
        if self.player is not None:
            return
        source = self.watch_source_entry.get().strip()
        password = self.watch_key_entry.get()
        if not source or not password:
            messagebox.showwarning("Aviso", "Informe o link/arquivo de vídeo e a senha secreta.")
            return
        if not media.is_url(source) and not os.path.isfile(source):
            messagebox.showwarning("Arquivo", "O vídeo não foi encontrado.")
            return

        cipher, export = to_settings(self._current_config())
        self.player = LivePlayer(
            source, password, cipher, export,
            display_width=lambda: self.video_canvas.size[0],
            on_frame=self._on_player_frame,
            on_done=lambda error: self._ui(self._player_finished, error))
        self.player.volume = float(self.volume_slider.get())
        self.btn_play.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        for button in (self.btn_back, self.btn_pause, self.btn_forward):
            button.configure(state="normal")
        self.video_canvas.reset()
        self.video_canvas.set_buffering(True)
        self.player.start()

    def toggle_pause(self) -> None:
        if self.player:
            self.player.toggle_pause()

    def _seek_relative(self, delta: float) -> None:
        if self.player:
            self.player.seek_relative(delta)

    def _on_timeline_seek(self, seconds: float) -> None:
        if self.player:
            self.player.seek(seconds)

    def stop_live_player(self) -> None:
        if self.player:
            self.player.stop()
            self._set_watch_status("parando reprodução...")

    def _set_watch_status(self, text: str) -> None:
        self.watch_status_label.configure(text=f"Status: {text}")

    def _on_video_click(self, _event) -> None:
        # Clique simples pausa; espera um instante para não conflitar com o duplo clique (tela cheia).
        if self._click_job is None:
            self._click_job = self.after(250, self._single_click)

    def _single_click(self) -> None:
        self._click_job = None
        self.toggle_pause()

    def _on_video_double_click(self, _event) -> None:
        if self._click_job is not None:
            self.after_cancel(self._click_job)
            self._click_job = None
        self.toggle_fullscreen()

    def _on_player_frame(self, image: Image.Image) -> None:
        """Roda na thread do player: redimensiona aqui para aliviar a thread da interface."""
        box_w, box_h = self.video_canvas.size
        w, h = image.size
        scale = min(box_w / w, box_h / h)
        if abs(scale - 1) > 0.01:
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
        with self._frame_lock:
            self._pending_frame = image

    def _pump_video(self) -> None:
        with self._frame_lock:
            image, self._pending_frame = self._pending_frame, None
        if image is not None:
            self.video_canvas.show_frame(image)
        self.after(10, self._pump_video)

    def _pump_player(self) -> None:
        """Atualiza tempo, linha do tempo, carregamento e ícones a partir do estado do player."""
        if self.player is not None:
            state = self.player.snapshot()
            self.time_label.configure(
                text=f"{format_time(state.position, state.duration)} / {format_time(state.duration, state.duration)}")
            self.timeline.set_progress(state.position, state.duration, state.buffered)
            self.video_canvas.set_buffering(state.buffering and not state.paused and not state.ended)
            self.video_canvas.set_center_icon("replay" if state.ended else "play" if state.paused else None)
            self.btn_pause.configure(text="↻" if state.ended else "▶" if state.paused else "⏸")
            if state.status:
                self._set_watch_status(state.status)
        self.after(100, self._pump_player)

    def _player_finished(self, error: Exception | None) -> None:
        self.player = None
        self.btn_play.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        for button in (self.btn_back, self.btn_pause, self.btn_forward):
            button.configure(state="disabled")
        self.btn_pause.configure(text="⏸")
        self.video_canvas.set_buffering(False)
        self.video_canvas.set_center_icon(None)
        self._set_watch_status("reprodução finalizada" if error is None else f"erro — {error}")
        if error is not None:
            messagebox.showerror("Erro na reprodução", str(error))

    # ------------------------------------------------------------------
    # TELA CHEIA E ATALHOS
    # ------------------------------------------------------------------

    def toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        segmented = getattr(self.tabview, "_segmented_button", None)
        if self._fullscreen:
            self.tabview.set("▶ Assistir")
            self._banner_visible = self.deps_banner.winfo_ismapped()
            for widget in (self.header, self.deps_banner, self.watch_top, self.watch_status_label):
                widget.pack_forget()
            if segmented is not None:
                segmented.grid_remove()
            self.attributes("-fullscreen", True)
            self.btn_fullscreen.configure(text="🗗")
        else:
            self.attributes("-fullscreen", False)
            self.header.pack(fill="x", before=self.tabview)
            if self._banner_visible:
                self.deps_banner.pack(fill="x", padx=15, pady=(10, 0), before=self.tabview, ipady=6, ipadx=10)
            if segmented is not None:
                segmented.grid()
            self.watch_top.pack(fill="x", padx=10, pady=8, before=self.player_panel)
            self.watch_status_label.pack(fill="x", padx=12, pady=(0, 4))
            self.btn_fullscreen.configure(text="⛶")

    def _bind_shortcuts(self) -> None:
        def handler(action):
            def run(event):
                focused = self.focus_get()
                if isinstance(focused, (tk.Entry, tk.Text)) or self.tabview.get() != "▶ Assistir":
                    return None
                action()
                return "break"
            return run

        self.bind("<space>", handler(self.toggle_pause))
        self.bind("<Left>", handler(lambda: self._seek_relative(-10)))
        self.bind("<Right>", handler(lambda: self._seek_relative(10)))
        self.bind("<KeyPress-f>", handler(self.toggle_fullscreen))
        self.bind("<Escape>", lambda _e: self.toggle_fullscreen() if self._fullscreen else None)

    # ------------------------------------------------------------------

    def on_close(self) -> None:
        if self.player:
            self.player.stop()
        self.stop_convert_event.set()
        self.save_settings(quiet=True)
        self.destroy()


def run() -> None:
    app = YouTubeCipherApp()
    app.mainloop()
