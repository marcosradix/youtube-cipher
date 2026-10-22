"""Embaralhamento de áudio: inversão espectral exata + embaralhamento temporal.

Há dois formatos. Vídeos novos usam o v3; o v2 continua sendo decodificado
(detect_scheme descobre qual é pelo próprio áudio).

v3 (desde a 2.3) — fatias com margens: o áudio original trabalha a 20 kHz e cada
fatia de 250 ms ganha 25 ms de margem de cada lado, feita com o próprio som da
fatia espelhado e desvanecendo até zero. O corte entre fatias acontece no
silêncio, longe do conteúdo: o codec do YouTube não vê transiente e não deixa
os pequenos estouros de ruído (~30 ms) que o v2 deixava em volta de cada corte
— ouvidos como cliques. As margens não contêm nada das fatias vizinhas, então
não revelam a ordem. Com as margens, cada fatia ocupa exatamente 250 ms no
fluxo a 24 kHz: a duração do áudio continua igual à do vídeo. Custo: banda do
áudio de 10 kHz (no v2, 12 kHz).

v2 — descrito abaixo, sem margens:

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


def transform_audio_block(block: np.ndarray, index_map: np.ndarray) -> np.ndarray:
    """Encode com `perm`, decode com `inv_perm`. `block` tem formato (BLOCK_SAMPLES, canais)."""
    channels = block.shape[1]
    slices = spectral_invert(block).reshape(GROUP_SIZE, SLICE_SAMPLES, channels)
    return slices[index_map].reshape(BLOCK_SAMPLES, channels)


# ------------------------------------------------------------------
# Formato v3: fatias com margens espelhadas
# ------------------------------------------------------------------

CORE_RATE = 20000                                        # taxa do áudio original no v3
CORE_SAMPLES = CORE_RATE // 4                            # 250 ms de áudio original por fatia
GUARD_SAMPLES = (SLICE_SAMPLES - CORE_SAMPLES) // 2      # 500 amostras = 25 ms de cada lado
CORE_BLOCK = CORE_SAMPLES * GROUP_SIZE                   # 5 s a 20 kHz
AUDIO_LABEL_V3 = f"audio-v3:{GROUP_SIZE}x{CORE_SAMPLES}"
_GUARD_IN = (np.sin(np.linspace(0, np.pi / 2, GUARD_SAMPLES)) ** 2)[:, None]   # 0 no corte -> 1 na fatia
_GUARD_OUT = _GUARD_IN[::-1]


def encode_block_v3(block: np.ndarray, perm: np.ndarray) -> np.ndarray:
    """(CORE_BLOCK, canais) a 20 kHz -> (BLOCK_SAMPLES, canais) no fluxo a 24 kHz."""
    channels = block.shape[1]
    slices = spectral_invert(block).reshape(GROUP_SIZE, CORE_SAMPLES, channels)[perm]
    left = slices[:, :GUARD_SAMPLES][:, ::-1] * _GUARD_IN
    right = slices[:, -GUARD_SAMPLES:][:, ::-1] * _GUARD_OUT
    return np.concatenate([left, slices, right], axis=1).reshape(BLOCK_SAMPLES, channels)


def decode_block_v3(block: np.ndarray, inv: np.ndarray) -> np.ndarray:
    """(BLOCK_SAMPLES, canais) do fluxo a 24 kHz -> (CORE_BLOCK, canais) a 20 kHz. Descarta as margens."""
    channels = block.shape[1]
    slots = block.reshape(GROUP_SIZE, SLICE_SAMPLES, channels)
    cores = slots[:, GUARD_SAMPLES:GUARD_SAMPLES + CORE_SAMPLES][inv]
    return spectral_invert(cores.reshape(CORE_BLOCK, channels))


def detect_scheme(block: np.ndarray) -> str:
    """"v3" ou "v2", pela presença das margens espelhadas num bloco do fluxo a 24 kHz."""
    mono = block.mean(axis=1).reshape(GROUP_SIZE, SLICE_SAMPLES)
    guard = mono[:, :GUARD_SAMPLES][:, ::-1]                              # = início da fatia × rampa
    expected = mono[:, GUARD_SAMPLES:2 * GUARD_SAMPLES] * _GUARD_IN[::-1, 0]
    num = np.sum(guard * expected)
    den = np.sqrt(np.sum(guard ** 2) * np.sum(expected ** 2)) + 1e-12
    return "v3" if num / den > 0.5 else "v2"


def find_block_start(samples: np.ndarray, max_shift: int) -> int | None:
    """Onde começa um bloco v3 em `samples` (fluxo a 24 kHz), procurando de 0 a `max_shift`.

    As margens espelhadas de cada fatia marcam o corte com precisão de uma amostra. Depois de um
    salto, o áudio lido pode começar alguns milissegundos fora do ponto pedido; com as fatias
    desencaixadas, cada emenda vira um estalo. Devolve None se o trecho não permitir medir (silêncio).
    """
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    if len(mono) < max_shift + BLOCK_SAMPLES:
        return None
    ramp = _GUARD_IN[::-1, 0]
    starts = np.arange(GROUP_SIZE) * SLICE_SAMPLES
    idx = starts[:, None] + np.arange(GUARD_SAMPLES)[None, :]
    best, best_shift = 0.0, None
    for shift in range(max_shift + 1):
        guard = mono[shift + idx][:, ::-1]
        expected = mono[shift + GUARD_SAMPLES + idx] * ramp
        num = np.sum(guard * expected)
        den = np.sqrt(np.sum(guard ** 2) * np.sum(expected ** 2)) + 1e-12
        if num / den > best:
            best, best_shift = num / den, shift
    return best_shift if best > 0.8 else None


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
    fronteira, um pico de alta frequência enorme (THRESHOLD vezes o entorno) e
    dominante (o maior transiente da região) é suavizado em ±1 ms. O resto do
    áudio não é tocado. No áudio real de um vídeo de teste, isso dá ~4 correções
    por minuto e deixa as fronteiras com a mesma taxa de picos do resto do som.

    Precisa ser conservador: corrigir fronteiras demais apaga os agudos em
    trechos de 2 ms várias vezes por segundo, e o ouvido percebe cada um como um
    "tique" — pior que o clique original.

    Processa em fluxo: a saída fica atrasada HOLD amostras (para enxergar os dois
    lados da fronteira entre blocos), mas as posições continuam alinhadas: a
    primeira chamada entrega HOLD amostras a menos e flush() entrega o restante.
    """

    DETECT = 48          # procura o pico em ±2 ms da fronteira
    HALF = 24            # corrige ±1 ms em volta do pico
    CONTEXT = 1200       # 50 ms de cada lado para medir o "normal" do trecho
    THRESHOLD = 20.0     # pico precisa ser 20x a mediana do entorno...
    DOMINANCE = 1.2      # ...e maior que qualquer transiente fora da janela de detecção
    HOLD = CONTEXT + DETECT + HALF + 10
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
            window = hp[mid - self.DETECT:mid + self.DETECT]
            outside = np.concatenate([hp[:mid - self.DETECT], hp[mid + self.DETECT:]])
            peak = window.max()
            if peak <= self.THRESHOLD * (np.median(hp) + 1e-9) or peak <= self.DOMINANCE * outside.max():
                continue
            i += int(np.argmax(window)) - self.DETECT + 1     # centraliza a correção no pico
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
    """Junta pedaços de áudio de tamanhos variados em blocos exatos (BLOCK_SAMPLES por padrão)."""

    def __init__(self, block_size: int = BLOCK_SAMPLES):
        self._parts: list[np.ndarray] = []
        self._size = 0
        self._block = block_size

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        self._parts.append(samples)
        self._size += len(samples)
        if self._size < self._block:
            return []
        data = np.concatenate(self._parts)
        n_full = len(data) // self._block
        blocks = [data[i * self._block:(i + 1) * self._block] for i in range(n_full)]
        rest = data[n_full * self._block:]
        self._parts, self._size = [rest], len(rest)
        return blocks

    def remainder(self) -> np.ndarray | None:
        """Último bloco parcial completado com silêncio (None se não sobrou nada)."""
        if self._size == 0:
            return None
        data = np.concatenate(self._parts)
        return np.concatenate([data, np.zeros((self._block - len(data), data.shape[1]), dtype=data.dtype)])


class RationalResampler:
    """Conversão de taxa por fração up/down (ex.: 20 -> 48 kHz = 12/5), contínua entre blocos.

    Usada só no domínio do áudio já decodificado, onde nada importante fica na
    borda da banda; um FIR de Kaiser moderado basta. A saída fica alinhada com a
    entrada (atraso do filtro compensado).
    """

    def __init__(self, up: int, down: int, channels: int = CHANNELS, taps_per_phase: int = 96):
        self._up, self._down = up, down
        taps = taps_per_phase * max(up, down) + 1
        cutoff = 0.92 / max(up, down)              # fração da Nyquist da taxa intermediária
        m = np.arange(taps) - (taps - 1) / 2
        h = cutoff * np.sinc(cutoff * m) * np.kaiser(taps, 8.0)
        self._h = up * h / h.sum()                 # ganho `up` compensa os zeros inseridos
        self._tail = np.zeros((taps - 1, channels))
        self._skip = (taps - 1) // 2
        self._emitted = 0
        self._channels = channels
        self._spectra: dict[int, np.ndarray] = {}

    def process(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) == 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        stuffed = np.zeros((len(samples) * self._up, self._channels))
        stuffed[::self._up] = samples
        buf = np.concatenate([self._tail, stuffed])
        self._tail = buf[-(len(self._h) - 1):]
        size = 1 << (len(buf) - 1).bit_length()
        if size not in self._spectra:
            self._spectra[size] = np.fft.rfft(self._h, size)
        y = np.fft.irfft(np.fft.rfft(buf, size, axis=0) * self._spectra[size][:, None], size, axis=0)
        y = y[len(self._h) - 1:len(buf)]
        cut = min(self._skip, len(y))
        y, self._skip = y[cut:], self._skip - cut
        first = (-self._emitted) % self._down
        self._emitted += len(y)
        return y[first::self._down].astype(np.float32)

    def flush(self) -> np.ndarray:
        pad = -(-(len(self._h) - 1) // 2 // self._up) + 1
        return self.process(np.zeros((pad, self._channels), dtype=np.float32))


class StreamEncoder:
    """Áudio original a 20 kHz -> fluxo embaralhado v3 a 48 kHz, em blocos de qualquer tamanho."""

    def __init__(self, perm_v3: np.ndarray):
        self._perm = perm_v3
        self._asm = BlockAssembler(CORE_BLOCK)
        self._up = HalfbandResampler("up")

    def process(self, samples: np.ndarray) -> np.ndarray:
        blocks = self._asm.push(samples)
        return self._emit(blocks)

    def flush(self) -> np.ndarray:
        """O último bloco é completado com silêncio: nada do final do áudio é descartado."""
        last = self._asm.remainder()
        out = self._emit([last] if last is not None else [])
        return np.concatenate([out, self._up.flush()])

    def _emit(self, blocks: list[np.ndarray]) -> np.ndarray:
        parts = [self._up.process(encode_block_v3(b, self._perm)) for b in blocks]
        return np.concatenate(parts) if parts else np.zeros((0, CHANNELS), dtype=np.float32)


class StreamDecoder:
    """Fluxo embaralhado a 48 kHz -> áudio original, detectando o formato (v2/v3) no primeiro bloco.

    `rate` fica definido após o primeiro bloco: 20 kHz (v3) ou 24 kHz (v2).
    `scheme` dado de antemão pula a detecção.
    """

    def __init__(self, inv_v2: np.ndarray, inv_v3: np.ndarray, scheme: str | None = None):
        self._inv_v2, self._inv_v3 = inv_v2, inv_v3
        self._down = HalfbandResampler("down")
        self._asm = BlockAssembler(BLOCK_SAMPLES)
        self._declicker = BoundaryDeclicker()
        self.scheme: str | None = None
        self.rate: int | None = None
        if scheme is not None:     # já conhecido (ex.: detectado no início do vídeo): não detecta de novo
            self._set_scheme(scheme)

    def _set_scheme(self, scheme: str) -> None:
        self.scheme = scheme
        self.rate = CORE_RATE if scheme == "v3" else AUDIO_RATE

    def process(self, samples48: np.ndarray) -> np.ndarray:
        return self._decode(self._asm.push(self._down.process(samples48)))

    def flush(self) -> np.ndarray:
        blocks = self._asm.push(self._down.flush())
        last = self._asm.remainder()
        out = self._decode(blocks + ([last] if last is not None else []))
        if self.scheme == "v2":
            out = np.concatenate([out, self._declicker.flush()])
        return out

    def _decode(self, blocks: list[np.ndarray]) -> np.ndarray:
        parts = []
        for block in blocks:
            if self.scheme is None:
                self._set_scheme(detect_scheme(block))
            if self.scheme == "v3":
                parts.append(decode_block_v3(block, self._inv_v3).astype(np.float32))
            else:
                parts.append(self._declicker.process(transform_audio_block(block, self._inv_v2)))
        return np.concatenate(parts) if parts else np.zeros((0, CHANNELS), dtype=np.float32)
