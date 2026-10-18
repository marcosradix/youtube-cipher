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
