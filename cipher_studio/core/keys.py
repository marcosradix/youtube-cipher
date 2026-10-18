"""Derivação de chave e permutações determinísticas.

Usa apenas hashlib/hmac da biblioteca padrão: um vídeo codificado hoje precisa
continuar decodificável daqui a anos, independentemente da versão do NumPy
(cujo gerador aleatório não garante a mesma sequência entre versões).
"""
import hashlib
import hmac

import numpy as np

SCHEME_VERSION = "v2"
_SALT = b"youtube-cipher-studio/" + SCHEME_VERSION.encode()


def derive_key(password: str) -> bytes:
    """Deriva uma chave de 256 bits da senha com scrypt (lento de propósito contra força bruta)."""
    if not password:
        raise ValueError("A senha não pode ser vazia.")
    return hashlib.scrypt(password.encode("utf-8"), salt=_SALT, n=2**14, r=8, p=1, dklen=32)


class _KeyStream:
    """Fluxo pseudoaleatório HMAC-SHA256 em modo contador."""

    def __init__(self, key: bytes, label: str):
        self._key = key
        self._label = label.encode("utf-8")
        self._counter = 0
        self._buf = b""

    def _next_u32(self) -> int:
        if len(self._buf) < 4:
            block = hmac.new(self._key, self._label + self._counter.to_bytes(8, "big"), hashlib.sha256)
            self._buf += block.digest()
            self._counter += 1
        value = int.from_bytes(self._buf[:4], "big")
        self._buf = self._buf[4:]
        return value

    def below(self, n: int) -> int:
        """Inteiro uniforme em [0, n) — amostragem por rejeição, sem viés de módulo."""
        limit = (1 << 32) - ((1 << 32) % n)
        while True:
            value = self._next_u32()
            if value < limit:
                return value % n


def permutation(n_elements: int, key: bytes, label: str) -> tuple[np.ndarray, np.ndarray]:
    """Gera o mapa de embaralhamento (encode) e o mapa inverso exato (decode).

    O `label` separa domínios (vídeo, áudio, grades diferentes) para que a mesma
    senha não produza permutações relacionadas entre si.
    """
    perm = list(range(n_elements))
    stream = _KeyStream(key, label)
    for i in range(n_elements - 1, 0, -1):
        j = stream.below(i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    perm_arr = np.array(perm, dtype=np.intp)
    return perm_arr, np.argsort(perm_arr)
