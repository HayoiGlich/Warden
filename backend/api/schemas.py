from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, List

from pydantic import BaseModel, Field, ConfigDict


class EventOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    event_id: int
    time_created: Optional[datetime] = None

    collector: str = ""

    computer: Optional[str] = None
    username: Optional[str] = None

    logon_type: Optional[str] = None
    ip_address: Optional[str] = None
    workstation_name: Optional[str] = None
    target_domain: Optional[str] = None

    groups: list[str] = Field(default_factory=list)

    message: str = ""
    status: Optional[str] = None
    failure_reason: Optional[str] = None
    authentication_package: Optional[str] = None
    process_id: Optional[str | int] = None
    thread_id: Optional[str | int] = None


class SearchStats(BaseModel):
    returned: int
    successful: int
    failed: int
    limit: int
    offset: int
    total: int


class CollectorStatusOut(BaseModel):
    name: str = ""
    host: str = ""
    connected: bool = False
    error: str = ""


class CollectorsResponse(BaseModel):
    success: bool = True
    connected: int = 0
    total: int = 0
    collectors: List[CollectorStatusOut] = Field(default_factory=list)


class SearchResponse(BaseModel):
    success: bool = True
    ad_connected: bool
    events: list[EventOut]
    collectors: List[CollectorStatusOut] = Field(default_factory=list)
    stats: SearchStats


class ADUserOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = ""
    displayName: str = ""
    givenName: str = ""
    sn: str = ""
    mail: str = ""


class ADSuggestResponse(BaseModel):
    success: bool
    users: list[ADUserOut]


class SystemInfo(BaseModel):
    hostname: str
    ip_address: str
    platform: str
    release: str


class SystemResponse(BaseModel):
    system_info: SystemInfo
    active_directory: Literal["connected", "disconnected"]


class HealthResponse(BaseModel):
    status: Literal["running"]
    timestamp: datetime
    database: Literal["connected", "disconnected"]
    active_directory: Literal["connected", "disconnected"]


class ErrorResponse(BaseModel):
    success: bool = False
    detail: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ADContainerOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    type: str = ""
    dn: str = ""
    description: str = ""


class ADGroupsResponse(BaseModel):
    success: bool
    username: str
    displayName: str = ""
    container: ADContainerOut = Field(default_factory=ADContainerOut)
    groups: List[str]


# =========================================================
# AD ADMIN (создание/редактирование пользователей)
# =========================================================


class ADOrgUnitOut(BaseModel):
    name: str = ""
    dn: str = ""
    description: str = ""


class ADGroupRefOut(BaseModel):
    name: str = ""
    dn: str = ""
    description: str = ""


class ADOusResponse(BaseModel):
    success: bool
    ous: List[ADOrgUnitOut] = Field(default_factory=list)


class ADGroupListResponse(BaseModel):
    success: bool
    groups: List[ADGroupRefOut] = Field(default_factory=list)


class ADOuUserOut(BaseModel):
    login: str = ""
    firstName: str = ""
    lastName: str = ""
    displayName: str = ""
    email: str = ""
    employeeNumber: str = ""
    enabled: bool = False
    passwordSet: bool = True
    accountExpires: str = ""
    whenCreated: str = ""
    ou: str = ""
    groups: List[str] = Field(default_factory=list)


class ADOuUsersResponse(BaseModel):
    success: bool
    users: List[ADOuUserOut] = Field(default_factory=list)


class ADUserDetailResponse(BaseModel):
    success: bool
    login: str = ""
    firstName: str = ""
    lastName: str = ""
    displayName: str = ""
    email: str = ""
    employeeNumber: str = ""
    enabled: bool = False
    accountExpires: str = ""
    whenCreated: str = ""
    dn: str = ""
    ou: str = ""
    container: ADContainerOut = Field(default_factory=ADContainerOut)
    groups: List[ADGroupRefOut] = Field(default_factory=list)


class ADUserCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(..., min_length=1, max_length=100)
    firstName: str = Field("", max_length=100)
    lastName: str = Field("", max_length=100)
    displayName: str = Field("", max_length=200)
    email: str = Field("", max_length=200)
    employeeNumber: str = Field("", max_length=100)
    accountExpires: str = Field("", max_length=20)
    password: str = Field("", max_length=200)
    ou: str = Field("", max_length=1000)
    groups: List[str] = Field(default_factory=list)
    enabled: bool = True


class ADUserUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(..., min_length=1, max_length=100)
    firstName: Optional[str] = Field(None, max_length=100)
    lastName: Optional[str] = Field(None, max_length=100)
    displayName: Optional[str] = Field(None, max_length=200)
    email: Optional[str] = Field(None, max_length=200)
    employeeNumber: Optional[str] = Field(None, max_length=100)
    accountExpires: Optional[str] = Field(None, max_length=20)
    ou: str = Field("", max_length=1000)
    addGroups: List[str] = Field(default_factory=list)
    removeGroups: List[str] = Field(default_factory=list)
    setGroups: Optional[List[str]] = None
    newPassword: str = Field("", max_length=200)
    enabled: Optional[bool] = None


class ADBulkCreateRequest(BaseModel):
    users: List[ADUserCreate] = Field(default_factory=list)


class ADBulkUpdateRequest(BaseModel):
    users: List[ADUserUpdate] = Field(default_factory=list)


class ADBulkResultItem(BaseModel):
    login: str = ""
    success: bool = False
    action: str = ""  # created | updated | error
    detail: str = ""
    warnings: List[str] = Field(default_factory=list)


class ADUserMutationResponse(BaseModel):
    success: bool
    result: ADBulkResultItem


class ADBulkResponse(BaseModel):
    success: bool
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    results: List[ADBulkResultItem] = Field(default_factory=list)


class FamStatusOut(BaseModel):
    configured: bool = False
    state: str = ""
    summary: str = ""
    detail: str = ""
    username: str = ""
    full_name: str = ""
    email: str = ""
    active: Optional[bool] = None


class FamStatusResponse(BaseModel):
    success: bool
    status: FamStatusOut


class FamSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(..., min_length=1, max_length=100)


# ---- Yandex Cloud: отчёт по ВМ ----


class YcReportRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    created_at: str = ""
    platform: str = ""
    cpu_type: str = ""
    cores: float = 0
    ram_gb: float = 0
    ssd_gb: float = 0
    hdd_gb: float = 0
    disk_gb: float = 0  # суммарно, для совместимости; в отчёте не используется
    snapshots_gb: float = 0


class YcReportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rows: List[YcReportRow] = Field(default_factory=list)


class YcTariffIn(BaseModel):
    """Тарифы (цена за час, ₽). Все поля опциональны — пустое считается 0."""

    model_config = ConfigDict(extra="ignore")

    cpu_100: float = 0
    cpu_50: float = 0
    cpu_hi: float = 0
    ram: float = 0
    ram_hi: float = 0
    ssd: float = 0
    ssd_io: float = 0
    hdd: float = 0
