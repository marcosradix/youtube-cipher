"""Embaralhamento de quadros por macroblocos.

A cifra é definida por uma GRADE fixa (colunas × linhas), não por um tamanho de
bloco em pixels. Assim o mesmo vídeo pode ser decodificado em qualquer resolução
que o YouTube entregar (360p, 720p, 1080p, 4K...): o quadro é redimensionado
para um múltiplo da grade e a permutação continua a mesma.
"""
from dataclasses import dataclass

import numpy as np

# Todas as grades dividem exatamente 640x360, 1280x720, 1920x1080, 2560x1440 e 3840x2160.
GRID_PRESETS = {
    "32x18": (32, 18),
    "64x36": (64, 36),
    "80x45": (80, 45),
}

# Lado menor do quadro (altura em vídeos horizontais, largura em verticais).
RESOLUTION_PRESETS = {
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
}


@dataclass(frozen=True)
class Geometry:
    cols: int
    rows: int
    block: int

    @property
    def width(self) -> int:
        return self.cols * self.block

    @property
    def height(self) -> int:
        return self.rows * self.block

    @property
    def n_blocks(self) -> int:
        return self.cols * self.rows

    @property
    def label(self) -> str:
        """Identificador de domínio da permutação de vídeo."""
        return f"video:{self.cols}x{self.rows}"


@dataclass(frozen=True)
class PlainSize:
    """Tamanho de exibição de um vídeo comum (sem cifra): não há grade, só largura e altura."""
    width: int
    height: int


def plain_size(src_w: int, src_h: int, display_width: int) -> PlainSize:
    """Cabe na largura da tela mantendo a proporção, sem ampliar; dimensões pares (H.264)."""
    w = min(max(160, int(display_width)), src_w)
    h = round(src_h * w / src_w)
    return PlainSize(w - w % 2, max(2, h - h % 2))


def _oriented_grid(grid: str, portrait: bool) -> tuple[int, int]:
    if grid not in GRID_PRESETS:
        raise ValueError(f"Grade desconhecida: {grid}")
    cols, rows = GRID_PRESETS[grid]
    return (rows, cols) if portrait else (cols, rows)


def _even_block(block: int) -> int:
    # yuv420p exige dimensões pares; com bloco par isso vale para qualquer grade.
    return max(2, block - block % 2)


def encode_geometry(src_w: int, src_h: int, grid: str, resolution: str) -> Geometry:
    """Geometria de saída do encode. `resolution` = preset ou "Auto" (resolução da origem)."""
    portrait = src_h > src_w
    cols, rows = _oriented_grid(grid, portrait)
    short_side = min(src_w, src_h) if resolution == "Auto" else RESOLUTION_PRESETS[resolution]
    return Geometry(cols, rows, _even_block(short_side // min(cols, rows)))


def decode_geometry(src_w: int, src_h: int, grid: str, max_block: int | None = None) -> Geometry:
    """Geometria de trabalho do decode: o múltiplo da grade mais próximo da resolução recebida."""
    portrait = src_h > src_w
    cols, rows = _oriented_grid(grid, portrait)
    block = round(min(src_w, src_h) / min(cols, rows))
    if max_block:
        block = min(block, max_block)
    return Geometry(cols, rows, _even_block(block))


def _to_blocks(frame: np.ndarray, g: Geometry) -> np.ndarray:
    blocks = frame.reshape(g.rows, g.block, g.cols, g.block, 3)
    return blocks.swapaxes(1, 2).reshape(g.n_blocks, g.block, g.block, 3)


def _from_blocks(blocks: np.ndarray, g: Geometry) -> np.ndarray:
    grid = blocks.reshape(g.rows, g.cols, g.block, g.block, 3)
    return grid.swapaxes(1, 2).reshape(g.height, g.width, 3)


def transform_frame(frame: np.ndarray, g: Geometry, index_map: np.ndarray, invert_colors: bool) -> np.ndarray:
    """Inverte as cores (opcional) e reposiciona os blocos segundo `index_map`.

    Encode usa `perm`, decode usa `inv_perm`; a inversão de cor é a própria inversa.
    O quadro precisa ter exatamente `g.height × g.width`.
    """
    if invert_colors:
        frame = 255 - frame
    return _from_blocks(_to_blocks(frame, g)[index_map], g)


def deblock(frame: np.ndarray, g: Geometry) -> np.ndarray:
    """Suaviza as 2 colunas/linhas de cada emenda entre blocos.

    A compressão do YouTube espalha um pouco de cor entre blocos vizinhos no vídeo
    embaralhado; após o decode essas bordas viram uma grade fina. Um filtro
    [1 2 1] só nas emendas remove a grade sem borrar o resto da imagem.
    """
    out = frame.astype(np.int16)
    xs = np.arange(g.block, g.width, g.block)
    if xs.size:
        a, l, r, d = out[:, xs - 2], out[:, xs - 1], out[:, xs], out[:, xs + 1]
        out[:, xs - 1] = (a + 2 * l + r + 2) // 4
        out[:, xs] = (l + 2 * r + d + 2) // 4
    ys = np.arange(g.block, g.height, g.block)
    if ys.size:
        a, u, b, d = out[ys - 2], out[ys - 1], out[ys], out[ys + 1]
        out[ys - 1] = (a + 2 * u + b + 2) // 4
        out[ys] = (u + 2 * b + d + 2) // 4
    return out.astype(np.uint8)


def seam_ratio(frame: np.ndarray, g: Geometry) -> float | None:
    """Razão entre a variação de cor nas emendas dos blocos e no interior deles.

    Imagem natural ≈ 1; imagem embaralhada ≫ 1. Retorna None em quadros sem
    detalhe suficiente (tela preta, fade) para a medida ser confiável.
    """
    gray = frame.mean(axis=2, dtype=np.float32)
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    seam_x = np.arange(g.block - 1, g.width - 1, g.block)
    seam_y = np.arange(g.block - 1, g.height - 1, g.block)
    inner_x = np.ones(dx.shape[1], dtype=bool)
    inner_x[seam_x] = False
    inner_y = np.ones(dy.shape[0], dtype=bool)
    inner_y[seam_y] = False
    inner = (dx[:, inner_x].mean() + dy[inner_y].mean()) / 2
    if inner < 0.5 or not seam_x.size or not seam_y.size:
        return None
    seam = (dx[:, seam_x].mean() + dy[seam_y].mean()) / 2
    return float(seam / inner)


class DecodeChecker:
    """Detecta senha/parâmetros errados comparando emendas antes e depois do decode."""

    def __init__(self, g: Geometry, samples_needed: int = 6):
        self._g = g
        self._needed = samples_needed
        self._scrambled: list[float] = []
        self._decoded: list[float] = []

    @property
    def done(self) -> bool:
        return len(self._decoded) >= self._needed

    def add(self, scrambled: np.ndarray, decoded: np.ndarray) -> None:
        rs = seam_ratio(scrambled, self._g)
        rd = seam_ratio(decoded, self._g)
        if rs is not None and rd is not None:
            self._scrambled.append(rs)
            self._decoded.append(rd)

    def verdict(self) -> bool | None:
        """True = decode parece correto; False = imagem ainda embaralhada; None = sem dados."""
        if len(self._decoded) < 3:
            return None
        return float(np.median(self._decoded)) < 0.75 * float(np.median(self._scrambled))
