import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yandexcloud
from yandex.cloud.compute.v1.disk_service_pb2 import GetDiskRequest
from yandex.cloud.compute.v1.disk_service_pb2_grpc import DiskServiceStub
from yandex.cloud.compute.v1.instance_service_pb2 import (
    AttachedDiskSpec,
    CreateInstanceRequest,
    GetInstanceRequest,
    ListInstancesRequest,
    NetworkInterfaceSpec,
    OneToOneNatSpec,
    PrimaryAddressSpec,
    ResourcesSpec,
)
from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub
from yandex.cloud.compute.v1.snapshot_service_pb2 import (
    CreateSnapshotRequest,
    ListSnapshotsRequest,
)
from yandex.cloud.compute.v1.snapshot_service_pb2_grpc import SnapshotServiceStub
from yandex.cloud.operation.operation_service_pb2 import GetOperationRequest
from yandex.cloud.operation.operation_service_pb2_grpc import OperationServiceStub

# ==================== КОНФИГУРАЦИЯ ====================
FOLDER_ID = "b1g63k911vl1iaaku9eh"
KEY_FILE = "authorized_key.json"
TARGET_ZONE_FOR_REPORT = "ru-central1-b"

DEFAULT_RESTORE_SUFFIX = "-restored"
DEFAULT_COPY_SUFFIX = "-copy"
DEFAULT_NAT_IPV4 = True

# Ожидание операций
OP_TIMEOUT_SEC = 3600
OP_POLL_SEC = 2.0


# ==================== SDK ====================
def init_sdk() -> yandexcloud.SDK:
    """Инициализирует SDK Yandex Cloud."""
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        key_data = json.load(f)
    return yandexcloud.SDK(service_account_key=key_data)


sdk = init_sdk()
instance_service = sdk.client(InstanceServiceStub)
snapshot_service = sdk.client(SnapshotServiceStub)
disk_service = sdk.client(DiskServiceStub)
operation_service = sdk.client(OperationServiceStub)


# ==================== КЭШИ ====================
# disk_id -> (name, size_bytes, type_id)
_disk_cache: Dict[str, Tuple[str, int, str]] = {}
# disk_id -> (latest_snapshot_date_str, latest_snapshot_id)
_snapshot_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}


@dataclass(frozen=True)
class DiskSnapshotInfo:
    disk_id: str
    disk_type: str  # SYSTEM | SECONDARY
    disk_name: str
    latest_snapshot_date: Optional[str]
    latest_snapshot_id: Optional[str]
    disk_size_bytes: Optional[int] = None
    disk_type_id: Optional[str] = None


def _safe_short(disk_id: str, n: int = 8) -> str:
    return disk_id[:n] if disk_id else "unknown"


def wait_operation(
    operation_id: str, timeout_sec: int = OP_TIMEOUT_SEC, poll_sec: float = OP_POLL_SEC
) -> None:
    """
    Ждём завершения операции YC. Бросает исключение при ошибке/таймауте.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        op = operation_service.Get(GetOperationRequest(operation_id=operation_id))
        if op.done:
            # если операция завершилась ошибкой — op.error
            if getattr(op, "error", None) and op.error.code:
                raise RuntimeError(
                    f"YC operation failed: {op.error.code} {op.error.message}"
                )
            return
        time.sleep(poll_sec)

    raise TimeoutError(f"Timeout waiting operation {operation_id}")


def get_disk_meta(disk_id: str) -> Tuple[str, Optional[int], Optional[str]]:
    """
    Возвращает (имя_диска, размер_в_байтах, type_id).
    Кэширует результат, чтобы не дергать API повторно.
    """
    if not disk_id:
        return ("Без имени", None, None)

    if disk_id in _disk_cache:
        name, size, type_id = _disk_cache[disk_id]
        # size в кэше хранится как int (0 если неизвестно)
        return name, (size or None), (type_id or None)

    try:
        disk_info = disk_service.Get(GetDiskRequest(disk_id=disk_id))
        name = disk_info.name or f"Без имени ({_safe_short(disk_id)})"
        size = int(getattr(disk_info, "size", 0)) or 0
        type_id = getattr(disk_info, "type_id", "") or ""
        _disk_cache[disk_id] = (name, size, type_id)
        return name, (size or None), (type_id or None)
    except Exception:
        return (f"Ошибка ({_safe_short(disk_id)})", None, None)


def get_latest_snapshot_for_disk(disk_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Возвращает (дата_снапшота_строкой, snapshot_id) для последнего снапшота диска.
    Кэширует результат.
    """
    if not disk_id:
        return None, None

    if disk_id in _snapshot_cache:
        return _snapshot_cache[disk_id]

    try:
        snap_request = ListSnapshotsRequest(
            folder_id=FOLDER_ID,
            filter=f'source_disk_id = "{disk_id}"',
        )
        snapshots = snapshot_service.List(snap_request).snapshots
        if not snapshots:
            _snapshot_cache[disk_id] = (None, None)
            return None, None

        latest = max(snapshots, key=lambda s: s.created_at.seconds)
        latest_date = datetime.fromtimestamp(latest.created_at.seconds).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        _snapshot_cache[disk_id] = (latest_date, latest.id)
        return latest_date, latest.id
    except Exception:
        _snapshot_cache[disk_id] = (None, None)
        return None, None


def ensure_latest_snapshot_for_disk(
    disk_id: str,
    disk_name: str,
    *,
    wait: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Возвращает последний снапшот. Если нет — создаёт новый снапшот диска.
    (date_str, snapshot_id)
    """
    snap_date, snap_id = get_latest_snapshot_for_disk(disk_id)
    if snap_id:
        return snap_date, snap_id

    safe_name = (disk_name or f"disk-{_safe_short(disk_id)}").replace(" ", "-")[:30]
    snap_name = (
        f"auto-{safe_name}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )

    print(
        f"[ИНФО] Снапшота нет для диска '{disk_name}'. Создаю новый снапшот: {snap_name}"
    )

    op = snapshot_service.Create(
        CreateSnapshotRequest(
            folder_id=FOLDER_ID,
            name=snap_name,
            disk_id=disk_id,
        )
    )

    if wait:
        wait_operation(op.id)

    # сброс кэша и перечитывание latest
    _snapshot_cache.pop(disk_id, None)
    snap_date, snap_id = get_latest_snapshot_for_disk(disk_id)
    if not snap_id:
        raise RuntimeError(
            f"Snapshot create operation finished, but snapshot not found for disk {disk_id}"
        )

    print(f"[УСПЕХ] Снапшот создан: {snap_id} ({snap_date})")
    return snap_date, snap_id


def generate_vm_report(target_zone: str) -> Dict[str, Any]:
    print(f"[ИНФО] Формирую отчёт по зоне: {target_zone}")

    instances_request = ListInstancesRequest(folder_id=FOLDER_ID)
    all_vms = instance_service.List(instances_request).instances

    report: Dict[str, Any] = {"vms": []}

    for vm in all_vms:
        if vm.zone_id != target_zone:
            continue

        vm_details = instance_service.Get(GetInstanceRequest(instance_id=vm.id))

        disks: List[DiskSnapshotInfo] = []

        # Системный диск
        boot_disk_id = vm_details.boot_disk.disk_id
        if boot_disk_id:
            disk_name, disk_size, disk_type_id = get_disk_meta(boot_disk_id)
            snap_date, snap_id = get_latest_snapshot_for_disk(boot_disk_id)
            disks.append(
                DiskSnapshotInfo(
                    disk_id=boot_disk_id,
                    disk_type="SYSTEM",
                    disk_name=disk_name,
                    latest_snapshot_date=snap_date,
                    latest_snapshot_id=snap_id,
                    disk_size_bytes=disk_size,
                    disk_type_id=disk_type_id,
                )
            )

        # Доп. диски
        for secondary_disk in vm_details.secondary_disks:
            disk_id = secondary_disk.disk_id
            disk_name, disk_size, disk_type_id = get_disk_meta(disk_id)
            snap_date, snap_id = get_latest_snapshot_for_disk(disk_id)
            disks.append(
                DiskSnapshotInfo(
                    disk_id=disk_id,
                    disk_type="SECONDARY",
                    disk_name=disk_name,
                    latest_snapshot_date=snap_date,
                    latest_snapshot_id=snap_id,
                    disk_size_bytes=disk_size,
                    disk_type_id=disk_type_id,
                )
            )

        report["vms"].append(
            {
                "id": vm.id,
                "name": vm.name,
                "zone_id": vm.zone_id,
                "disks": [d.__dict__ for d in disks],
            }
        )

    return report


def _build_attached_disk_spec_from_snapshot(
    *,
    vm_name: str,
    disk: Dict[str, Any],
    name_suffix: str = "restored",
    unique_token: str = "",
    auto_delete: bool = True,
) -> AttachedDiskSpec:
    """
    Собирает AttachedDiskSpec, стараясь восстановить размер/тип диска.

    name_suffix  — суффикс в имени нового диска ('restored' / 'copy').
    unique_token — необязательная добавка для уникальности имени диска
                   (нужна, когда создаётся несколько копий в одном фолдере).
    """
    snapshot_id = disk.get("latest_snapshot_id")
    disk_name = disk.get("disk_name") or disk.get("name") or "disk"
    size_bytes = disk.get("disk_size_bytes")
    type_id = disk.get("disk_type_id") or "network-ssd"

    safe_disk_name = (disk_name or "disk").replace(" ", "-")[:20]
    name_parts = [vm_name, safe_disk_name, name_suffix]
    if unique_token:
        name_parts.append(unique_token)
    # Имена ресурсов в YC ограничены 63 символами.
    new_disk_name = "-".join(p for p in name_parts if p)[:63]

    disk_spec = AttachedDiskSpec(
        disk_spec=AttachedDiskSpec.DiskSpec(
            name=new_disk_name,
            snapshot_id=snapshot_id,
            type_id=type_id,
            size=int(size_bytes) if size_bytes else 20 * 1024**3,
        ),
        auto_delete=auto_delete,
    )
    disk_spec.mode = AttachedDiskSpec.Mode.READ_WRITE
    return disk_spec


def _create_instance_from_entry(
    *,
    report_entry: Dict[str, Any],
    target_zone: str,
    target_subnet_id: str,
    new_instance_name: str,
    disk_name_suffix: str,
    with_nat_ipv4: bool,
    unique_token: str = "",
    copy_security_groups: bool = True,
) -> Optional[str]:
    """
    Общая логика создания новой ВМ из снапшотов дисков исходной ВМ.

    Используется и при переносе (restore_vm_to_zone), и при копировании (copy_vm).
    При отсутствии снапшотов диска они создаются автоматически.
    Возвращает ID операции создания или None при ошибке.
    """
    original_vm_id = report_entry["id"]
    vm_details = instance_service.Get(GetInstanceRequest(instance_id=original_vm_id))

    # Если подсеть не задана — пробуем переиспользовать подсеть оригинала.
    # Подсеть привязана к зоне, поэтому это корректно только в той же зоне.
    subnet_id = target_subnet_id
    if not subnet_id:
        if vm_details.network_interfaces and vm_details.zone_id == target_zone:
            subnet_id = vm_details.network_interfaces[0].subnet_id
            print(f"[ИНФО] SUBNET ID не задан — использую подсеть оригинала: {subnet_id}")
    if not subnet_id:
        print("[ОШИБКА] Не удалось определить SUBNET ID для новой ВМ.")
        return None

    boot_disk_spec: Optional[AttachedDiskSpec] = None
    secondary_disk_specs: List[AttachedDiskSpec] = []

    # ВАЖНО: тут автосоздание снапшотов, если их нет
    for disk in report_entry.get("disks", []):
        if not disk.get("latest_snapshot_id"):
            dname = (
                disk.get("disk_name")
                or disk.get("name")
                or f"disk-{_safe_short(disk.get('disk_id', ''))}"
            )
            did = disk.get("disk_id")
            if not did:
                print(f"[ПРЕДУПРЕЖДЕНИЕ] У диска '{dname}' нет disk_id — пропускаю.")
                continue

            try:
                snap_date, snap_id = ensure_latest_snapshot_for_disk(
                    did, dname, wait=True
                )
                disk["latest_snapshot_id"] = snap_id
                disk["latest_snapshot_date"] = snap_date
            except Exception as e:
                print(f"[ОШИБКА] Не удалось создать снапшот для '{dname}' ({did}): {e}")
                continue

        disk_spec = _build_attached_disk_spec_from_snapshot(
            vm_name=vm_details.name,
            disk=disk,
            name_suffix=disk_name_suffix,
            unique_token=unique_token,
            auto_delete=True,
        )

        if disk.get("disk_type") == "SYSTEM" or disk.get("type") == "SYSTEM":
            boot_disk_spec = disk_spec
        else:
            secondary_disk_specs.append(disk_spec)

    if not boot_disk_spec:
        print("[ОШИБКА] Не найден снапшот системного диска. Создание невозможно.")
        return None

    primary_v4_spec = PrimaryAddressSpec(
        one_to_one_nat_spec=OneToOneNatSpec(ip_version="IPV4")
        if with_nat_ipv4
        else None
    )

    # Копируем группы безопасности оригинала (берём с первого сетевого интерфейса).
    # ВАЖНО: группы безопасности привязаны к сети (VPC), поэтому валидны только
    # если целевая подсеть в той же сети, что и оригинал.
    security_group_ids: List[str] = []
    if copy_security_groups and vm_details.network_interfaces:
        security_group_ids = list(vm_details.network_interfaces[0].security_group_ids)
    if security_group_ids:
        print(
            f"[ИНФО] Копирую группы безопасности: {', '.join(security_group_ids)}"
        )

    create_request = CreateInstanceRequest(
        folder_id=FOLDER_ID,
        name=new_instance_name,
        zone_id=target_zone,
        platform_id=vm_details.platform_id,
        resources_spec=ResourcesSpec(
            memory=vm_details.resources.memory,
            cores=vm_details.resources.cores,
            core_fraction=vm_details.resources.core_fraction,
        ),
        metadata=vm_details.metadata,
        boot_disk_spec=boot_disk_spec,
        secondary_disk_specs=secondary_disk_specs,
        network_interface_specs=[
            NetworkInterfaceSpec(
                subnet_id=subnet_id,
                primary_v4_address_spec=primary_v4_spec,
                security_group_ids=security_group_ids,
            )
        ],
    )

    print(f"[ИНФО] Создаю ВМ '{new_instance_name}' (запрос сформирован).")
    operation = instance_service.Create(create_request)
    print(f"[УСПЕХ] Операция создания запущена. ID операции: {operation.id}")
    return operation.id


def restore_vm_to_zone(
    original_vm_report_entry: Dict[str, Any],
    target_zone: str,
    target_subnet_id: str,
    new_name_suffix: str = DEFAULT_RESTORE_SUFFIX,
    with_nat_ipv4: bool = DEFAULT_NAT_IPV4,
    copy_security_groups: bool = True,
) -> Optional[str]:
    """
    Перенос (миграция) ВМ в указанную зону: новая ВМ создаётся из снапшотов
    дисков оригинала. Оригинал не удаляется.
    """
    vm_name = original_vm_report_entry["name"]
    print(f"\n[ИНФО] Начинаю восстановление ВМ '{vm_name}' в зону '{target_zone}'")

    return _create_instance_from_entry(
        report_entry=original_vm_report_entry,
        target_zone=target_zone,
        target_subnet_id=target_subnet_id,
        new_instance_name=f"{vm_name}{new_name_suffix}",
        disk_name_suffix="restored",
        with_nat_ipv4=with_nat_ipv4,
        copy_security_groups=copy_security_groups,
    )


def copy_vm(
    original_vm_report_entry: Dict[str, Any],
    target_zone: Optional[str] = None,
    target_subnet_id: str = "",
    copies: int = 1,
    new_name_suffix: str = DEFAULT_COPY_SUFFIX,
    with_nat_ipv4: bool = DEFAULT_NAT_IPV4,
    copy_security_groups: bool = True,
) -> List[str]:
    """
    Копирование ВМ: создаёт одну или несколько копий ВМ из снапшотов её дисков.

    В отличие от переноса:
      * по умолчанию копирует в ТУ ЖЕ зону, где находится оригинал
        (target_zone=None), и может переиспользовать подсеть оригинала
        (target_subnet_id="");
      * поддерживает создание нескольких копий за раз (copies);
      * имена ВМ и дисков делаются уникальными, чтобы не было коллизий.

    Возвращает список ID операций создания (по одному на копию).
    """
    vm_name = original_vm_report_entry["name"]
    zone = target_zone or original_vm_report_entry.get("zone_id")
    if not zone:
        print("[ОШИБКА] Не удалось определить целевую зону для копирования.")
        return []

    copies = max(1, copies)
    print(f"\n[ИНФО] Копирование ВМ '{vm_name}' в зону '{zone}' (копий: {copies})")

    operation_ids: List[str] = []
    for i in range(1, copies + 1):
        if copies > 1:
            new_instance_name = f"{vm_name}{new_name_suffix}-{i}"
            unique_token = f"{datetime.now(timezone.utc).strftime('%H%M%S')}-{i}"
        else:
            new_instance_name = f"{vm_name}{new_name_suffix}"
            unique_token = datetime.now(timezone.utc).strftime("%H%M%S")

        print(f"\n[ИНФО] Создаю копию {i}/{copies}: '{new_instance_name}'")
        op_id = _create_instance_from_entry(
            report_entry=original_vm_report_entry,
            target_zone=zone,
            target_subnet_id=target_subnet_id,
            new_instance_name=new_instance_name,
            disk_name_suffix="copy",
            with_nat_ipv4=with_nat_ipv4,
            unique_token=unique_token,
            copy_security_groups=copy_security_groups,
        )
        if op_id:
            operation_ids.append(op_id)
        else:
            print(f"[ОШИБКА] Не удалось запустить копию '{new_instance_name}'.")

    return operation_ids


def _status_for_vm(vm_entry: Dict[str, Any]) -> str:
    disks = vm_entry.get("disks", [])
    if not disks:
        return "НЕТ ДИСКОВ"

    ok = all(d.get("latest_snapshot_date") for d in disks)
    return "ГОТОВО" if ok else "НЕТ СНАПШОТОВ"


if __name__ == "__main__":
    print("=" * 60)
    print("YANDEX CLOUD VM SNAPSHOT (ОТЧЁТ / ПЕРЕНОС / КОПИРОВАНИЕ)")
    print("=" * 60)

    report = generate_vm_report(TARGET_ZONE_FOR_REPORT)
    vms_list = report["vms"]

    if not vms_list:
        print(
            f"\n[ИНФО] В зоне '{TARGET_ZONE_FOR_REPORT}' виртуальные машины не найдены. Выход."
        )
        raise SystemExit(0)

    print(f"\nНайдено ВМ в зоне '{TARGET_ZONE_FOR_REPORT}': {len(vms_list)}")
    print("-" * 60)
    print(f"{'#':<3} | {'Имя ВМ':<25} | {'Статус снапшотов':<20}")
    print("-" * 60)

    for index, vm in enumerate(vms_list, 1):
        status = _status_for_vm(vm)
        print(f"{index:<3} | {vm['name']:<25} | {status:<20}")
    print("-" * 60)

    try:
        selected_index = int(
            input(
                f"\nВведите номер ВМ (1-{len(vms_list)}), либо 0 для выхода: "
            )
        )
        if selected_index == 0:
            print("[ИНФО] Выход по запросу пользователя.")
            raise SystemExit(0)
        if not (1 <= selected_index <= len(vms_list)):
            print(f"[ОШИБКА] Неверный выбор. Введите число от 1 до {len(vms_list)}.")
            raise SystemExit(1)
    except ValueError:
        print("[ОШИБКА] Введите корректное число.")
        raise SystemExit(1)

    selected_vm = vms_list[selected_index - 1]

    print(f"\n[ИНФО] Выбрана ВМ: '{selected_vm['name']}' (ID: {selected_vm['id']})")
    print("Диски и снапшоты:")
    for d in selected_vm.get("disks", []):
        disk_name = d.get("disk_name") or d.get("name")
        disk_type = d.get("disk_type") or d.get("type")
        status = d.get("latest_snapshot_date") or "СНАПШОТА НЕТ"
        print(f"  - {disk_type} диск '{disk_name}': {status}")

    print("\n" + "=" * 60)
    print("ВЫБОР ОПЕРАЦИИ")
    print("=" * 60)
    print("  1 — Перенос (миграция в другую зону)")
    print("  2 — Копирование (создать одну или несколько копий ВМ)")

    operation_choice = input("\nВыберите операцию (1/2): ").strip()
    if operation_choice not in ("1", "2"):
        print("[ОШИБКА] Неверный выбор операции. Выход.")
        raise SystemExit(1)

    if operation_choice == "1":
        # ---------- ПЕРЕНОС ----------
        print("\n" + "=" * 60)
        print("ПАРАМЕТРЫ ПЕРЕНОСА")
        print("=" * 60)

        target_zone = (
            input("Введите целевую зону [по умолчанию: ru-central1-a]: ").strip()
            or "ru-central1-a"
        )
        print(f"[ИНФО] Целевая зона: {target_zone}")

        target_subnet_id = input(
            f"Введите SUBNET ID в зоне '{target_zone}' для новой ВМ: "
        ).strip()
        if not target_subnet_id:
            print("[ОШИБКА] SUBNET ID обязателен. Выход.")
            raise SystemExit(1)
        print(f"[ИНФО] SUBNET ID: {target_subnet_id}")

        print("\n" + "=" * 60)
        print("ПОДТВЕРЖДЕНИЕ")
        print("=" * 60)
        print(f"ВМ для переноса: {selected_vm['name']}")
        print(f"Перенести в зону: {target_zone}")
        print(f"Подсеть: {target_subnet_id}")

        confirm = input("\nПродолжить перенос? (да/нет): ").strip().lower()
        if confirm not in ("да", "д", "yes", "y"):
            print("[ИНФО] Перенос отменён пользователем.")
            raise SystemExit(0)

        print(f"\n[ИНФО] Запускаю перенос ВМ '{selected_vm['name']}'...")
        op_id = restore_vm_to_zone(selected_vm, target_zone, target_subnet_id)

        if op_id:
            print(f"[УСПЕХ] Перенос запущен. ID операции: {op_id}")
        else:
            print("[ОШИБКА] Перенос не запустился (см. сообщения выше).")
    else:
        # ---------- КОПИРОВАНИЕ ----------
        print("\n" + "=" * 60)
        print("ПАРАМЕТРЫ КОПИРОВАНИЯ")
        print("=" * 60)

        source_zone = selected_vm.get("zone_id") or TARGET_ZONE_FOR_REPORT
        target_zone = (
            input(
                f"Введите целевую зону [по умолчанию: {source_zone} — зона оригинала]: "
            ).strip()
            or source_zone
        )
        print(f"[ИНФО] Целевая зона: {target_zone}")

        subnet_prompt = "Введите SUBNET ID для копии"
        if target_zone == source_zone:
            subnet_prompt += " [Enter — использовать подсеть оригинала]"
        target_subnet_id = input(f"{subnet_prompt}: ").strip()
        if not target_subnet_id and target_zone != source_zone:
            print(
                "[ОШИБКА] SUBNET ID обязателен при копировании в другую зону. Выход."
            )
            raise SystemExit(1)

        try:
            copies = int(
                input("Сколько копий создать? [по умолчанию: 1]: ").strip() or "1"
            )
        except ValueError:
            print("[ОШИБКА] Введите корректное число копий.")
            raise SystemExit(1)
        if copies < 1:
            print("[ОШИБКА] Количество копий должно быть не меньше 1.")
            raise SystemExit(1)

        print("\n" + "=" * 60)
        print("ПОДТВЕРЖДЕНИЕ")
        print("=" * 60)
        print(f"ВМ для копирования: {selected_vm['name']}")
        print(f"Зона копий: {target_zone}")
        print(f"Подсеть: {target_subnet_id or '(подсеть оригинала)'}")
        print(f"Количество копий: {copies}")

        confirm = input("\nПродолжить копирование? (да/нет): ").strip().lower()
        if confirm not in ("да", "д", "yes", "y"):
            print("[ИНФО] Копирование отменено пользователем.")
            raise SystemExit(0)

        print(f"\n[ИНФО] Запускаю копирование ВМ '{selected_vm['name']}'...")
        op_ids = copy_vm(
            selected_vm,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            copies=copies,
        )

        if op_ids:
            print(f"\n[УСПЕХ] Запущено копий: {len(op_ids)} из {copies}.")
            for op_id in op_ids:
                print(f"  - ID операции: {op_id}")
        else:
            print("[ОШИБКА] Копирование не запустилось (см. сообщения выше).")
