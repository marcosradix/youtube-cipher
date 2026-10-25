"""Componentes visuais do player: área de vídeo com sobreposições e linha do tempo.

Ambos são `tk.Canvas` desenhados à mão, para permitir formas arredondadas
(pílulas, cápsulas, pontos) que os widgets padrão não oferecem.
"""
import math
import tkinter as tk

from PIL import Image, ImageTk

PLAYER_BG = "#0a0a0a"
BAR_BG = "#141414"

# Cada estilo: nome exibido e cor de destaque (também usada no indicador de carregamento).
TIMELINE_STYLES = {
    "pill": ("Pílula", "#8b5cf6"),
    "capsule": ("Cápsula", "#2dd4bf"),
    "segments": ("Segmentos", "#f59e0b"),
    "dots": ("Pontos", "#ec4899"),
    "gradient": ("Gradiente", "#22d3ee"),
}
_GRADIENT = ("#22d3ee", "#8b5cf6", "#ec4899")


def fullscreen_icon(kind: str, size: int = 20, color: str = "#e8e8e8") -> Image.Image:
    """Ícone de tela cheia desenhado (cantos para fora = entrar, para dentro = sair).

    Desenhado como imagem porque símbolos Unicode como "🗗" não existem em todas as
    fontes e aparecem como "?".
    """
    from PIL import ImageDraw

    s = size * 4                       # desenha grande e reduz: bordas suaves
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, a, b = max(3, s // 9), s * 0.14, s * 0.40   # espessura, borda, comprimento do canto
    lo, hi = a, s - a
    corners = [(lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)]
    for x, y, dx, dy in corners:
        length = b - a
        if kind == "exit":            # canto aponta para dentro: o vértice fica deslocado para o centro
            length = s * 0.19
            inset = s * 0.36 - a
            x, y, dx, dy = x + dx * inset, y + dy * inset, -dx, -dy
        pts = [(x, y + dy * length), (x, y), (x + dx * length, y)]
        d.line(pts, fill=color, width=w, joint="curve")
        for px, py in (pts[0], pts[2]):
            d.ellipse([px - w / 2, py - w / 2, px + w / 2, py + w / 2], fill=color)
    return img.resize((size * 2, size * 2), Image.LANCZOS)


def format_time(seconds: float, reference: float = 0.0) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h or reference >= 3600:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _gradient_at(t: float) -> str:
    t = min(1.0, max(0.0, t)) * (len(_GRADIENT) - 1)
    i = min(int(t), len(_GRADIENT) - 2)
    return _mix(_GRADIENT[i], _GRADIENT[i + 1], t - i)


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


def _pill(canvas: tk.Canvas, x1, x2, y, thickness, color, **kw):
    """Barra com pontas totalmente arredondadas (linha com capstyle redondo)."""
    if x2 - x1 < 1:
        r = thickness / 2
        return canvas.create_oval(x1 - r, y - r, x1 + r, y + r, fill=color, outline="", **kw)
    return canvas.create_line(x1, y, x2, y, width=thickness, fill=color, capstyle=tk.ROUND, **kw)


def _circle(canvas: tk.Canvas, x, y, r, **kw):
    return canvas.create_oval(x - r, y - r, x + r, y + r, **kw)


class Timeline(tk.Canvas):
    """Linha do tempo clicável/arrastável com balão de tempo ao passar o mouse."""

    PAD = 16
    TRACK_Y = 31
    HEIGHT = 46

    def __init__(self, master, on_seek, style: str = "pill"):
        super().__init__(master, height=self.HEIGHT, bg=BAR_BG, highlightthickness=0, cursor="hand2")
        self.on_seek = on_seek
        self.style = style if style in TIMELINE_STYLES else "pill"
        self._position = 0.0
        self._duration = 0.0
        self._buffered = 0.0
        self._hover_x: float | None = None
        self._drag_frac: float | None = None
        self._xb = 0.0
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    @property
    def accent(self) -> str:
        return TIMELINE_STYLES[self.style][1]

    def set_style(self, style: str) -> None:
        self.style = style if style in TIMELINE_STYLES else "pill"
        self.redraw()

    def set_progress(self, position: float, duration: float, buffered: float = 0.0) -> None:
        if abs(position - self._position) < 0.05 and duration == self._duration \
                and abs(buffered - self._buffered) < 0.25:
            return
        self._position, self._duration, self._buffered = position, duration, buffered
        if self._drag_frac is None:
            self.redraw()

    # ---------------- interação ----------------

    def _frac_at(self, x: float) -> float:
        width = max(1, self.winfo_width() - 2 * self.PAD)
        return min(1.0, max(0.0, (x - self.PAD) / width))

    def _on_motion(self, event) -> None:
        self._hover_x = event.x
        self.redraw()

    def _on_leave(self, _event) -> None:
        self._hover_x = None
        self.redraw()

    def _on_press(self, event) -> None:
        if self._duration > 0:
            self._drag_frac = self._frac_at(event.x)
            self.redraw()

    def _on_drag(self, event) -> None:
        if self._drag_frac is not None:
            self._hover_x = event.x
            self._drag_frac = self._frac_at(event.x)
            self.redraw()

    def _on_release(self, _event) -> None:
        if self._drag_frac is not None:
            target = self._drag_frac * self._duration
            self._position = target
            self._drag_frac = None
            self.redraw()
            self.on_seek(target)

    # ---------------- desenho ----------------

    def redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w < 2 * self.PAD + 10:
            return
        x1, x2, y = self.PAD, w - self.PAD, self.TRACK_Y
        if self._drag_frac is not None:
            frac = self._drag_frac
        elif self._duration > 0:
            frac = min(1.0, self._position / self._duration)
        else:
            frac = 0.0
        xp = x1 + (x2 - x1) * frac
        buffered_frac = min(1.0, self._buffered / self._duration) if self._duration > 0 else 0.0
        # Trecho já carregado (cinza mais claro), desenhado por cada estilo.
        self._xb = max(xp, x1 + (x2 - x1) * buffered_frac)
        active = self._hover_x is not None or self._drag_frac is not None
        getattr(self, f"_draw_{self.style}")(x1, x2, y, xp, frac, active)
        if active and self._duration > 0:
            bubble_x = self._hover_x if self._hover_x is not None else xp
            self._draw_bubble(bubble_x, self._frac_at(bubble_x) * self._duration)

    def _draw_bubble(self, x: float, seconds: float) -> None:
        text = format_time(seconds, self._duration)
        half = 6 + 4 * len(text)
        x = min(max(x, self.PAD + half - 8), self.winfo_width() - self.PAD - half + 8)
        _round_rect(self, x - half, 2, x + half, 20, 9, fill="#f1f1f1", outline="")
        self.create_text(x, 11, text=text, fill="#111111", font=("Helvetica", 10, "bold"))

    def _draw_pill(self, x1, x2, y, xp, _frac, active):
        thick = 10 if active else 8
        _pill(self, x1, x2, y, thick, "#2f2f36")
        _pill(self, x1, self._xb, y, thick, "#55556a")
        _pill(self, x1, xp, y, thick, self.accent)
        r = 9 if active else 7
        _circle(self, xp, y, r, fill="#ffffff", outline=self.accent, width=3)

    def _draw_capsule(self, x1, x2, y, xp, _frac, active):
        _pill(self, x1, x2, y, 18 if active else 16, "#1f3a3a")
        _pill(self, x1, self._xb, y, 8, "#3f6b67")
        _pill(self, x1, max(x1, xp), y, 8, self.accent)
        handle = 11 if active else 9
        self.create_line(xp, y - handle, xp, y + handle, width=6, fill="#ffffff", capstyle=tk.ROUND)

    def _draw_segments(self, x1, x2, y, xp, _frac, active):
        gap = 5
        n = max(8, min(40, int((x2 - x1) // 30)))
        seg = ((x2 - x1) - gap * (n - 1)) / n
        thick = 8 if active else 6
        hover_seg = None
        if self._hover_x is not None:
            hover_seg = int((self._frac_at(self._hover_x) * n) // 1)
        for i in range(n):
            sx = x1 + i * (seg + gap)
            ex = sx + seg
            # Pontas arredondadas avançam thick/2 além da linha: compensa para manter o espaçamento.
            inset = thick / 2
            lit = i == hover_seg
            loaded = self._xb >= ex
            base = "#6a6a6a" if lit else "#5a5248" if loaded else "#3a3a3a"
            _pill(self, sx + inset, ex - inset, y, thick, base)
            if xp >= ex:
                _pill(self, sx + inset, ex - inset, y, thick, "#fbbf24" if lit else self.accent)
            elif xp > sx:
                _pill(self, sx + inset, max(sx + inset, xp - inset), y, thick, self.accent)

    def _draw_dots(self, x1, x2, y, xp, _frac, active):
        spacing = 9
        n = int((x2 - x1) // spacing) + 1
        for i in range(n):
            x = x1 + i * spacing
            if x <= xp:
                _circle(self, x, y, 3, fill=self.accent, outline="")
            elif x <= self._xb:
                _circle(self, x, y, 2.6, fill="#8a6a7c", outline="")
            else:
                _circle(self, x, y, 2.2, fill="#3e3e3e", outline="")
        glow = 11 if active else 9
        _circle(self, xp, y, glow, fill="#3a1a2c", outline="")
        _circle(self, xp, y, 6, fill=self.accent, outline="#ffffff", width=2)

    def _draw_gradient(self, x1, x2, y, xp, frac, active):
        thick = 9 if active else 6
        _pill(self, x1, x2, y, thick, "#2a2a2a")
        _pill(self, x1, self._xb, y, thick, "#4a4a58")
        steps = max(1, int((xp - x1) // 6))
        for i in range(steps):
            sx = x1 + (xp - x1) * i / steps
            ex = x1 + (xp - x1) * (i + 1) / steps
            _pill(self, sx, ex, y, thick, _gradient_at((sx - x1) / max(1, x2 - x1)))
        _circle(self, xp, y, 8 if active else 6, fill=_gradient_at(frac), outline="#ffffff", width=2)


class VideoCanvas(tk.Canvas):
    """Área de vídeo com indicador de carregamento e ícone de pausa sobrepostos no centro."""

    SPINNER_DOTS = 12

    def __init__(self, master, placeholder: str):
        super().__init__(master, bg=PLAYER_BG, highlightthickness=0)
        self.size = (960, 540)
        self.accent = TIMELINE_STYLES["pill"][1]
        self._photo = None
        self._image_item = self.create_image(0, 0, anchor="center")
        self._placeholder = self.create_text(0, 0, text=placeholder, fill="#777777", font=("Helvetica", 14),
                                             justify="center")
        self._buffering = False
        self._spin_step = 0
        self._spin_job = None
        self._center_icon: str | None = None
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event) -> None:
        self.size = (max(160, event.width), max(90, event.height))
        cx, cy = event.width / 2, event.height / 2
        self.coords(self._image_item, cx, cy)
        self.coords(self._placeholder, cx, cy)
        self._draw_overlay()

    def show_frame(self, image: Image.Image) -> None:
        self._photo = ImageTk.PhotoImage(image)
        self.itemconfigure(self._image_item, image=self._photo)
        self.itemconfigure(self._placeholder, state="hidden")
        self.tag_raise("overlay")

    def reset(self) -> None:
        self._photo = None
        self.itemconfigure(self._image_item, image="")
        self.itemconfigure(self._placeholder, state="normal")
        self.set_buffering(False)
        self.set_center_icon(None)

    def set_accent(self, color: str) -> None:
        self.accent = color
        self._draw_overlay()

    def set_buffering(self, buffering: bool) -> None:
        if buffering == self._buffering:
            return
        self._buffering = buffering
        if buffering and self._spin_job is None:
            self._spin()
        elif not buffering and self._spin_job is not None:
            self.after_cancel(self._spin_job)
            self._spin_job = None
        self._draw_overlay()

    def set_center_icon(self, icon: str | None) -> None:
        """icon: "play", "replay" ou None."""
        if icon != self._center_icon:
            self._center_icon = icon
            self._draw_overlay()

    def _spin(self) -> None:
        self._spin_step = (self._spin_step + 1) % self.SPINNER_DOTS
        self._draw_overlay()
        self._spin_job = self.after(70, self._spin)

    def _draw_overlay(self) -> None:
        self.delete("overlay")
        # O texto de instrução só aparece quando não há imagem nem carregamento.
        self.itemconfigure(self._placeholder,
                           state="normal" if self._photo is None and not self._buffering else "hidden")
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy = w / 2, h / 2
        if self._buffering:
            _circle(self, cx, cy, 46, fill="#000000", outline="#262626", width=1, tags="overlay")
            for i in range(self.SPINNER_DOTS):
                # O ponto "cabeça" é o maior e mais claro; a cauda desbota.
                age = (self._spin_step - i) % self.SPINNER_DOTS
                fade = 1 - age / self.SPINNER_DOTS
                angle = 2 * math.pi * i / self.SPINNER_DOTS - math.pi / 2
                x, y = cx + 26 * math.cos(angle), cy + 26 * math.sin(angle)
                _circle(self, x, y, 1.6 + 3.4 * fade, fill=_mix("#333333", self.accent, fade), outline="",
                        tags="overlay")
            self.create_text(cx, cy + 62, text="Carregando…", fill="#bbbbbb", font=("Helvetica", 12),
                             tags="overlay")
        elif self._center_icon:
            _circle(self, cx, cy, 38, fill="#000000", outline=self.accent, width=3, tags="overlay")
            if self._center_icon == "play":
                self.create_polygon(cx - 11, cy - 16, cx - 11, cy + 16, cx + 17, cy, fill="#ffffff",
                                    outline="#ffffff", joinstyle=tk.ROUND, width=4, tags="overlay")
            else:
                self.create_arc(cx - 16, cy - 16, cx + 16, cy + 16, start=60, extent=290, style=tk.ARC,
                                outline="#ffffff", width=5, tags="overlay")
                self.create_polygon(cx + 4, cy - 23, cx + 15, cy - 13, cx + 2, cy - 9, fill="#ffffff",
                                    outline="#ffffff", joinstyle=tk.ROUND, width=2, tags="overlay")


# Teclas de mídia (keysyms do X11) e a ação no player; usadas também na tela cheia.
MEDIA_KEYS = {
    "XF86AudioPlay": lambda app: app.toggle_pause(),
    "XF86AudioPause": lambda app: app.toggle_pause(),
    "XF86AudioForward": lambda app: app._seek_relative(10),
    "XF86AudioRewind": lambda app: app._seek_relative(-10),
}
