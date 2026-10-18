"""Embaralhamento de áudio: inversão espectral exata + embaralhamento temporal.

Inversão espectral: a 24 kHz, multiplicar as amostras por (-1)^n espelha o
espectro em torno de 6 kHz (f -> 12 kHz - f). Graves viram agudos e vice-versa,
a voz fica irreconhecível, e a operação é a própria inversa — sem a perda e o
ruído residual da modulação em anel por cosseno. Toda a energia fica abaixo de
12 kHz, faixa que o AAC/Opus do YouTube preserva.

Embaralhamento temporal: cada bloco de 5 s é dividido em 20 fatias de 250 ms
reordenadas pela senha. Como as fatias têm número par de amostras, a inversão
e a permutação comutam, então encode e decode usam a mesma função.
"""
import numpy as np

AUDIO_RATE = 24000
CHANNELS = 2
SLICE_SAMPLES = AUDIO_RATE // 4          # 250 ms
GROUP_SIZE = 20
BLOCK_SAMPLES = SLICE_SAMPLES * GROUP_SIZE  # 5 s
AUDIO_LABEL = f"audio:{GROUP_SIZE}x{SLICE_SAMPLES}"


def spectral_invert(samples: np.ndarray) -> np.ndarray:
    out = samples.copy()
    out[1::2] *= -1
    return out


def pad_block(samples: np.ndarray) -> np.ndarray:
    """Completa com silêncio até BLOCK_SAMPLES (o último bloco do arquivo)."""
    missing = BLOCK_SAMPLES - len(samples)
    if missing <= 0:
        return samples
    return np.concatenate([samples, np.zeros((missing, samples.shape[1]), dtype=samples.dtype)])


def transform_audio_block(block: np.ndarray, index_map: np.ndarray) -> np.ndarray:
    """Encode com `perm`, decode com `inv_perm`. `block` tem formato (BLOCK_SAMPLES, canais)."""
    channels = block.shape[1]
    slices = spectral_invert(block).reshape(GROUP_SIZE, SLICE_SAMPLES, channels)
    return slices[index_map].reshape(BLOCK_SAMPLES, channels)


def to_int16(samples: np.ndarray) -> np.ndarray:
    return np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)


HOST_RATE = AUDIO_RATE * 2  # 48 kHz: taxa do arquivo enviado ao YouTube e lida no decode

_HALFBAND_TAPS = 2401


def _halfband_filter() -> np.ndarray:
    """Passa-baixas em 12 kHz (a 48 kHz) com transição de ~200 Hz e ~90 dB de rejeição."""
    m = np.arange(_HALFBAND_TAPS) - (_HALFBAND_TAPS - 1) / 2
    h = 0.5 * np.sinc(0.5 * m) * np.kaiser(_HALFBAND_TAPS, 9.0)
    return h / h.sum()


class HalfbandResampler:
    """Conversão exata 2x entre 24 kHz e 48 kHz, contínua entre blocos e com atraso compensado.

    Por que não usar o reamostrador do FFmpeg: a inversão espectral leva os graves
    da voz para logo abaixo de 12 kHz, justamente a borda onde o filtro padrão do
    FFmpeg (corte em ~11,6 kHz) atenua e deixa vazar imagens. Isso destruía os
    graves e deixava um "fantasma" do áudio embaralhado após o decode. Este filtro
    longo preserva o sinal até ~11,9 kHz. Do lado do YouTube (44,1/48 kHz), 12 kHz
    fica no meio da banda e é preservado normalmente.
    """

    _filter: np.ndarray | None = None
    DELAY = (_HALFBAND_TAPS - 1) // 2  # em amostras a 48 kHz

    def __init__(self, direction: str, channels: int = CHANNELS):
        if direction not in ("up", "down"):
            raise ValueError(direction)
        if HalfbandResampler._filter is None:
            HalfbandResampler._filter = _halfband_filter()
        self._h = HalfbandResampler._filter
        self._up = direction == "up"
        self._channels = channels
        self._tail = np.zeros((len(self._h) - 1, channels))
        self._skip = self.DELAY      # descarta o atraso do filtro: a saída fica alinhada com a entrada
        self._emitted = 0            # amostras a 48 kHz já passadas (paridade da dizimação)
        self._spectra: dict[int, np.ndarray] = {}

    def _fir(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self._tail, x])
        self._tail = buf[-(len(self._h) - 1):]
        size = 1 << (len(buf) - 1).bit_length()
        if size not in self._spectra:
            self._spectra[size] = np.fft.rfft(self._h, size)
        y = np.fft.irfft(np.fft.rfft(buf, size, axis=0) * self._spectra[size][:, None], size, axis=0)
        return y[len(self._h) - 1:len(buf)]

    def process(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) == 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        if self._up:
            stuffed = np.zeros((len(samples) * 2, self._channels))
            stuffed[::2] = samples
            y = 2 * self._fir(stuffed)
        else:
            y = self._fir(np.asarray(samples, dtype=np.float64))
        cut = min(self._skip, len(y))
        y, self._skip = y[cut:], self._skip - cut
        if not self._up:
            first = (-self._emitted) % 2
            self._emitted += len(y)
            y = y[first::2]
        return y.astype(np.float32)

    def flush(self) -> np.ndarray:
        """Entrega o final retido pelo atraso do filtro."""
        pad = self.DELAY // 2 if self._up else self.DELAY
        return self.process(np.zeros((pad, self._channels), dtype=np.float32))


class BoundaryDeclicker:
    """Remove cliques nas fronteiras das fatias de 250 ms após o decode.

    No áudio embaralhado cada fronteira é um corte brusco; o codec do YouTube
    espalha um pouco de erro em volta dele. Em ~1% das fronteiras esse erro vira
    um clique audível depois de desembaralhar. Aqui, só nos ±2 ms de cada
    fronteira, um pico de alta frequência muito acima do entorno é substituído
    por uma versão suavizada (passa-baixas local com transição em cosseno). O
    resto do áudio não é tocado.

    Processa em fluxo: a saída fica atrasada HOLD amostras (para enxergar os dois
    lados da fronteira entre blocos), mas as posições continuam alinhadas: a
    primeira chamada entrega HOLD amostras a menos e flush() entrega o restante.
    """

    HALF = 48            # ±2 ms a 24 kHz
    CONTEXT = 1200       # 50 ms de cada lado para medir o "normal" do trecho
    THRESHOLD = 8.0      # quantas vezes acima do entorno conta como clique
    HOLD = CONTEXT + HALF + 2
    _SMOOTH = np.hanning(11)[1:-1] / np.hanning(11)[1:-1].sum()   # passa-baixas ~2,5 kHz
    _FADE = np.sin(np.linspace(0, np.pi, 2 * HALF)) ** 2           # 0 nas bordas, 1 no centro

    def __init__(self, channels: int = CHANNELS):
        self._buf = np.zeros((0, channels), dtype=np.float32)
        self._start = 0          # posição absoluta de _buf[0]
        self._emitted = 0        # amostras já entregues
        self._next = SLICE_SAMPLES   # próxima fronteira a examinar (cada uma só uma vez)
        self.repaired = 0

    def _repair(self, upto: int) -> None:
        """Examina as fronteiras cujo contexto completo já está em _buf, até a posição `upto`."""
        while self._next <= upto:
            b, self._next = self._next, self._next + SLICE_SAMPLES
            i = b - self._start
            if i - self.CONTEXT < 0 or i + self.CONTEXT > len(self._buf):
                continue
            mono = self._buf[i - self.CONTEXT:i + self.CONTEXT].mean(axis=1)
            hp = np.abs(np.diff(mono, 2))
            mid = self.CONTEXT - 1
            peak = hp[mid - self.HALF:mid + self.HALF].max()
            if peak <= self.THRESHOLD * (np.median(hp) + 1e-9):
                continue
            seg = self._buf[i - self.HALF - 8:i + self.HALF + 8]
            # Mediana remove o impulso; o passa-baixas alisa o que sobrou.
            median = np.median(np.lib.stride_tricks.sliding_window_view(seg, 9, axis=0), axis=-1)
            median = np.concatenate([seg[:4], median, seg[-4:]])
            smooth = np.stack([np.convolve(median[:, c], self._SMOOTH, mode="same") for c in range(seg.shape[1])], 1)
            w = self._FADE[:, None]
            self._buf[i - self.HALF:i + self.HALF] = (1 - w) * seg[8:-8] + w * smooth[8:-8]
            self.repaired += 1

    def process(self, samples: np.ndarray) -> np.ndarray:
        self._buf = np.concatenate([self._buf, samples.astype(np.float32)])
        end = self._start + len(self._buf)
        self._repair(end - self.CONTEXT)
        ready = max(0, end - self.HOLD - self._emitted)
        return self._take(ready)

    def flush(self) -> np.ndarray:
        return self._take(self._start + len(self._buf) - self._emitted)

    def _take(self, n: int) -> np.ndarray:
        offset = self._emitted - self._start
        out = self._buf[offset:offset + n].copy()
        self._emitted += n
        # Guarda só o contexto necessário para as próximas fronteiras.
        keep_from = max(0, self._emitted - self._start - self.HOLD)
        self._buf, self._start = self._buf[keep_from:], self._start + keep_from
        return out


class BlockAssembler:
    """Junta pedaços de áudio de tamanhos variados em blocos exatos de BLOCK_SAMPLES."""

    def __init__(self, channels: int = CHANNELS):
        self._parts: list[np.ndarray] = []
        self._size = 0
        self._channels = channels

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        self._parts.append(samples)
        self._size += len(samples)
        if self._size < BLOCK_SAMPLES:
            return []
        data = np.concatenate(self._parts)
        n_full = len(data) // BLOCK_SAMPLES
        blocks = [data[i * BLOCK_SAMPLES:(i + 1) * BLOCK_SAMPLES] for i in range(n_full)]
        rest = data[n_full * BLOCK_SAMPLES:]
        self._parts, self._size = [rest], len(rest)
        return blocks

    def remainder(self) -> np.ndarray | None:
        """Último bloco parcial completado com silêncio (None se não sobrou nada)."""
        if self._size == 0:
            return None
        return pad_block(np.concatenate(self._parts))
