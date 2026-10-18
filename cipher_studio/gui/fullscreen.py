"""Janela de tela cheia: só o vídeo, com controles que aparecem ao mexer o mouse.

É uma janela separada (e não a principal com partes escondidas) para que nenhuma
margem ou painel da interface sobre na tela. Os controles e o cursor somem após
alguns segundos sem movimento e voltam com qualquer movimento ou clique.
"""
import tkinter as tk

import customtkinter as ctk

from .player_widgets import BAR_BG, PLAYER_BG, Timeline, VideoCanvas, fullscreen_icon

HIDE_AFTER_MS = 2500


class FullscreenView(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("YouTube Cipher Studio")
        self.configure(bg=PLAYER_BG)
        self._controls_visible = False
        self._hovering_bar = False
        self._hide_job = None
        self._last_motion = (None, None)

        self.canvas = VideoCanvas(self, "")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.set_accent(app.timeline.accent)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", app._on_video_double_click)

        # Barra de controles sobreposta à parte de baixo do vídeo.
        self.bar = ctk.CTkFrame(self, fg_color=BAR_BG, corner_radius=0)
        self.timeline = Timeline(self.bar, on_seek=app._on_timeline_seek, style=app.timeline.style)
        self.timeline.pack(fill="x", padx=12, pady=(6, 0))
        row = ctk.CTkFrame(self.bar, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 10))
        style = dict(height=36, fg_color="transparent", hover_color="#2a2a2a")
        ctk.CTkButton(row, text="⟲10", width=56, font=ctk.CTkFont(size=14),
                      command=lambda: app._seek_relative(-10), **style).pack(side="left")
        self.btn_pause = ctk.CTkButton(row, text="⏸", width=50, font=ctk.CTkFont(size=22),
                                       command=app.toggle_pause, **style)
        self.btn_pause.pack(side="left", padx=2)
        ctk.CTkButton(row, text="10⟳", width=56, font=ctk.CTkFont(size=14),
                      command=lambda: app._seek_relative(10), **style).pack(side="left")
        self.time_label = ctk.CTkLabel(row, text="00:00 / 00:00", font=ctk.CTkFont(family="Menlo", size=14))
        self.time_label.pack(side="left", padx=16)

        exit_icon = fullscreen_icon("exit", 22)
        ctk.CTkButton(row, text="", width=44, image=ctk.CTkImage(exit_icon, exit_icon, size=(22, 22)),
                      command=app.toggle_fullscreen, **style).pack(side="right")
        self.volume = ctk.CTkSlider(row, from_=0, to=1, width=130, command=self._on_volume)
        self.volume.set(float(app.volume_slider.get()))
        self.volume.pack(side="right", padx=(0, 10))
        ctk.CTkLabel(row, text="🔊").pack(side="right", padx=4)

        for widget in (self.bar, self.timeline):
            widget.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            widget.bind("<Leave>", lambda _e: self._set_hover(False), add="+")
        self.bind("<Motion>", self._on_motion)
        self.bind("<Escape>", lambda _e: app.toggle_fullscreen())
        self.bind("<KeyPress-f>", lambda _e: app.toggle_fullscreen())
        self.bind("<space>", lambda _e: (self.show_controls(), app.toggle_pause()))
        self.bind("<Left>", lambda _e: (self.show_controls(), app._seek_relative(-10)))
        self.bind("<Right>", lambda _e: (self.show_controls(), app._seek_relative(10)))
        self.protocol("WM_DELETE_WINDOW", app.toggle_fullscreen)

        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.after(30, self._enter)

    def _enter(self) -> None:
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        self.lift()
        self.focus_force()
        self.show_controls()

    # ---------------- controles automáticos ----------------

    def show_controls(self) -> None:
        if not self._controls_visible:
            self.bar.place(relx=0, rely=1, relwidth=1, anchor="sw")
            self.bar.lift()
            self._set_cursor("")
            self._controls_visible = True
        self._schedule_hide()

    def _schedule_hide(self) -> None:
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
        self._hide_job = self.after(HIDE_AFTER_MS, self._hide_controls)

    def _hide_controls(self) -> None:
        self._hide_job = None
        state = self.app.player.snapshot() if self.app.player else None
        # Mantém visível com o mouse sobre a barra ou com o vídeo pausado/no fim.
        if self._hovering_bar or (state is not None and (state.paused or state.ended)):
            self._schedule_hide()
            return
        self.bar.place_forget()
        self._set_cursor("none")
        self._controls_visible = False

    def _set_cursor(self, cursor: str) -> None:
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _set_hover(self, hovering: bool) -> None:
        self._hovering_bar = hovering

    def _on_motion(self, event) -> None:
        # Alguns sistemas geram <Motion> sem movimento real; só reage a mudança de posição.
        position = (event.x_root, event.y_root)
        if position != self._last_motion:
            self._last_motion = position
            self.show_controls()

    def _on_click(self, event) -> None:
        if not self._controls_visible:
            self.show_controls()      # primeiro clique só traz os controles de volta
        else:
            self.show_controls()
            self.app._on_video_click(event)

    def _on_volume(self, value: float) -> None:
        self.app.volume_slider.set(value)
        self.app._on_volume(value)

    # ---------------- atualização pelo app ----------------

    def update_state(self, state, time_text: str, pause_text: str) -> None:
        self.time_label.configure(text=time_text)
        self.timeline.set_progress(state.position, state.duration, state.buffered)
        self.canvas.set_buffering(state.buffering and not state.paused and not state.ended)
        self.canvas.set_center_icon("replay" if state.ended else "play" if state.paused else None)
        self.btn_pause.configure(text=pause_text)
