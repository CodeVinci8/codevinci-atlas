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
# Codex — App Server (JSON-RPC); Claude — auth status (план) + stream-json
# rate_limit_event (статус окна + reset). used_percentage Claude отдаёт лишь
# через onboarding-закрытый status-line — числа не выдумываем.
# Никогда не читаем/не копируем токены/cookie/сессии; email/org — redaction на
# границе. Нет числа → точный error_code, а не немой UNKNOWN.
# =============================================================================
import json as _json  # noqa: E402
import os as _os  # noqa: E402
import re as _re  # noqa: E402
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


def _reap(p) -> None:
    """Надёжно завершить и «пожать» дочерний процесс (никаких зомби/висящих pipe)."""
    for fn in (lambda: p.stdin and p.stdin.close(), p.terminate):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    try:
        p.wait(timeout=2)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
    for stream in (p.stdout, p.stderr):
        try:
            if stream:
                stream.close()
        except Exception:  # noqa: BLE001
            pass


def probe_codex_capacity(root_path: str, *, executable: str, run_as_user: str | None = None,
                         timeout: float = 25.0) -> dict:
    """Реальные лимиты Codex через официальный ``codex app-server`` (JSON-RPC 2.0):
    initialize → (initialized) → account/read[planType] → account/rateLimits/read.

    Хардненинг: ждём именно **ответ на initialize** (без фиксированных sleep),
    отправляем notification ``initialized``, читаем и ``result``, и ``error``,
    различаем сбои инициализации/аккаунта/лимитов, надёжно reap-им процесс.
    Персистим только plan и rate-limit поля; email/org/токены не сохраняем; окна
    показываем только реально возвращённые (никакого выдуманного 5-часового окна)."""
    cmd = _base_argv(root_path, run_as_user, {"CODEX_HOME": root_path}) + [executable, "app-server"]
    try:
        p = _sp.Popen(cmd, stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, bufsize=1)
    except Exception as exc:  # noqa: BLE001
        return _cap_error("codex", "CODEX_APPSERVER_SPAWN_FAILED", str(exc))
    results: dict[int, dict] = {}
    errors: dict[int, dict] = {}
    cv = _th.Condition()

    def reader():
        try:
            for line in p.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if not (isinstance(msg, dict) and "id" in msg):
                    continue  # notifications (без id) игнорируем — идентификаторы не храним
                with cv:
                    if "result" in msg:
                        r = msg["result"]
                        results[msg["id"]] = r if isinstance(r, dict) else {"_": r}
                    elif "error" in msg:
                        e = msg["error"]
                        errors[msg["id"]] = e if isinstance(e, dict) else {"message": str(e)}
                    cv.notify_all()
        finally:
            with cv:
                cv.notify_all()

    _th.Thread(target=reader, daemon=True).start()

    def send(obj):
        p.stdin.write(_json.dumps(obj) + "\n")
        p.stdin.flush()

    def wait_for(rid: int, deadline: float) -> bool:
        with cv:
            while rid not in results and rid not in errors:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return False
                cv.wait(timeout=remaining)
            return True

    try:
        deadline = _time.monotonic() + timeout
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "atlas", "title": "Atlas", "version": "1.0"}}})
        if not wait_for(1, deadline):
            return _cap_error("codex", "CODEX_INIT_TIMEOUT", "initialize без ответа")
        if 1 in errors:
            return _cap_error("codex", "CODEX_INIT_FAILED", str(errors[1].get("message", "")))
        # Notification initialized (без id → ответа не ждём); best-effort.
        try:
            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        except Exception:  # noqa: BLE001
            pass
        send({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {}})
        send({"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read", "params": {}})
        wait_for(2, deadline)
        wait_for(3, deadline)
    except Exception as exc:  # noqa: BLE001
        return _cap_error("codex", "CODEX_APPSERVER_IO_FAILED", str(exc))
    finally:
        _reap(p)

    acct = results.get(2) or {}
    if not acct and 2 in errors:
        return _cap_error("codex", "CODEX_ACCOUNT_READ_FAILED", str(errors[2].get("message", "")))
    plan = str(((acct.get("account") or {}).get("planType") or "")).strip()
    auth_ok = bool(acct.get("account"))
    if 3 in errors and 3 not in results:
        if not auth_ok:
            return _cap_error("codex", "CODEX_NOT_AUTHENTICATED", "нет account", plan=plan)
        return _cap_error("codex", "CODEX_RATELIMITS_FAILED",
                          str(errors[3].get("message", "")), plan=plan, auth_ok=True)
    rl = (results.get(3) or {}).get("rateLimits")
    if not rl:
        if not auth_ok:
            return _cap_error("codex", "CODEX_NOT_AUTHENTICATED", "app-server без account")
        return _cap_error("codex", "CODEX_RATELIMITS_EMPTY", "нет rateLimits", plan=plan, auth_ok=True)
    windows = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue  # только реально возвращённые окна; null secondary не выдумываем
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
                          timeout: float = 60.0, start_window: bool = False) -> dict:
    """Ёмкость Claude из ОФИЦИАЛЬНЫХ поверхностей (§11.6):

    * план и авторизация — ``claude auth status --json`` (без затрат подписки);
    * **числовые** окна (``used_percentage``+``resets_at``) — ОФИЦИАЛЬНЫЙ status-line
      ``rate_limits`` (stdin JSON команде statusLine) из интерактивной сессии после
      первого API-ответа (§3, code.claude.com/docs/en/statusline#rate-limit-usage);
    * fallback (тип+статус+reset без %) — официальные ``rate_limit_event`` из
      ``claude -p … --output-format stream-json --verbose``.

    Диагностированное ограничение (свежая проба v2.1.220, документированный порядок):
    интерактивный REPL изолированного профиля с незавершённым onboarding показывает
    экран «Select login method» → OAuth-URL ДО первого prompt. Завершение = login
    (запрещено; блокируется safety-классификатором), а ``hasCompletedOnboarding``
    forge запрещён. Поэтому для профиля с НЕзавершённым onboarding числа недоступны
    → честный ``rate_limit_event`` статус. Для профиля с завершённым onboarding
    status-line отдаёт реальные проценты — код это поддерживает без изменений.

    Безопасность (§30): не читаем credentials/cookies/session; берём только
    used_percentage/resets_at/rateLimitType/status; session_id/uuid/request_id/
    email/org/prompt/response отбрасываем на границе; spool 0600, эфемерный."""
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
    if not start_window:
        # «Обновить лимиты» без окна не тратит подписку: возвращаем план+auth.
        r = _cap_error("claude", "CLAUDE_NEEDS_START_WINDOW",
                       "числовой статус окна требует owner-действия «Начать окно»",
                       plan=plan, auth_ok=True)
        r["source"] = "claude-auth-status"
        return r
    # 1. Предпочтительно — ОФИЦИАЛЬНЫЙ status-line rate_limits (реальные проценты).
    win_num, sl_err, sl_detail = _claude_statusline_windows(root_path, executable, run_as_user,
                                                            timeout)
    if win_num:
        return {"provider": "claude", "plan": plan, "auth_ok": True, "windows": win_num,
                "source": "claude-statusline", "checked_at": _now_iso(),
                "error_code": "", "detail": _safe(sl_detail)}
    # 2. Fallback — официальный rate_limit_event (статус+reset без %).
    windows, err, detail = _claude_start_window(root_path, executable, run_as_user, timeout)
    # error_code пуст, если окна получены (fallback успешен); иначе — точная причина
    # (сначала fallback-ошибка, затем диагностика status-line).
    ecode = "" if windows else (err or sl_err)
    return {"provider": "claude", "plan": plan, "auth_ok": True, "windows": windows,
            "source": "claude-stream-json" if windows else "claude-auth-status",
            "checked_at": _now_iso(),
            "error_code": ecode, "detail": _safe(detail or sl_detail)}


# Отображение официального rateLimitType → окно Atlas.
_CLAUDE_WIN = {"five_hour": ("5h", "Сессия (5 ч)", 300),
               "seven_day": ("7d", "Неделя (7 дн)", 10080)}


def parse_claude_rate_limit_events(stream_text: str) -> list[dict]:
    """Safe-извлечение официальных ``rate_limit_event`` из stream-json Claude.
    Берём ТОЛЬКО rateLimitType/status/resetsAt; идентификаторы/текст отбрасываем."""
    out: list[dict] = []
    for line in (stream_text or "").splitlines():
        line = line.strip()
        if '"rate_limit_info"' not in line:
            continue
        try:
            d = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        info = d.get("rate_limit_info") if isinstance(d, dict) else None
        if not isinstance(info, dict):
            continue
        rt = info.get("rateLimitType")
        if rt in _CLAUDE_WIN:
            out.append({"rateLimitType": rt, "status": str(info.get("status") or "").lower(),
                        "resetsAt": info.get("resetsAt")})
    return out


def _claude_windows_from_events(events: list[dict]) -> list[dict]:
    """Официальные события → окна Atlas (последнее событие на тип окна).
    ``status``: allowed(доступно)/warning(мало)/rejected(исчерпано); reset — точный."""
    by: dict[str, dict] = {}
    for e in events:
        by[e["rateLimitType"]] = e
    windows: list[dict] = []
    for rt, e in by.items():
        wid, label, mins = _CLAUDE_WIN[rt]
        status = e.get("status") or ""
        used = 100.0 if status == "rejected" else None  # только исчерпание даёт число
        w = _mk_window(win_id=wid, label=label, used_pct=used,
                       reset_at=_epoch_iso(e.get("resetsAt")), window_mins=mins)
        w["status"] = status  # allowed|warning|rejected — статус без фикции процента
        windows.append(w)
    return windows


def _statusline_from_spool(spool_text: str) -> list[dict]:
    """Safe-извлечение числовых окон из официального status-line JSON (rate_limits).
    Берём ТОЛЬКО used_percentage/resets_at; всё остальное отбрасываем на границе."""
    try:
        d = _json.loads(spool_text)
    except (_json.JSONDecodeError, TypeError):
        return []
    rl = d.get("rate_limits") if isinstance(d, dict) else None
    if not isinstance(rl, dict):
        return []
    out: list[dict] = []
    for key, (wid, label, mins) in _CLAUDE_WIN.items():
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        up = w.get("used_percentage")
        if up is None:
            continue
        win = _mk_window(win_id=wid, label=label, used_pct=up,
                         reset_at=_epoch_iso(w.get("resets_at")), window_mins=mins)
        win["status"] = ("rejected" if up >= 100 else "warning" if up >= 80 else "allowed")
        out.append(win)
    return out


def statusline_filter_script(spool: str) -> str:
    """Текст self-contained statusLine-фильтра (call-8 privacy, §3, D).

    Читает сырой status-line JSON из stdin В ПАМЯТИ, извлекает ТОЛЬКО разрешённые
    поля (rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}) и атомарно
    пишет в spool (0600) уже нормализованный минимум. Сырой payload/prompt/response/
    email/token/cookie/session/account на диск НЕ попадают. Всё прочее отбрасывается
    до какой-либо persistent-записи."""
    return (
        "#!/usr/bin/python3\n"
        "import sys,json,os,tempfile\n"
        f"SPOOL={spool!r}\n"
        "raw=sys.stdin.read()\n"
        "try: d=json.loads(raw)\n"
        "except Exception: d={}\n"
        "rl=d.get('rate_limits') or {}\n"
        "out={}\n"
        "for w in ('five_hour','seven_day'):\n"
        "    x=rl.get(w) if isinstance(rl,dict) else None\n"
        "    x=x or {}\n"
        "    up=x.get('used_percentage')\n"
        "    if up is not None: out[w]={'used_percentage':up,'resets_at':x.get('resets_at')}\n"
        "fd,tmp=tempfile.mkstemp(dir=os.path.dirname(SPOOL))\n"
        "os.write(fd,json.dumps({'rate_limits':out}).encode()); os.close(fd)\n"
        "os.chmod(tmp,0o600); os.replace(tmp,SPOOL)\n"
        "print('atlas')\n")


def _claude_statusline_windows(root: str, exe: str, user: str | None, timeout: float):
    """ОФИЦИАЛЬНЫЙ числовой сбор ёмкости через status-line ``rate_limits`` (§3):
    эфемерный ``--settings`` со statusLine-командой → spool 0600; интерактивная
    сессия; после первого API-ответа Claude Code вызывает statusLine и передаёт
    ``rate_limits`` на stdin команды; мы читаем spool и берём только used%/resets.

    Безопасно и fail-closed: НИКОГДА не навигируем login/terms (owner-запрет +
    safety-классификатор). Экран onboarding/login → немедленный abort с точным
    диагностическим кодом. Сообщение отправляем ТОЛЬКО достигнув ready-prompt без
    onboarding-маркеров. Гарантированный kill-server и удаление эфемерных файлов."""
    import shutil
    import tempfile
    import uuid
    tmux = shutil.which("tmux")
    if not tmux:
        return [], "CLAUDE_TMUX_ABSENT", "tmux недоступен для status-line пробы"
    work = tempfile.mkdtemp(prefix="atlas-sl-")
    spool = _os.path.join(work, "rate.json")
    sl = _os.path.join(work, "sl.sh")
    settings = _os.path.join(work, "settings.json")
    sock = _os.path.join(work, "t.sock")
    session = "atlas-sl-" + uuid.uuid4().hex[:8]
    try:
        # statusLine-скрипт (call-8 privacy fix): фильтрует stdin В ПАМЯТИ и пишет
        # на диск ТОЛЬКО нормализованные safe-поля. Сырой payload/prompt/response/
        # email/token НИКОГДА не попадает в spool.
        with open(sl, "w") as f:
            f.write(statusline_filter_script(spool))
        _os.chmod(sl, 0o755)
        with open(settings, "w") as f:
            _json.dump({"statusLine": {"type": "command", "command": sl}, "theme": "dark",
                        "includeCoAuthoredBy": False}, f)
        if user:
            _sp.run(["chown", "-R", f"{user}:{user}", work], capture_output=True, timeout=10)
            _os.chmod(work, 0o750)
        env = {"HOME": root, "CLAUDE_CONFIG_DIR": root, "PATH": f"/usr/bin:/bin:{root}/.local/bin",
               "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": "/bin/bash"}
        envargs = [f"{k}={v}" for k, v in env.items()]
        runner = ["runuser", "-u", user, "--"] if user else []

        def tmux_cmd(*args, to: float = 8.0):
            return _sp.run([*runner, "env", "-i", *envargs, tmux, "-S", sock, *args],
                           capture_output=True, text=True, timeout=to, stdin=_sp.DEVNULL)

        r = tmux_cmd("new-session", "-d", "-s", session, "-x", "150", "-y", "46",
                     "-c", root, f"{exe} --settings {settings}", to=15.0)
        if r.returncode != 0:
            return [], "CLAUDE_SL_SPAWN_FAILED", _safe(r.stderr or "tmux new-session")
        # onboarding/login маркеры (fail-closed) и ready-prompt.
        gate = ("select login method", "sign in", "paste code", "/login", "log in",
                "accept", "terms of service", "welcome to claude")
        ready = ("? for shortcuts", "esc to interrupt")
        deadline = _time.monotonic() + min(14.0, timeout * 0.45)
        at_prompt = False
        sent = False
        while _time.monotonic() < deadline:
            _time.sleep(1.0)
            if _os.path.exists(spool):
                break
            screen = tmux_cmd("capture-pane", "-p", "-t", session, to=6.0).stdout or ""
            low = screen.lower()
            if any(m in low for m in gate):
                # Экран onboarding/login: НЕ навигируем, немедленный abort.
                return [], "CLAUDE_STATUSLINE_ONBOARDING_GATED", \
                    "интерактивный REPL требует login-onboarding до prompt (login запрещён)"
            if not at_prompt and any(m in low for m in ready):
                at_prompt = True
            if at_prompt and not sent:
                # Ready-prompt достигнут без onboarding → один минимальный ответ.
                tmux_cmd("send-keys", "-t", session, "say ok", to=4.0)
                _time.sleep(0.6)
                tmux_cmd("send-keys", "-t", session, "Enter", to=4.0)
                sent = True
        # Дождаться status-line spool с rate_limits после ответа.
        if sent:
            udeadline = _time.monotonic() + min(24.0, timeout * 0.5)
            while _time.monotonic() < udeadline:
                _time.sleep(1.0)
                if _os.path.exists(spool):
                    try:
                        if "rate_limits" in open(spool).read():
                            break
                    except OSError:
                        pass
        if not _os.path.exists(spool):
            return [], "CLAUDE_STATUSLINE_NO_SPOOL", "statusLine не сработал (prompt/response не достигнут)"
        try:
            with open(spool) as f:
                spool_text = f.read()
        except OSError as exc:
            return [], "CLAUDE_STATUSLINE_READ_FAILED", str(exc)
        wins = _statusline_from_spool(spool_text)
        if wins:
            return wins, "", ""
        return [], "CLAUDE_STATUSLINE_NO_RATE_LIMITS", "status-line без rate_limits (нет API-ответа)"
    except _sp.TimeoutExpired:
        return [], "CLAUDE_STATUSLINE_TIMEOUT", "status-line проба превысила лимит времени"
    except Exception as exc:  # noqa: BLE001
        return [], "CLAUDE_STATUSLINE_FAILED", str(exc)
    finally:
        try:
            _sp.run([*(["runuser", "-u", user, "--"] if user else []), "env", "-i",
                     f"HOME={root}", "PATH=/usr/bin:/bin", "SHELL=/bin/bash", tmux, "-S", sock,
                     "kill-server"], capture_output=True, timeout=8)
        except Exception:  # noqa: BLE001
            pass
        try:
            import shutil as _sh
            _sh.rmtree(work, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def _claude_start_window(root: str, exe: str, user: str | None, timeout: float):
    """Owner-действие «Начать окно и обновить»: РОВНО один минимальный официальный
    ответ Claude (tools/MCP/repo off) через ``-p … --output-format stream-json``;
    из потока берём официальные ``rate_limit_event``. Тратит немного подписки.
    Без чтения credentials, без /usage TUI, без мутаций config/onboarding."""
    argv = _base_argv(root, user, {"CLAUDE_CONFIG_DIR": root}) + [
        exe, "-p", "ok", "--output-format", "stream-json", "--verbose", "--allowedTools", ""]
    try:
        out = _sp.run(argv, capture_output=True, text=True, timeout=timeout, stdin=_sp.DEVNULL)
    except _sp.TimeoutExpired:
        return [], "CLAUDE_START_WINDOW_TIMEOUT", "минимальный ответ превысил лимит времени"
    except Exception as exc:  # noqa: BLE001
        return [], "CLAUDE_START_WINDOW_FAILED", str(exc)
    events = parse_claude_rate_limit_events(out.stdout)
    windows = _claude_windows_from_events(events)
    if windows:
        return windows, "", ""
    return [], "CLAUDE_NO_RATE_LIMIT_EVENT", "official CLI не отдал rate_limit_event"




def probe_capacity(provider: str, root_path: str, *, executable: str,
                   run_as_user: str | None = None, timeout: float = 30.0,
                   start_window: bool = False) -> dict:
    """Диспетчер по провайдеру. Возвращает нормализованный результат ёмкости.
    ``start_window`` (только Claude) → owner-действие «Начать окно и обновить»."""
    if provider == "codex":
        return probe_codex_capacity(root_path, executable=executable, run_as_user=run_as_user,
                                    timeout=timeout)
    if provider == "claude":
        return probe_claude_capacity(root_path, executable=executable, run_as_user=run_as_user,
                                     timeout=max(timeout, 60.0), start_window=start_window)
    return _cap_error(provider or "unknown", "UNSUPPORTED_PROVIDER", f"provider={provider}")


def capacity_status_from_windows(windows: list[dict]) -> str:
    """AVAILABLE/LOW/EXHAUSTED/UNKNOWN из окон.

    Учитывает и числовые окна (Codex ``used_pct``), и статус-окна (Claude
    ``rate_limit_event`` status): ``rejected`` → EXHAUSTED, ``warning`` → LOW,
    ``allowed`` → AVAILABLE. Явный статус исчерпания/предупреждения приоритетнее."""
    statuses = [(w.get("status") or "").lower() for w in windows if w.get("status")]
    used = [w["used_pct"] for w in windows if w.get("used_pct") is not None]
    mx = max(used) if used else None
    if "rejected" in statuses or (mx is not None and mx >= 100):
        return "EXHAUSTED"
    if "warning" in statuses or (mx is not None and mx >= 80):
        return "LOW"
    if mx is not None or "allowed" in statuses:
        return "AVAILABLE"
    return "UNKNOWN"


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


def _latest_windowed_observation(s, profile_id: str):
    """Последнее наблюдение с реальными числовыми окнами (валидное ИЛИ ранее
    перенесённое STALE — оно сохраняет исходный ``data_observed_at``). Основа
    stale-fallback: неудачная проба не должна затирать валидные числа пустым
    свежим UNKNOWN."""
    from sqlalchemy import select as _sel

    from .orm import CapacityObservation
    rows = s.execute(_sel(CapacityObservation)
                     .where(CapacityObservation.profile_id == profile_id)
                     .order_by(CapacityObservation.observed_at.desc()).limit(20)).scalars().all()
    for r in rows:
        if (r.windows_json or "[]") not in ("[]", "", None):
            return r
    return None


def persist_capacity(profile_id: str, cap: dict) -> dict:
    """Сохранить нормализованное наблюдение ёмкости (safe-поля) в
    ``capacity_observations``. Возвращает to_dict() новой строки.

    Stale-fallback (§11.6): успешная числовая проба пишет свежее наблюдение;
    неудачная (пустые окна) — **не затирает** валидные числа, а переносит
    последние валидные окна как ``STALE`` с новым error_code и временем сбоя,
    сохраняя честный ``data_observed_at`` (возраст данных). Если валидного
    наблюдения ещё не было — точное недоступное состояние с error_code (не немой
    UNKNOWN). Авторизация здесь не меняется — сбой ёмкости ≠ AUTH_REQUIRED."""
    from .db import session_scope
    from .ids import new_id
    from .orm import CapacityObservation
    wins = cap.get("windows") or []
    now = _dt.now(tz=_tz.utc)
    err = (cap.get("error_code") or "")[:60]
    with session_scope() as s:
        if wins:
            by = {w["id"]: w for w in wins}
            five = by.get("5h")
            seven = by.get("7d") or by.get("1d")
            status = capacity_status_from_windows(wins)
            primary_reset = (five or seven or {}).get("reset_at")
            row = CapacityObservation(
                id=new_id("cap"), profile_id=profile_id, status=status,
                five_h_used_pct=(five or {}).get("used_pct") if five else None,
                seven_d_used_pct=(seven or {}).get("used_pct") if seven else None,
                reset_at=_parse_iso(primary_reset),
                source=cap.get("source", "unknown")[:30], confidence="official",
                stale=False, plan=(cap.get("plan") or "")[:40],
                five_h_reset_at=_parse_iso((five or {}).get("reset_at")) if five else None,
                seven_d_reset_at=_parse_iso((seven or {}).get("reset_at")) if seven else None,
                error_code=err, windows_json=_json.dumps(wins, ensure_ascii=False),
                observed_at=now, data_observed_at=now)
        else:
            prev = _latest_windowed_observation(s, profile_id)
            if prev is not None:
                # Перенос последних валидных окон как STALE: данные прежние,
                # observed_at = момент неудачной проверки, data_observed_at сохранён.
                row = CapacityObservation(
                    id=new_id("cap"), profile_id=profile_id, status="STALE",
                    five_h_used_pct=prev.five_h_used_pct, seven_d_used_pct=prev.seven_d_used_pct,
                    reset_at=prev.reset_at, source=prev.source, confidence="stale", stale=True,
                    plan=(cap.get("plan") or prev.plan or "")[:40],
                    five_h_reset_at=prev.five_h_reset_at, seven_d_reset_at=prev.seven_d_reset_at,
                    error_code=err or "CAPACITY_REFRESH_FAILED",
                    windows_json=prev.windows_json,
                    observed_at=now, data_observed_at=(prev.data_observed_at or prev.observed_at))
            else:
                # Валидных чисел никогда не было → точное недоступное состояние.
                row = CapacityObservation(
                    id=new_id("cap"), profile_id=profile_id, status="UNKNOWN",
                    five_h_used_pct=None, seven_d_used_pct=None, reset_at=None,
                    source=cap.get("source", "unknown")[:30],
                    confidence="plan_only" if cap.get("plan") else "none", stale=False,
                    plan=(cap.get("plan") or "")[:40], error_code=err,
                    windows_json="[]", observed_at=now, data_observed_at=now)
        s.add(row)
        s.commit()
        return row.to_dict()


def _load_registry(path: str | None = None) -> dict:
    try:
        with open(path or _REGISTRY_PATH) as fh:
            return _json.load(fh)["profiles"]
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
                       timeout: float = 30.0, start_window: bool = False) -> list[dict]:
    """Пробит ёмкость по alias и персистит наблюдения. Ограничения (§11.6):

    * **single-flight**: если сверка уже идёт — честно вернуть
      ``state=REFRESH_IN_PROGRESS`` (не запускать вторую и не устраивать шторм);
    * **per-alias cooldown** ``_CAP_MIN_INTERVAL_S``: слишком частый refresh
      возвращает ``state=COOLDOWN`` c ``cooldown_remaining_s`` (без пробы);
    * ``force=True`` — bypass cooldown; допускается **только** из доверенного
      deploy/admin-пути (CLI). HTTP-refresh вызывает без force и уважает интервал.
    * ``start_window`` (только Claude) — owner-действие «Начать окно и обновить»
      (один минимальный официальный ответ, немного подписки).

    ``prober(provider, root, exe, user, timeout)`` инъектируется в тестах."""
    probe = prober or (lambda provider, root, exe, user, to: probe_capacity(
        provider, root, executable=exe, run_as_user=user, timeout=to,
        start_window=start_window))
    reg = _load_registry(registry_path)
    targets = aliases or list(reg.keys())
    if not _cap_lock.acquire(blocking=False):
        # Другой refresh уже идёт — честный статус вместо немого no-op/дубля.
        return [{"alias": a, "state": "REFRESH_IN_PROGRESS"} for a in targets] or \
               [{"alias": None, "state": "REFRESH_IN_PROGRESS"}]
    out: list[dict] = []
    try:
        now = _time.time()
        for alias in targets:
            p = reg.get(alias)
            if not p:
                out.append({"alias": alias, "state": "UNKNOWN_ALIAS"})
                continue
            since = now - _cap_last.get(alias, 0.0)
            if not force and since < _CAP_MIN_INTERVAL_S:
                out.append({"alias": alias, "state": "COOLDOWN",
                            "cooldown_remaining_s": round(_CAP_MIN_INTERVAL_S - since, 1)})
                continue
            pid = _profile_id_for_alias(alias)
            if not pid:
                out.append({"alias": alias, "state": "NO_PROFILE"})
                continue
            cap = probe(p.get("provider", ""), p["root_path"], p["executable_path"],
                        p.get("runtime_user"), timeout)
            _cap_last[alias] = _time.time()
            rec = persist_capacity(pid, cap)
            out.append({"alias": alias, "state": "REFRESHED",
                        **{k: cap.get(k) for k in ("plan", "auth_ok", "source", "error_code")},
                        "status": rec["status"], "windows": cap.get("windows", [])})
    finally:
        _cap_lock.release()
    return out
