"""Честная модель ёмкости (Master Spec §11.6, §45.4).

Ключевое правило: если стабильного официального interface для остатка
лимита нет — статус ``UNKNOWN``, а не вычисленная фикция. VP-0 намеренно не
заявляет 5h/7d-остаток, потому что провайдеры не отдают его через стабильный
источник.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .ids import utcnow_iso


class CapacityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    LOW = "LOW"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN = "UNKNOWN"


class CapacitySource(str, Enum):
    OFFICIAL_STRUCTURED = "official_structured"
    WRAPPER = "wrapper"
    OBSERVED = "observed"
    MANUAL = "manual"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Capacity:
    status: CapacityStatus
    source: CapacitySource
    observed_at: str
    remaining_5h: int | None = None
    remaining_7d: int | None = None
    reset_at: str | None = None
    confidence: str = "low"

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "source": self.source.value,
            "observed_at": self.observed_at,
            "5h_remaining": self.remaining_5h,
            "7d_remaining": self.remaining_7d,
            "reset_at": self.reset_at,
            "confidence": self.confidence,
        }


def unknown_capacity() -> Capacity:
    """Единственно честный результат для VP-0: остаток не известен."""

    return Capacity(
        status=CapacityStatus.UNKNOWN,
        source=CapacitySource.UNKNOWN,
        observed_at=utcnow_iso(),
        remaining_5h=None,
        remaining_7d=None,
        reset_at=None,
        confidence="none",
    )


# =============================================================================
# VP-7: реальные числовые лимиты подписок из ОФИЦИАЛЬНЫХ CLI-источников (§11.6).
# Codex — App Server (JSON-RPC); Claude — auth status (план) + PTY /usage (окна).
# Никогда не читаем/не копируем токены/cookie/сессии; email/org — redaction на
# границе. Нет числа → точный error_code, а не немой UNKNOWN.
# =============================================================================
import json as _json  # noqa: E402
import os as _os  # noqa: E402
import re as _re  # noqa: E402
import select as _select  # noqa: E402
import subprocess as _sp  # noqa: E402
import threading as _th  # noqa: E402
import time as _time  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
from datetime import timezone as _tz  # noqa: E402

_EMAIL_RE = _re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _now_iso() -> str:
    return _dt.now(_tz.utc).isoformat()


def _safe(text: str, limit: int = 160) -> str:
    return _EMAIL_RE.sub("<redacted>", (text or ""))[:limit]


def _epoch_iso(v) -> str | None:
    try:
        return _dt.fromtimestamp(int(v), tz=_tz.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _base_argv(root: str, run_as_user: str | None, extra: dict) -> list[str]:
    env = {"HOME": root, "PATH": f"/usr/bin:/bin:{root}/.local/bin", "LANG": "C.UTF-8", **extra}
    envargs = [f"{k}={v}" for k, v in env.items()]
    if run_as_user:
        return ["runuser", "-u", run_as_user, "--", "env", "-i", *envargs]
    return ["env", "-i", *envargs]


def _window_label(mins: int | None) -> tuple[str, str]:
    if mins is None:
        return "window", "Окно"
    if mins <= 360:
        return "5h", "Сессия (5 ч)"
    if mins <= 1440:
        return "1d", "Сутки"
    return "7d", "Неделя (7 дн)"


def _mk_window(*, win_id: str, label: str, used_pct, reset_at: str | None,
               window_mins: int | None) -> dict:
    up = None if used_pct is None else round(float(used_pct), 1)
    remaining = None if up is None else max(0.0, round(100.0 - up, 1))
    return {"id": win_id, "label": label, "used_pct": up, "remaining_pct": remaining,
            "reset_at": reset_at, "window_mins": window_mins}


def _cap_error(provider: str, code: str, detail: str, *, plan: str = "",
               auth_ok: bool = False) -> dict:
    return {"provider": provider, "plan": plan, "auth_ok": auth_ok, "windows": [],
            "source": f"{provider}-probe", "checked_at": _now_iso(),
            "error_code": code, "detail": _safe(detail)}


def probe_codex_capacity(root_path: str, *, executable: str, run_as_user: str | None = None,
                         timeout: float = 25.0) -> dict:
    """Реальные лимиты Codex через официальный ``codex app-server`` (JSON-RPC:
    initialize → account/read[planType] → account/rateLimits/read). Только safe-поля."""
    cmd = _base_argv(root_path, run_as_user, {"CODEX_HOME": root_path}) + [executable, "app-server"]
    try:
        p = _sp.Popen(cmd, stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, bufsize=1)
    except Exception as exc:  # noqa: BLE001
        return _cap_error("codex", "CODEX_APPSERVER_SPAWN_FAILED", str(exc))
    responses: dict[int, dict] = {}
    done = _th.Event()

    def reader():
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and "id" in msg and "result" in msg:
                responses[msg["id"]] = msg["result"]
                if 3 in responses:
                    done.set()
                    return

    _th.Thread(target=reader, daemon=True).start()

    def send(obj):
        p.stdin.write(_json.dumps(obj) + "\n")
        p.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "atlas", "title": "Atlas", "version": "1.0"}}})
        _time.sleep(0.4)
        send({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {}})
        _time.sleep(0.2)
        send({"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read", "params": {}})
        done.wait(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return _cap_error("codex", "CODEX_APPSERVER_IO_FAILED", str(exc))
    finally:
        for fn in (lambda: p.stdin.close(), lambda: p.terminate()):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        try:
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    acct = responses.get(2) or {}
    plan = str(((acct.get("account") or {}).get("planType") or "")).strip()
    auth_ok = bool(acct.get("account"))
    rl = (responses.get(3) or {}).get("rateLimits")
    if not rl:
        if not auth_ok:
            return _cap_error("codex", "CODEX_NOT_AUTHENTICATED", "app-server без account")
        return _cap_error("codex", "CODEX_RATELIMITS_EMPTY", "нет rateLimits", plan=plan, auth_ok=True)
    windows = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        mins = w.get("windowDurationMins")
        wid, label = _window_label(mins)
        windows.append(_mk_window(win_id=wid, label=label, used_pct=w.get("usedPercent"),
                                  reset_at=_epoch_iso(w.get("resetsAt")), window_mins=mins))
    reached = rl.get("rateLimitReachedType")
    return {"provider": "codex", "plan": plan or str(rl.get("planType") or ""), "auth_ok": auth_ok,
            "windows": windows, "source": "codex-app-server", "checked_at": _now_iso(),
            "error_code": "" if windows else "CODEX_NO_WINDOWS",
            "detail": f"reached={reached}" if reached else ""}


def probe_claude_capacity(root_path: str, *, executable: str, run_as_user: str | None = None,
                          timeout: float = 30.0) -> dict:
    """План Claude из ``auth status --json`` + окна из bounded PTY ``/usage``.
    Проба read-only: только ``/usage``, без промпта/агента/мутации login."""
    plan, auth_ok = "", False
    try:
        argv = _base_argv(root_path, run_as_user, {"CLAUDE_CONFIG_DIR": root_path}) + \
            [executable, "auth", "status", "--json"]
        out = _sp.run(argv, capture_output=True, text=True, timeout=45, stdin=_sp.DEVNULL)
        data = _json.loads(out.stdout)
        auth_ok = bool(data.get("loggedIn"))
        plan = str(data.get("subscriptionType") or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _cap_error("claude", "CLAUDE_AUTH_STATUS_FAILED", str(exc))
    if not auth_ok:
        return _cap_error("claude", "CLAUDE_NOT_AUTHENTICATED", "не авторизован", plan=plan)
    windows, err, detail = _claude_usage_windows(root_path, executable, run_as_user, timeout)
    return {"provider": "claude", "plan": plan, "auth_ok": True, "windows": windows,
            "source": "claude-usage-tui" if windows else "claude-auth-status",
            "checked_at": _now_iso(), "error_code": err, "detail": _safe(detail)}


def _clean_tty(b: bytearray) -> str:
    t = b.decode("utf-8", "replace")
    t = _re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", t)
    t = _re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", t)
    t = _re.sub(r"\x1b[=>]", "", t)
    t = _re.sub(r"[\x00-\x08\x0b-\x1f]", " ", t)
    return _EMAIL_RE.sub("<redacted>", t)


def _parse_claude_usage(text: str) -> list[dict]:
    windows: list[dict] = []
    for win_id, label, keys, mins in (("5h", "Сессия (5 ч)", ("current session", "session"), 300),
                                       ("7d", "Неделя (7 дн)", ("current week", "week"), 10080)):
        used = None
        reset_txt = None
        for key in keys:
            idx = text.lower().find(key)
            if idx < 0:
                continue
            seg = text[idx:idx + 260]
            m = _re.search(r"(\d{1,3})\s*%", seg)
            if m:
                used = min(100, int(m.group(1)))
            rm = _re.search(r"[Rr]esets?\s+([^\n|]{3,40})", seg)
            if rm:
                reset_txt = rm.group(1).strip()
            break
        if used is not None:
            w = _mk_window(win_id=win_id, label=label, used_pct=used, reset_at=None, window_mins=mins)
            if reset_txt:
                w["reset_text"] = _safe(reset_txt, 40)
            windows.append(w)
    return windows


def _claude_usage_windows(root: str, exe: str, user: str | None, timeout: float):
    import pty
    env = {"HOME": root, "CLAUDE_CONFIG_DIR": root, "PATH": f"/usr/bin:/bin:{root}/.local/bin",
           "TERM": "xterm-256color", "LANG": "C.UTF-8", "COLUMNS": "120", "LINES": "44"}
    envargs = [f"{k}={v}" for k, v in env.items()]
    base = ["runuser", "-u", user, "--", "env", "-i", *envargs] if user else ["env", "-i", *envargs]
    cmd = [*base, "sh", "-c", f"cd {root} && exec {exe}"]
    master, slave = pty.openpty()
    try:
        p = _sp.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    except Exception as exc:  # noqa: BLE001
        _os.close(master)
        _os.close(slave)
        return [], "CLAUDE_USAGE_SPAWN_FAILED", str(exc)
    _os.close(slave)
    buf = bytearray()

    def drain(secs):
        end = _time.time() + secs
        while _time.time() < end:
            r, _, _ = _select.select([master], [], [], 0.3)
            if r:
                try:
                    data = _os.read(master, 65536)
                except OSError:
                    return
                if not data:
                    return
                buf.extend(data)

    try:
        drain(min(7.0, timeout * 0.3))
        screen = _clean_tty(buf)
        low = screen.lower()
        if "login method" in low or "welcome to claude" in low:
            return [], "CLAUDE_ONBOARDING_REQUIRED", "onboarding/login блокирует /usage"
        # Диалог доверия к каталогу (folder trust — не login): принять Enter.
        if "trust this folder" in low or "trust the files" in low:
            _os.write(master, b"\r")
            drain(2.0)
        buf.clear()
        # Ввести /usage и отправить. Slash-autocomplete может требовать второй Enter
        # (первый принимает completion, второй исполняет команду).
        _os.write(master, b"/usage")
        drain(1.2)
        _os.write(master, b"\r")
        drain(1.2)
        _os.write(master, b"\r")
        drain(min(9.0, timeout * 0.5))
        panel = _clean_tty(buf)
    finally:
        try:
            _os.write(master, b"\x03")
            _time.sleep(0.2)
            _os.write(master, b"\x03")
        except OSError:
            pass
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        _os.close(master)
    windows = _parse_claude_usage(panel)
    if not windows:
        # Официальная проба выполнена (ready-prompt достигнут), но интерактивная
        # панель /usage не рендерится/не парсится в headless PTY этой версии CLI.
        # Точная причина вместо немого UNKNOWN; план берётся из auth status.
        return [], "CLAUDE_USAGE_TUI_NOT_HEADLESS", "Claude Code /usage не отдаёт панель headless"
    return windows, "", ""


def probe_capacity(provider: str, root_path: str, *, executable: str,
                   run_as_user: str | None = None, timeout: float = 30.0) -> dict:
    """Диспетчер по провайдеру. Возвращает нормализованный результат ёмкости."""
    if provider == "codex":
        return probe_codex_capacity(root_path, executable=executable, run_as_user=run_as_user,
                                    timeout=timeout)
    if provider == "claude":
        return probe_claude_capacity(root_path, executable=executable, run_as_user=run_as_user,
                                     timeout=timeout)
    return _cap_error(provider or "unknown", "UNSUPPORTED_PROVIDER", f"provider={provider}")


def capacity_status_from_windows(windows: list[dict]) -> str:
    """AVAILABLE/LOW/EXHAUSTED/UNKNOWN из числовых окон (по максимальному used%)."""
    used = [w["used_pct"] for w in windows if w.get("used_pct") is not None]
    if not used:
        return "UNKNOWN"
    mx = max(used)
    if mx >= 100:
        return "EXHAUSTED"
    if mx >= 80:
        return "LOW"
    return "AVAILABLE"


# --- Персистентность + reconcile (single-flight, bounded) ----------------------
_REGISTRY_PATH = "/var/lib/codevinci-atlas/profiles/registry.json"
_CAP_MIN_INTERVAL_S = 90.0   # bounded caching: не чаще, чтобы не устраивать шторм
_cap_lock = _th.Lock()
_cap_last: dict[str, float] = {}


def _parse_iso(v: str | None):
    if not v:
        return None
    try:
        return _dt.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def persist_capacity(profile_id: str, cap: dict) -> dict:
    """Сохранить нормализованное наблюдение ёмкости (safe-поля) в
    ``capacity_observations``. Возвращает to_dict() новой строки."""
    from .db import session_scope
    from .ids import new_id
    from .orm import CapacityObservation
    wins = cap.get("windows") or []
    by = {w["id"]: w for w in wins}
    five = by.get("5h")
    seven = by.get("7d") or by.get("1d")
    status = capacity_status_from_windows(wins)
    primary_reset = (five or seven or {}).get("reset_at") if wins else None
    with session_scope() as s:
        row = CapacityObservation(
            id=new_id("cap"), profile_id=profile_id, status=status,
            five_h_used_pct=(five or {}).get("used_pct") if five else None,
            seven_d_used_pct=(seven or {}).get("used_pct") if seven else None,
            reset_at=_parse_iso(primary_reset),
            source=cap.get("source", "unknown")[:30],
            confidence="official" if wins else ("plan_only" if cap.get("plan") else "none"),
            stale=False, plan=(cap.get("plan") or "")[:40],
            five_h_reset_at=_parse_iso((five or {}).get("reset_at")) if five else None,
            seven_d_reset_at=_parse_iso((seven or {}).get("reset_at")) if seven else None,
            error_code=(cap.get("error_code") or "")[:60],
            windows_json=_json.dumps(wins, ensure_ascii=False))
        s.add(row)
        s.commit()
        return row.to_dict()


def _load_registry(path: str | None = None) -> dict:
    try:
        return _json.load(open(path or _REGISTRY_PATH))["profiles"]
    except (OSError, KeyError, ValueError):
        return {}


def _profile_id_for_alias(alias: str) -> str | None:
    from sqlalchemy import select as _select_

    from .db import session_scope
    from .orm import AgentProfile
    with session_scope() as s:
        row = s.execute(_select_(AgentProfile).where(AgentProfile.alias == alias)).scalars().first()
        return row.id if row else None


def reconcile_capacity(*, prober=None, registry_path: str | None = None,
                       aliases: list[str] | None = None, force: bool = False,
                       timeout: float = 30.0) -> list[dict]:
    """Пробит ёмкость по всем (или указанным) alias и персистит наблюдения.
    Bounded single-flight: повторный вызов в пределах интервала — no-op (если не
    force). ``prober(provider, root, exe, user, timeout)`` инъектируется в тестах."""
    probe = prober or (lambda provider, root, exe, user, to: probe_capacity(
        provider, root, executable=exe, run_as_user=user, timeout=to))
    reg = _load_registry(registry_path)
    targets = aliases or list(reg.keys())
    out = []
    if not _cap_lock.acquire(blocking=False):
        return out  # single-flight: другой reconcile уже идёт
    try:
        now = _time.time()
        for alias in targets:
            p = reg.get(alias)
            if not p:
                continue
            if not force and (now - _cap_last.get(alias, 0.0)) < _CAP_MIN_INTERVAL_S:
                continue
            pid = _profile_id_for_alias(alias)
            if not pid:
                continue
            cap = probe(p.get("provider", ""), p["root_path"], p["executable_path"],
                        p.get("runtime_user"), timeout)
            _cap_last[alias] = _time.time()
            rec = persist_capacity(pid, cap)
            out.append({"alias": alias, **{k: cap.get(k) for k in
                        ("plan", "auth_ok", "source", "error_code")}, "status": rec["status"],
                        "windows": cap.get("windows", [])})
    finally:
        _cap_lock.release()
    return out
