"""Atlas Runner — native host-процесс запуска CLI (Master Spec §13).

Runner общается с Core через Unix domain socket с request-token, принимает
только argv-массив (не shell-строку), проверяет allowlist каталогов и
исполняемых файлов, стримит нормализованные redacted-события, поддерживает
heartbeat/timeout/interrupt и ведёт минимальный recovery journal.
"""

__all__ = ["__version__"]
__version__ = "0.0.0-vp0"
