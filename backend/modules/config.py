from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(override=True)


class Settings(BaseSettings):
    # Database settings
    db_host: str = Field(..., alias="DB_HOST")
    db_port: int = Field(5432, alias="DB_PORT")
    db_name: str = Field(..., alias="DB_NAME")
    db_user: str = Field(..., alias="DB_USER")
    db_password: str = Field(..., alias="DB_PASSWORD")

    # AD settings
    ad_server: str = Field(..., alias="AD_SERVER")
    ad_domain: str = Field(..., alias="AD_DOMAIN")
    ad_username: str = Field(
        ...,
        alias="AD_USER",
        validation_alias=AliasChoices("AD_USER", "AD_USERNAME"),
    )
    ad_password: str = Field(..., alias="AD_PASSWORD")

    # AD secure channel (нужен для создания учёток и смены пароля).
    # Для операций с паролем обязателен зашифрованный канал — LDAPS или StartTLS:
    #   LDAPS (порт 636):     AD_USE_SSL=1
    #   StartTLS поверх 389:  AD_START_TLS=1
    # Если ничего не включено — группы/OU/атрибуты меняются, но не пароли.
    ad_use_ssl: bool = Field(False, alias="AD_USE_SSL")
    ad_start_tls: bool = Field(False, alias="AD_START_TLS")
    ad_port: int | None = Field(None, alias="AD_PORT")
    # Метод bind: "simple" (по умолчанию) или "ntlm". NTLM — только способ
    # аутентификации (DOMAIN\\user), на шифрование канала он не влияет.
    ad_auth: str = Field("simple", alias="AD_AUTH")
    # Проверять ли сертификат сервера при LDAPS/StartTLS. Для внутреннего AD с
    # самоподписанным сертификатом оставьте False (канал всё равно шифруется).
    ad_tls_validate: bool = Field(False, alias="AD_TLS_VALIDATE")

    # Куда по умолчанию класть новые учётки (DN OU). Если пусто — берётся
    # дефолтный контейнер CN=Users,<baseDN>.
    ad_default_user_ou: str = Field("", alias="AD_DEFAULT_USER_OU")
    # Суффикс UPN для userPrincipalName (например staff.local). Если пусто —
    # используется AD_DOMAIN.
    ad_upn_suffix: str = Field("", alias="AD_UPN_SUFFIX")

    # App settings
    app_host: str = Field("", alias="APP_HOST")
    app_port: int = Field(8000, alias="APP_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    disable_ad: bool = Field(False, alias="DISABLE_AD")

    # ---- App database (auth + настройки приложения) ----
    # Отдельный Postgres приложения (НЕ базы коллекторов). По умолчанию —
    # сервис `db` из docker-compose; можно указать внешний.
    app_db_host: str = Field("db", alias="APP_DB_HOST")
    app_db_port: int = Field(5432, alias="APP_DB_PORT")
    app_db_name: str = Field("appdb", alias="APP_DB_NAME")
    app_db_user: str = Field("app", alias="APP_DB_USER")
    app_db_password: str = Field("app", alias="APP_DB_PASSWORD")

    # ---- Авторизация / сессии ----
    # Ключ подписи cookie-сессий. В проде задайте случайный через env!
    session_secret: str = Field("change-me-please-in-prod", alias="SESSION_SECRET")
    session_cookie: str = Field("winlog_session", alias="SESSION_COOKIE")
    session_max_age: int = Field(28800, alias="SESSION_MAX_AGE")  # 8 часов
    # Пускать AD-пользователей только из этой группы (пусто = любой прошедший bind)
    ad_login_group: str = Field("", alias="AD_LOGIN_GROUP")
    # Дефолтный локальный админ (создаётся при первом старте, если users пуст)
    default_admin_user: str = Field("admin", alias="DEFAULT_ADMIN_USER")
    default_admin_password: str = Field("admin1", alias="DEFAULT_ADMIN_PASSWORD")

    # ---- OIDC / вход через Avanpost (2FA) ----
    # Внешний IdP (Avanpost) как поставщик входа с двухфакторкой.
    # Discovery: <issuer>/.well-known/openid-configuration
    oidc_enabled: bool = Field(False, alias="OIDC_ENABLED")
    oidc_client_id: str = Field("", alias="OIDC_CLIENT_ID")
    oidc_client_secret: str = Field("", alias="OIDC_CLIENT_SECRET")
    oidc_auth_url: str = Field("", alias="OIDC_AUTH_URL")
    oidc_token_url: str = Field("", alias="OIDC_TOKEN_URL")
    oidc_userinfo_url: str = Field("", alias="OIDC_USERINFO_URL")
    oidc_end_session_url: str = Field("", alias="OIDC_END_SESSION_URL")
    oidc_redirect_uri: str = Field("", alias="OIDC_REDIRECT_URI")
    oidc_scope: str = Field("openid profile email", alias="OIDC_SCOPE")
    # Клейм, соответствующий логину AD (sAMAccountName). Обычно
    # preferred_username; иногда sub/email.
    oidc_username_claim: str = Field("preferred_username", alias="OIDC_USERNAME_CLAIM")
    # Заголовок кнопки на форме входа.
    oidc_button_label: str = Field(
        "Войти через Avanpost (2FA)", alias="OIDC_BUTTON_LABEL"
    )
    # Проверять TLS-сертификат IdP (для самоподписанного — False).
    oidc_verify_ssl: bool = Field(False, alias="OIDC_VERIFY_SSL")

    # ---- Avanpost FAM (GraphQL API) — проверка попадания учётки в Avanpost ----
    # Адрес и креды по умолчанию берутся из OIDC-настроек (тот же Avanpost, что
    # и для входа с 2FA), поэтому обычно ничего дополнительно задавать не нужно.
    # При необходимости всё переопределяется через FAM_*.
    fam_base_url: str = Field("", alias="FAM_BASE_URL")
    fam_graphql_url: str = Field("", alias="FAM_GRAPHQL_URL")
    fam_token_url: str = Field("", alias="FAM_TOKEN_URL")
    fam_access_token: str = Field("", alias="FAM_ACCESS_TOKEN")
    fam_client_id: str = Field("", alias="FAM_CLIENT_ID")
    fam_client_secret: str = Field("", alias="FAM_CLIENT_SECRET")
    # Способ получения токена для GraphQL API: client_credentials (по умолчанию,
    # т.к. переиспользуем client_id/secret из OIDC) или password.
    fam_grant_type: str = Field("client_credentials", alias="FAM_GRANT_TYPE")
    fam_token_scope: str = Field("", alias="FAM_TOKEN_SCOPE")
    fam_api_username: str = Field("", alias="FAM_API_USERNAME")
    fam_api_password: str = Field("", alias="FAM_API_PASSWORD")
    fam_verify_ssl: bool = Field(False, alias="FAM_VERIFY_SSL")
    fam_timeout_seconds: int = Field(10, alias="FAM_TIMEOUT_SECONDS")
    fam_cache_seconds: int = Field(60, alias="FAM_CACHE_SECONDS")

    # ---- Yandex Cloud (отчёт по ВМ / расчёт стоимости) ----
    yc_folder_id: str = Field("b1g63k911vl1iaaku9eh", alias="YC_FOLDER_ID")
    yc_key_file: str = Field("yandex_mig/authorized_key.json", alias="YC_KEY_FILE")
    yc_template_file: str = Field(
        "yandex_mig/Шаблон. Расчет ВМ цена. Общий.xlsx", alias="YC_TEMPLATE_FILE"
    )
    # Пусто — брать ВМ из всех зон фолдера; иначе фильтр по одной зоне.
    yc_report_zone: str = Field("", alias="YC_REPORT_ZONE")

    @model_validator(mode="after")
    def _derive_fam_defaults_from_oidc(self) -> "Settings":
        # Базовый адрес Avanpost (scheme://host) — из любого OIDC-URL.
        if not str(self.fam_base_url or "").strip():
            source = self.oidc_auth_url or self.oidc_token_url or self.oidc_userinfo_url
            if source:
                parts = urlsplit(source)
                if parts.scheme and parts.netloc:
                    self.fam_base_url = f"{parts.scheme}://{parts.netloc}"
        # Токен-эндпоинт и клиентские креды переиспользуем из OIDC.
        if not str(self.fam_token_url or "").strip() and self.oidc_token_url:
            self.fam_token_url = self.oidc_token_url
        if not str(self.fam_client_id or "").strip() and self.oidc_client_id:
            self.fam_client_id = self.oidc_client_id
        if not str(self.fam_client_secret or "").strip() and self.oidc_client_secret:
            self.fam_client_secret = self.oidc_client_secret
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()
