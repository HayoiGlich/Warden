"""Хранилище доменных кредов вошедшего пользователя (для делегирования).

При делегировании MID выполняет операции AD под учётной записью вошедшего
пользователя, а не под служебной. Для этого после входа нужно помнить его
пароль AD на время сессии.

Пароль НЕ кладём в cookie (даже зашифрованным) — держим в памяти процесса,
а в сессии-cookie лежит только непрозрачный токен. Плата: при рестарте
сервиса креды теряются и для операций AD нужен повторный вход (сессия при
этом остаётся валидной — токен просто перестаёт находиться). Многопроцессный
режим не поддержан (как и cookie-сессии сейчас) — под Redis вынесем позже.
"""

from __future__ import annotations

import threading
import time
from secrets import token_urlsafe
from typing import Optional

# TTL кредов ~ времени жизни сессии. Ленивая чистка при доступе.
_TTL_SECONDS = 12 * 60 * 60

_lock = threading.Lock()
_store: dict[str, dict] = {}


def _now() -> float:
    return time.monotonic()


def _purge_locked() -> None:
    cutoff = _now() - _TTL_SECONDS
    stale = [k for k, v in _store.items() if v["ts"] < cutoff]
    for k in stale:
        _store.pop(k, None)


def put(username: str, password: str) -> str:
    """Сохранить креды, вернуть токен для сессии."""
    token = token_urlsafe(24)
    with _lock:
        _purge_locked()
        _store[token] = {
            "username": str(username or ""),
            "password": str(password or ""),
            "ts": _now(),
        }
    return token


def get(token: Optional[str]) -> Optional[dict]:
    """Креды по токену или None (нет/устарел). Обновляет отметку времени."""
    if not token:
        return None
    with _lock:
        item = _store.get(token)
        if item is None:
            return None
        if item["ts"] < _now() - _TTL_SECONDS:
            _store.pop(token, None)
            return None
        item["ts"] = _now()
        return {"username": item["username"], "password": item["password"]}


def drop(token: Optional[str]) -> None:
    if not token:
        return
    with _lock:
        _store.pop(token, None)
