"""Сортируемые идентификаторы и UTC-временные метки.

VP-0 не тянет внешние зависимости, поэтому используется компактная
лексикографически-сортируемая схема ID (время + случайный суффикс),
совместимая по духу с требованием раздела 23.1 «sortable ID».
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, без I L O U


def _base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Вернуть сортируемый ID вида ``prefix_<time><rand>``.

    Первая часть кодирует миллисекунды эпохи (монотонно растёт), вторая —
    случайность для уникальности внутри одной миллисекунды.
    """

    now_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(5), "big")
    return f"{prefix}_{_base32(now_ms, 9)}{_base32(rand, 8)}"


def utcnow_iso() -> str:
    """ISO-8601 UTC с суффиксом ``Z`` (стабильный формат для событий)."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
