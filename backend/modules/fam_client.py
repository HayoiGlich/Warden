from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from backend.modules.config import settings

logger = logging.getLogger("log_analyzer")


def _normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _graphql_string(value: str) -> str:
    return json.dumps(str(value or ""))


def _empty_status(
    *,
    configured: bool,
    username: str = "",
    state: str,
    summary: str,
    detail: str = "",
    full_name: str = "",
    email: str = "",
    active: bool | None = None,
) -> dict[str, Any]:
    return {
        "configured": bool(configured),
        "state": state,
        "summary": summary,
        "detail": detail,
        "username": str(username or ""),
        "full_name": str(full_name or ""),
        "email": str(email or ""),
        "active": active if active is None else bool(active),
    }


class FamClient:
    def __init__(self) -> None:
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._discovery_cache: tuple[float, str] | None = None
        self._user_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @property
    def configured(self) -> bool:
        if str(settings.fam_access_token or "").strip():
            return bool(_normalize_base_url(settings.fam_base_url) or str(settings.fam_graphql_url or "").strip())

        grant_type = self._grant_type()
        has_base = bool(_normalize_base_url(settings.fam_base_url))
        has_client = bool(str(settings.fam_client_id or "").strip())

        if grant_type == "client_credentials":
            return has_base and has_client

        return (
            has_base
            and has_client
            and bool(str(settings.fam_api_username or "").strip())
            and bool(str(settings.fam_api_password or "").strip())
        )

    def _grant_type(self) -> str:
        return str(settings.fam_grant_type or "password").strip().lower() or "password"

    def _cache_ttl(self) -> int:
        return max(5, int(settings.fam_cache_seconds or 60))

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=max(2, int(settings.fam_timeout_seconds or 10)))

    def _request_ssl(self) -> bool:
        return bool(settings.fam_verify_ssl)

    def _token_cache_valid(self) -> bool:
        return bool(self._token) and (time.time() + 30) < self._token_expires_at

    def _invalidate_token(self) -> None:
        self._token = ""
        self._token_expires_at = 0.0

    def _graphql_url(self) -> str:
        explicit = str(settings.fam_graphql_url or "").strip()
        if explicit:
            return explicit

        base_url = _normalize_base_url(settings.fam_base_url)
        if not base_url:
            return ""
        return f"{base_url}/graphql"

    async def _discover_token_url(self, session: aiohttp.ClientSession) -> str:
        explicit = str(settings.fam_token_url or "").strip()
        if explicit:
            return explicit

        base_url = _normalize_base_url(settings.fam_base_url)
        if not base_url:
            raise RuntimeError("FAM base URL is not configured")

        now = time.time()
        if self._discovery_cache and (now - self._discovery_cache[0]) < self._cache_ttl():
            return self._discovery_cache[1]

        discovery_url = f"{base_url}/.well-known/openid-configuration"
        async with session.get(discovery_url, ssl=self._request_ssl()) as response:
            body = await response.json(content_type=None)
            if response.status >= 400:
                detail = body if isinstance(body, dict) else {}
                raise RuntimeError(
                    f"FAM discovery failed with HTTP {response.status}: {detail!r}"
                )

        token_url = str(body.get("token_endpoint") or "").strip()
        if not token_url:
            raise RuntimeError("Token endpoint is missing in FAM discovery response")

        self._discovery_cache = (now, token_url)
        return token_url

    def _grant_payload(self) -> dict[str, str]:
        grant_type = self._grant_type()
        payload = {
            "grant_type": grant_type,
            "client_id": str(settings.fam_client_id or "adminconsole").strip() or "adminconsole",
        }

        client_secret = str(settings.fam_client_secret or "").strip()
        if client_secret:
            payload["client_secret"] = client_secret

        if grant_type == "client_credentials":
            scope = str(settings.fam_token_scope or "").strip()
            if scope:
                payload["scope"] = scope
            return payload

        payload["username"] = str(settings.fam_api_username or "").strip()
        payload["password"] = str(settings.fam_api_password or "")

        scope = str(settings.fam_token_scope or "").strip()
        if scope:
            payload["scope"] = scope

        return payload

    @staticmethod
    def _token_error_hint(body: dict[str, Any]) -> str:
        error = str(body.get("error") or "").strip().lower()
        description = str(body.get("error_description") or "").strip()
        error_hint = str(body.get("error_hint") or "").strip()

        joined = " ".join(part for part in [description.lower(), error_hint.lower()] if part)
        if error == "unauthorized_client" and "requested grant is not allowed" in joined:
            return (
                " Для этого client_id выбранный grant type не разрешен. "
                "Нужно либо настроить отдельное OIDC-приложение в FAM с Allowed grant types "
                "Password или Client Credentials, либо положить готовый токен в FAM_ACCESS_TOKEN."
            )
        return ""

    async def _get_access_token(
        self,
        session: aiohttp.ClientSession,
        *,
        force_refresh: bool = False,
    ) -> str:
        if not self.configured:
            raise RuntimeError("FAM integration is not configured")

        static_token = str(settings.fam_access_token or "").strip()
        if static_token:
            self._token = static_token
            self._token_expires_at = time.time() + 365 * 24 * 60 * 60
            return static_token

        if not force_refresh and self._token_cache_valid():
            return self._token

        async with self._token_lock:
            if not force_refresh and self._token_cache_valid():
                return self._token

            token_url = await self._discover_token_url(session)
            payload = self._grant_payload()

            async with session.post(
                token_url,
                data=payload,
                ssl=self._request_ssl(),
            ) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    detail = body if isinstance(body, dict) else {}
                    hint = self._token_error_hint(detail) if isinstance(detail, dict) else ""
                    raise RuntimeError(
                        f"FAM token request failed with HTTP {response.status}: {detail!r}{hint}"
                    )

            token = str(body.get("access_token") or "").strip()
            if not token:
                raise RuntimeError("FAM token response does not contain access_token")

            expires_in = int(body.get("expires_in") or 300)
            self._token = token
            self._token_expires_at = time.time() + max(60, expires_in)
            return token

    async def _graphql_request(
        self,
        session: aiohttp.ClientSession,
        token: str,
        query: str,
        *,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        graphql_url = self._graphql_url()
        if not graphql_url:
            raise RuntimeError("FAM GraphQL URL is not configured")

        headers = {
            "Authorization": f"Bearer {token}",
            "bearer_token": token,
            "Accept": "application/json",
        }

        async with session.get(
            graphql_url,
            params={"query": query},
            headers=headers,
            ssl=self._request_ssl(),
        ) as response:
            raw_text = await response.text()
            try:
                body = json.loads(raw_text) if raw_text else {}
            except json.JSONDecodeError:
                body = {"raw": raw_text}

            if response.status in {401, 403} and retry_auth and not str(settings.fam_access_token or "").strip():
                self._invalidate_token()
                fresh_token = await self._get_access_token(session, force_refresh=True)
                return await self._graphql_request(
                    session,
                    fresh_token,
                    query,
                    retry_auth=False,
                )

            if response.status >= 400:
                raise RuntimeError(
                    f"FAM GraphQL request failed with HTTP {response.status}: {body!r}"
                )

        return body if isinstance(body, dict) else {}

    @staticmethod
    def _extract_user_node(payload: dict[str, Any]) -> dict[str, Any] | None:
        edges = payload.get("data", {}).get("users", {}).get("edges", [])
        if not isinstance(edges, list):
            return None

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if isinstance(node, dict):
                return node
        return None

    @staticmethod
    def _build_present_status(username: str, node: dict[str, Any]) -> dict[str, Any]:
        first_name = str(node.get("firstname") or "").strip()
        middle_name = str(node.get("middlename") or "").strip()
        last_name = str(node.get("lastname") or "").strip()
        full_name = " ".join(part for part in [last_name, first_name, middle_name] if part)
        email = str(node.get("email") or "").strip()
        active_raw = node.get("active")
        active = None if active_raw is None else bool(active_raw)

        if active is False:
            return _empty_status(
                configured=True,
                username=username,
                state="inactive",
                summary="Пользователь найден в Avanpost FAM, но учетная запись неактивна.",
                detail="Учетная запись присутствует в FAM, однако помечена как неактивная.",
                full_name=full_name,
                email=email,
                active=active,
            )

        return _empty_status(
            configured=True,
            username=username,
            state="present",
            summary="Пользователь найден в Avanpost FAM.",
            detail="Учетная запись найдена в Avanpost FAM по логину.",
            full_name=full_name,
            email=email,
            active=active,
        )

    async def _lookup_user_with_session(
        self,
        session: aiohttp.ClientSession,
        token: str,
        username: str,
    ) -> dict[str, Any]:
        safe_username = str(username or "").strip()
        if not safe_username:
            return _empty_status(
                configured=self.configured,
                username="",
                state="missing",
                summary="Логин пользователя не задан.",
                detail="Для проверки в Avanpost FAM нужен логин пользователя.",
            )

        full_query = (
            "query { "
            f'users (where: {{username: {{eq: {_graphql_string(safe_username)}}}}}) '
            "{ edges { node { username firstname lastname middlename email active } } } "
            "}"
        )
        minimal_query = (
            "query { "
            f'users (where: {{username: {{eq: {_graphql_string(safe_username)}}}}}) '
            "{ edges { node { username } } } "
            "}"
        )

        body = await self._graphql_request(session, token, full_query)
        if body.get("errors"):
            logger.warning(
                "FAM GraphQL full query returned errors for %r: %r",
                safe_username,
                body["errors"],
            )
            body = await self._graphql_request(session, token, minimal_query)

        node = self._extract_user_node(body)
        if not node:
            return _empty_status(
                configured=True,
                username=safe_username,
                state="missing",
                summary="Пользователь не найден в Avanpost FAM.",
                detail="По указанному логину в Avanpost FAM учетная запись не найдена.",
            )

        return self._build_present_status(safe_username, node)

    def _get_cached_status(self, username: str) -> dict[str, Any] | None:
        key = str(username or "").strip().lower()
        if not key:
            return None

        cached = self._user_cache.get(key)
        if not cached:
            return None

        if (time.time() - cached[0]) >= self._cache_ttl():
            self._user_cache.pop(key, None)
            return None

        return dict(cached[1])

    def _set_cached_status(self, username: str, status: dict[str, Any]) -> None:
        key = str(username or "").strip().lower()
        if not key:
            return
        self._user_cache[key] = (time.time(), dict(status))

    def invalidate_user(self, username: str) -> None:
        """Сбрасывает кэш по логину — следующий lookup сходит в FAM заново."""
        key = str(username or "").strip().lower()
        if key:
            self._user_cache.pop(key, None)

    async def lookup_user(self, username: str) -> dict[str, Any]:
        result = await self.lookup_users([username])
        return result.get(str(username or "").strip(), self.not_configured_status(username))

    async def lookup_users(self, usernames: list[str]) -> dict[str, dict[str, Any]]:
        clean_usernames: list[str] = []
        seen: set[str] = set()
        for username in usernames:
            safe_username = str(username or "").strip()
            if not safe_username:
                continue
            key = safe_username.lower()
            if key in seen:
                continue
            seen.add(key)
            clean_usernames.append(safe_username)

        if not clean_usernames:
            return {}

        if not self.configured:
            return {username: self.not_configured_status(username) for username in clean_usernames}

        results: dict[str, dict[str, Any]] = {}
        pending: list[str] = []

        for username in clean_usernames:
            cached = self._get_cached_status(username)
            if cached is not None:
                results[username] = cached
            else:
                pending.append(username)

        if not pending:
            return results

        try:
            async with aiohttp.ClientSession(timeout=self._timeout()) as session:
                token = await self._get_access_token(session)
                semaphore = asyncio.Semaphore(8)

                async def worker(safe_username: str) -> tuple[str, dict[str, Any]]:
                    async with semaphore:
                        status = await self._lookup_user_with_session(session, token, safe_username)
                        self._set_cached_status(safe_username, status)
                        return safe_username, status

                fetched = await asyncio.gather(*(worker(username) for username in pending))
        except Exception as exc:
            logger.exception("FAM lookup failed for %d users", len(pending))
            error_status = self.error_status(detail=str(exc))
            for username in pending:
                results[username] = dict(error_status, username=username)
            return results

        for username, status in fetched:
            results[username] = status

        return results

    def not_configured_status(self, username: str = "") -> dict[str, Any]:
        return _empty_status(
            configured=False,
            username=str(username or "").strip(),
            state="not_configured",
            summary="Прямая проверка Avanpost FAM не настроена.",
            detail="Нужны URL FAM и подходящий способ авторизации: Password, Client Credentials или готовый токен.",
        )

    def error_status(self, *, username: str = "", detail: str = "") -> dict[str, Any]:
        safe_detail = str(detail or "").strip()
        return _empty_status(
            configured=self.configured,
            username=str(username or "").strip(),
            state="error",
            summary="Не удалось проверить пользователя в Avanpost FAM.",
            detail=safe_detail or "Ошибка соединения с FAM API.",
        )


_fam_client: FamClient | None = None


def get_fam_client() -> FamClient:
    global _fam_client
    if _fam_client is None:
        _fam_client = FamClient()
    return _fam_client
