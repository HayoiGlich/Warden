"""Отчёт по виртуальным машинам Yandex Cloud.

Функции:
  * list_vms()          — перечень ВМ фолдера с ресурсами, снапшотами и ценой;
  * load_tariff()       — цены за сутки из шаблона (ЦПУ / ОЗУ / SSD);
  * build_report_xlsx() — заполняет шаблон выбранными ВМ и отдаёт байты xlsx.

Импорты Yandex Cloud SDK делаются лениво (внутри функций), чтобы приложение
стартовало даже без установленного пакета `yandexcloud`. Генерация xlsx требует
только openpyxl и файл-шаблон, поэтому работает независимо от доступа к облаку.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from backend.modules.config import settings

logger = logging.getLogger("log_analyzer")

GIB = 1024 ** 3

# platform_id -> человекочитаемое название (как в консоли YC).
PLATFORM_NAMES = {
    "standard-v1": "Intel Broadwell",
    "standard-v2": "Intel Cascade Lake",
    "standard-v3": "Intel Ice Lake",
    "standard-v3-t4": "Intel Ice Lake (T4)",
    "highfreq-v3": "Intel Ice Lake (Compute Optimized)",
    "gpu-standard-v1": "Intel Broadwell + GPU V100",
    "gpu-standard-v2": "Intel Cascade Lake + GPU V100",
    "gpu-standard-v3": "AMD EPYC + GPU A100",
    "gpu-standard-v3i": "Intel Ice Lake + GPU",
}

# Платформы с высокой частотой ЦПУ — тариф «Compute Optimized».
COMPUTE_OPTIMIZED_PLATFORMS = {"highfreq-v3"}


def _cpu_type(platform_id: str) -> str:
    return "Compute Optimized" if platform_id in COMPUTE_OPTIMIZED_PLATFORMS else "Обычный"


def _disk_kind(type_id: str) -> str:
    """Классификация диска по type_id: network-hdd → HDD, остальное → SSD."""
    return "hdd" if "hdd" in str(type_id or "").lower() else "ssd"


OS_TYPE_NAMES = {0: "—", 1: "Linux", 2: "Windows"}

STATUS_NAMES = {
    "PROVISIONING": "Provisioning",
    "RUNNING": "Running",
    "STOPPING": "Stopping",
    "STOPPED": "Stopped",
    "STARTING": "Starting",
    "RESTARTING": "Restarting",
    "UPDATING": "Updating",
    "ERROR": "Error",
    "CRASHED": "Crashed",
    "DELETING": "Deleting",
}


# --------------------------------------------------------------------------- #
# Пути и признак настроенности
# --------------------------------------------------------------------------- #


# Корень проекта (…/, где лежит main.py и backend/). Относительные пути к ключу
# и шаблону из .env считаем от него, а не от текущего рабочего каталога —
# иначе в Docker/сервисе (WORKDIR=/app) путь может не найтись.
_BASE_DIR = Path(__file__).resolve().parents[2]


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (_BASE_DIR / path)


def _key_path() -> Path:
    return _resolve(settings.yc_key_file).resolve()


def _template_path() -> Path:
    return _resolve(settings.yc_template_file).resolve()


def configured() -> bool:
    """Есть ли ключ сервисного аккаунта для доступа к облаку."""
    return _key_path().is_file()


def template_ready() -> bool:
    return _template_path().is_file()


# --------------------------------------------------------------------------- #
# Тариф из шаблона
# --------------------------------------------------------------------------- #


def load_tariff() -> Dict[str, float]:
    """Цена за сутки из настроенных тарифов (вкладка «Тарифы»).

    В хранилище цены заданы за час, поэтому за сутки умножаем на 24 — как и
    формула =B2*24 в листе «Тариф» шаблона.
    """
    from backend.modules.yc_tariff import yc_tariff

    v = yc_tariff.values()

    def day(key: str) -> float:
        try:
            return round(float(v.get(key) or 0) * 24, 6)
        except (TypeError, ValueError):
            return 0.0

    return {
        "cpu_day": day("cpu_100"),     # ЦПУ 100% (обычный), за сутки
        "cpu_day_half": day("cpu_50"),  # ЦПУ 50%
        "cpu_day_hi": day("cpu_hi"),   # ЦПУ высокопроизв. (Compute Optimized)
        "ram_day": day("ram"),         # ОЗУ (обычное)
        "ram_day_hi": day("ram_hi"),   # ОЗУ (высокопроизв.)
        "ssd_day": day("ssd"),         # SSD
        "hdd_day": day("hdd"),         # HDD
    }


def _cost_day(tariff: Dict[str, float], cpu_type: str, cores, ram_gb, ssd_gb, hdd_gb) -> float:
    """Стоимость ВМ в сутки: ЦПУ+ОЗУ по типу платформы, диски раздельно."""
    is_hi = cpu_type == "Compute Optimized"
    cpu_price = tariff["cpu_day_hi"] if is_hi else tariff["cpu_day"]
    ram_price = tariff["ram_day_hi"] if is_hi else tariff["ram_day"]
    return _round2(
        (cores or 0) * cpu_price
        + (ram_gb or 0) * ram_price
        + (ssd_gb or 0) * tariff["ssd_day"]
        + (hdd_gb or 0) * tariff["hdd_day"]
    )


# --------------------------------------------------------------------------- #
# Листинг ВМ
# --------------------------------------------------------------------------- #


def _load_key_data() -> dict:
    key_path = _key_path()
    if not key_path.is_file():
        raise RuntimeError(f"Не найден ключ сервисного аккаунта: {key_path}")
    with key_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _init_sdk():
    try:
        import yandexcloud
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise RuntimeError(
            "Библиотека yandexcloud не установлена. "
            "Установите зависимости (requirements-web.txt)."
        ) from exc

    return yandexcloud.SDK(service_account_key=_load_key_data())


def _round2(value: float) -> float:
    return round(float(value or 0), 2)


def _fmt_created(ts) -> str:
    """protobuf Timestamp -> ДД.ММ.ГГГГ. Пусто, если даты нет."""
    seconds = int(getattr(ts, "seconds", 0) or 0)
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%d.%m.%Y")


def list_vms() -> List[Dict[str, Any]]:
    """Перечень ВМ фолдера: ресурсы, размеры дисков/снапшотов и расчётная цена."""
    sdk = _init_sdk()

    from yandex.cloud.compute.v1.instance_pb2 import Instance
    from yandex.cloud.compute.v1.instance_service_pb2 import ListInstancesRequest
    from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub
    from yandex.cloud.compute.v1.disk_service_pb2 import GetDiskRequest
    from yandex.cloud.compute.v1.disk_service_pb2_grpc import DiskServiceStub
    from yandex.cloud.compute.v1.image_service_pb2 import GetImageRequest
    from yandex.cloud.compute.v1.image_service_pb2_grpc import ImageServiceStub
    from yandex.cloud.compute.v1.snapshot_service_pb2 import ListSnapshotsRequest
    from yandex.cloud.compute.v1.snapshot_service_pb2_grpc import SnapshotServiceStub

    inst_stub = sdk.client(InstanceServiceStub)
    disk_stub = sdk.client(DiskServiceStub)
    image_stub = sdk.client(ImageServiceStub)
    snap_stub = sdk.client(SnapshotServiceStub)

    fid = settings.yc_folder_id
    zone_filter = (settings.yc_report_zone or "").strip()
    tariff = load_tariff()

    # disk_id -> (size_bytes, source_image_id)
    disk_cache: Dict[str, tuple] = {}
    # image_id -> "Linux" | "Windows" | "—"
    image_os_cache: Dict[str, str] = {}
    # disk_id -> суммарный размер снапшотов в байтах
    snap_cache: Dict[str, int] = {}

    def disk_meta(disk_id: str) -> tuple:
        if not disk_id:
            return (0, "", "")
        if disk_id not in disk_cache:
            try:
                d = disk_stub.Get(GetDiskRequest(disk_id=disk_id))
                disk_cache[disk_id] = (
                    int(d.size),
                    getattr(d, "source_image_id", "") or "",
                    getattr(d, "type_id", "") or "",
                )
            except Exception as exc:
                logger.warning("YC: не удалось получить диск %s: %s", disk_id, exc)
                disk_cache[disk_id] = (0, "", "")
        return disk_cache[disk_id]

    def snapshots_bytes(disk_id: str) -> int:
        if not disk_id:
            return 0
        if disk_id not in snap_cache:
            total = 0
            try:
                resp = snap_stub.List(
                    ListSnapshotsRequest(
                        folder_id=fid,
                        filter=f'source_disk_id = "{disk_id}"',
                    )
                )
                for snap in resp.snapshots:
                    total += int(
                        getattr(snap, "storage_size", 0)
                        or getattr(snap, "disk_size", 0)
                        or 0
                    )
            except Exception as exc:
                logger.warning("YC: снапшоты диска %s недоступны: %s", disk_id, exc)
            snap_cache[disk_id] = total
        return snap_cache[disk_id]

    def resolve_os(image_id: str) -> str:
        if not image_id:
            return "—"
        if image_id in image_os_cache:
            return image_os_cache[image_id]
        label = "—"
        try:
            img = image_stub.Get(GetImageRequest(image_id=image_id))
            os_type = int(getattr(getattr(img, "os", None), "type", 0) or 0)
            label = OS_TYPE_NAMES.get(os_type, "—")
            if label == "—":
                label = _detect_os_from_family(getattr(img, "family", ""))
        except Exception as exc:
            logger.warning("YC: образ %s недоступен: %s", image_id, exc)
        image_os_cache[image_id] = label
        return label

    rows: List[Dict[str, Any]] = []
    page_token = ""
    while True:
        resp = inst_stub.List(
            ListInstancesRequest(folder_id=fid, page_size=100, page_token=page_token)
        )
        for inst in resp.instances:
            if zone_filter and inst.zone_id != zone_filter:
                continue

            disk_ids: List[str] = []
            boot_disk_id = ""
            if getattr(inst, "boot_disk", None) and getattr(inst.boot_disk, "disk_id", ""):
                boot_disk_id = inst.boot_disk.disk_id
                disk_ids.append(boot_disk_id)
            for sd in getattr(inst, "secondary_disks", []):
                if getattr(sd, "disk_id", ""):
                    disk_ids.append(sd.disk_id)

            ssd_bytes = 0
            hdd_bytes = 0
            total_snap_bytes = 0
            for disk_id in disk_ids:
                size_b, _img, type_id = disk_meta(disk_id)
                if _disk_kind(type_id) == "hdd":
                    hdd_bytes += size_b
                else:
                    ssd_bytes += size_b
                total_snap_bytes += snapshots_bytes(disk_id)

            boot_image_id = disk_meta(boot_disk_id)[1] if boot_disk_id else ""

            cores = int(inst.resources.cores)
            core_fraction = int(inst.resources.core_fraction)
            ram_gb = _round2(int(inst.resources.memory) / GIB)
            ssd_gb = _round2(ssd_bytes / GIB)
            hdd_gb = _round2(hdd_bytes / GIB)
            snap_gb = _round2(total_snap_bytes / GIB)

            cpu_type = _cpu_type(inst.platform_id)
            cost_day = _cost_day(tariff, cpu_type, cores, ram_gb, ssd_gb, hdd_gb)
            cost_year = _round2(cost_day * 365)

            rows.append(
                {
                    "id": inst.id,
                    "name": inst.name,
                    "created_at": _fmt_created(getattr(inst, "created_at", None)),
                    "zone_id": inst.zone_id,
                    "status": _fmt_status(Instance, inst.status),
                    "platform": PLATFORM_NAMES.get(inst.platform_id, inst.platform_id or ""),
                    "platform_id": inst.platform_id,
                    "cpu_type": cpu_type,
                    "os": resolve_os(boot_image_id),
                    "cores": cores,
                    "core_fraction": core_fraction,
                    "ram_gb": ram_gb,
                    "ssd_gb": ssd_gb,
                    "hdd_gb": hdd_gb,
                    "disk_gb": _round2((ssd_bytes + hdd_bytes) / GIB),
                    "snapshots_gb": snap_gb,
                    "cost_day": cost_day,
                    "cost_year": cost_year,
                }
            )

        page_token = resp.next_page_token
        if not page_token:
            break

    rows.sort(key=lambda r: str(r["name"]).lower())
    return rows


# --------------------------------------------------------------------------- #
# Кэш листинга ВМ (обращение к облаку медленное — не дёргаем на каждый заход)
# --------------------------------------------------------------------------- #

# {"data": [...], "ts": epoch_seconds}. Живёт в памяти процесса до обновления.
_vms_cache: Dict[str, Any] = {"data": None, "ts": 0.0}


def get_vms(force: bool = False) -> tuple:
    """Отдаёт (список_ВМ, время_загрузки_epoch). Из кэша, если он есть и не
    запрошено принудительное обновление."""
    if not force and _vms_cache["data"] is not None:
        return _vms_cache["data"], _vms_cache["ts"]
    import time

    data = list_vms()
    _vms_cache["data"] = data
    _vms_cache["ts"] = time.time()
    return data, _vms_cache["ts"]


def cached_at() -> float:
    return float(_vms_cache["ts"] or 0.0)


def has_cache() -> bool:
    return _vms_cache["data"] is not None


def recompute_cached_costs() -> None:
    """Пересчитывает стоимость в кэше по актуальному тарифу — без похода в облако
    (нужно после изменения тарифов, чтобы не перезагружать все ВМ)."""
    if _vms_cache["data"] is None:
        return
    tariff = load_tariff()
    for vm in _vms_cache["data"]:
        vm["cost_day"] = _cost_day(
            tariff,
            vm.get("cpu_type", ""),
            vm.get("cores", 0),
            vm.get("ram_gb", 0),
            vm.get("ssd_gb", 0),
            vm.get("hdd_gb", 0),
        )
        vm["cost_year"] = _round2(vm["cost_day"] * 365)


def _fmt_status(instance_cls, status_int: int) -> str:
    try:
        name = instance_cls.Status.Name(status_int)
    except Exception:
        return str(status_int)
    return STATUS_NAMES.get(name, name.capitalize())


def _detect_os_from_family(family: str) -> str:
    f = (family or "").lower()
    if not f:
        return "—"
    if "win" in f:
        return "Windows"
    linux_markers = (
        "ubuntu", "debian", "centos", "rocky", "almalinux", "fedora", "rhel",
        "redhat", "suse", "opensuse", "linux", "astra", "alt", "oracle", "amazon",
    )
    if any(m in f for m in linux_markers):
        return "Linux"
    return "—"


# --------------------------------------------------------------------------- #
# Генерация отчёта из шаблона
# --------------------------------------------------------------------------- #


def build_report_xlsx(rows: List[Dict[str, Any]]) -> bytes:
    """Заполняет шаблон выбранными ВМ и возвращает содержимое xlsx (байты).

    Пересобираются листы «Список ВМ» и «Снимки» (сырые данные) и «Общий»
    (формулы расчёта цены). Лист «Тариф» из шаблона сохраняется как есть.

    В «Список ВМ» теперь есть платформа, тип ЦПУ (Обычный / Compute Optimized)
    и раздельные объёмы SSD и HDD. Цена в «Общий» считается по типу платформы
    (обычный/высокопроизводительный тариф ЦПУ и ОЗУ) и раздельно по SSD/HDD.
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill

    path = _template_path()
    if not path.is_file():
        raise RuntimeError(f"Не найден шаблон отчёта: {path}")

    wb = load_workbook(path, data_only=False)
    ws_list = wb["Список ВМ"]
    ws_snap = wb["Снимки"]
    ws_total = wb["Общий"]

    # Подставляем актуальные цены (вкладка «Тарифы») в лист «Тариф» — строка 2
    # (за час), строка 3 (за сутки) в шаблоне пересчитается формулой =…*24.
    from backend.modules.yc_tariff import FIELD_CELLS, yc_tariff

    ws_tariff = wb["Тариф"]
    tariff_values = yc_tariff.values()
    for key, cell in FIELD_CELLS.items():
        ws_tariff[cell] = tariff_values.get(key, 0.0)

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E2EFDA")

    list_headers = [
        "Имя ВМ", "Дата создания", "Платформа", "Тип ЦПУ", "ЦПУ, шт.", "ОЗУ, Гб",
        "SSD, Гб", "HDD, Гб",
    ]
    snap_headers = ["Имя ВМ", "Снимки, Гб"]
    total_headers = [
        "Имя ВМ", "Дата создания", "Платформа", "Тип ЦПУ", "ЦПУ, шт.", "ОЗУ, Гб",
        "SSD, Гб", "HDD, Гб", "Снимки, Гб", "Итого за ВМ в день, ₽",
        "Итого за ВМ в год, ₽", "Итого за всё в год, ₽",
    ]

    def reset(ws, headers):
        if ws.max_row >= 1:
            ws.delete_rows(1, ws.max_row)
        for col, title in enumerate(headers, start=1):
            cell = ws.cell(1, col, title)
            cell.font = header_font
            cell.fill = header_fill

    reset(ws_list, list_headers)
    reset(ws_snap, snap_headers)
    reset(ws_total, total_headers)

    for idx, row in enumerate(rows, start=2):
        name = str(row.get("name") or "")
        created_at = str(row.get("created_at") or "")
        platform = str(row.get("platform") or "")
        cpu_type = str(row.get("cpu_type") or "Обычный")
        cores = row.get("cores") or 0
        ram_gb = row.get("ram_gb") or 0
        ssd_gb = row.get("ssd_gb") or 0
        hdd_gb = row.get("hdd_gb") or 0
        snap_gb = row.get("snapshots_gb") or 0

        ws_list.cell(idx, 1, name)
        ws_list.cell(idx, 2, created_at)
        ws_list.cell(idx, 3, platform)
        ws_list.cell(idx, 4, cpu_type)
        ws_list.cell(idx, 5, cores)
        ws_list.cell(idx, 6, ram_gb)
        ws_list.cell(idx, 7, ssd_gb)
        ws_list.cell(idx, 8, hdd_gb)

        ws_snap.cell(idx, 1, name)
        ws_snap.cell(idx, 2, snap_gb)

        # Тариф ЦПУ/ОЗУ зависит от типа платформы: обычный (B3/E3) либо
        # высокопроизводительный / Compute Optimized (D3/F3).
        is_hi = cpu_type == "Compute Optimized"
        cpu_cell = "$D$3" if is_hi else "$B$3"
        ram_cell = "$F$3" if is_hi else "$E$3"

        # A..H — зеркало «Список ВМ», I — снимки, J/K/L — расчёт.
        for col, letter in enumerate("ABCDEFGH", start=1):
            ws_total.cell(idx, col, f"='Список ВМ'!{letter}{idx}")
        ws_total.cell(idx, 9, f"='Снимки'!B{idx}")
        ws_total.cell(
            idx,
            10,
            f"=E{idx}*Тариф!{cpu_cell}+F{idx}*Тариф!{ram_cell}"
            f"+G{idx}*Тариф!$G$3+H{idx}*Тариф!$I$3",
        )
        ws_total.cell(idx, 11, f"=J{idx}*365")

    if rows:
        last = len(rows) + 1
        ws_total.cell(2, 12, f"=SUM(K2:K{last})")

    # Ширина колонок для читабельности + закреплённая шапка.
    for ws, headers in (
        (ws_list, list_headers),
        (ws_snap, snap_headers),
        (ws_total, total_headers),
    ):
        for col_idx, title in enumerate(headers, start=1):
            letter = ws.cell(1, col_idx).column_letter
            ws.column_dimensions[letter].width = max(12, min(32, len(title) + 3))
        ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
