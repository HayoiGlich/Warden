import os
import sys
import json
import asyncio
import platform
import re
import logging
import subprocess
import tempfile
import traceback
from datetime import datetime
from sqlalchemy import text, and_, or_, select
from sqlalchemy.exc import IntegrityError

os.environ["DISABLE_AD"] = "1"

# Проверка и запрос прав администратора для Windows
if platform.system() == "Windows":
    import ctypes

    def is_admin():
        """Проверяет, запущен ли скрипт от имени администратора"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False

    def elevate():
        """Перезапускает скрипт с правами администратора"""
        if not is_admin():
            print("Требуются права администратора для доступа к файлам журналов...")
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )
            sys.exit(0)

    # Запрашиваем права администратора
    elevate()
else:
    print("Этот скрипт предназначен только для Windows")
    sys.exit(1)

# Импорт модулей базы данных после проверки прав
from database import Database, LoginEvent

# =========================================================
# КОНФИГУРАЦИЯ ИЗ ОКРУЖЕНИЯ (с fallback на прежние значения)
# =========================================================
# Поддержка .env рядом со скриптом — опционально (если установлен python-dotenv).
# Если пакета нет, используются переменные окружения / системы / задачи планировщика.
try:
    from dotenv import load_dotenv

    load_dotenv()  # override=False — заданный выше DISABLE_AD=1 не перезатирается
except Exception:
    pass

# Путь к собранному журналу ForwardedEvents (WEF-коллектор кладёт его сюда).
EVTX_PATH = os.getenv("EVTX_PATH", r"D:\ForwardedEvents\ForwardedEvents.evtx")

# Параметры ЛОКАЛЬНОЙ БД коллектора (каждый коллектор пишет в свой PostgreSQL).
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "unvpa5%w0rd!")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_NAME = os.getenv("DB_NAME", "logs")
DB_PORT = int(os.getenv("DB_PORT", "5432"))

# =========================================================
# НАСТРОЙКА ЛОГГИРОВАНИЯ
# =========================================================


def setup_logging(log_file_path="logs/evtx_importer.log"):
    """Настройка логирования в файл"""
    # Создаем директорию для логов если ее нет
    log_dir = os.path.dirname(log_file_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Настройка формата логов
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Создаем логгер
    logger = logging.getLogger("EVTX_Importer")
    logger.setLevel(logging.DEBUG)

    # Очищаем существующие обработчики
    logger.handlers.clear()

    # Файловый обработчик
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(log_format, date_format)
    file_handler.setFormatter(file_formatter)

    # Консольный обработчик (все сообщения)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", date_format
    )
    console_handler.setFormatter(console_formatter)

    # Добавляем обработчики
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Инициализация логгера
logger = setup_logging("logs/evtx_importer.log")

# =========================================================
# НАСТРОЙКИ
# =========================================================

BATCH_SIZE = 100

# =========================================================
# ФУНКЦИИ ПАРСИНГА
# =========================================================


def parse_iso_time(value: str) -> datetime:
    """Парсинг времени в формате ISO"""
    if not value:
        return datetime.now()

    try:
        # Нормализация строки времени
        value = value.replace("Z", "+00:00")
        if "." in value:
            parts = value.split(".")
            if len(parts) > 1:
                frac = parts[1][:6]
                value = parts[0] + "." + frac + "+00:00"

        try:
            return datetime.fromisoformat(value)
        except:
            return datetime.now()
    except Exception as e:
        logger.debug(f"Ошибка парсинга времени {value}: {e}")
        return datetime.now()


# =========================================================
# ОБРАБОТКА EVTX ФАЙЛОВ - УПРОЩЕННАЯ ВЕРСИЯ
# =========================================================


def read_evtx_with_powershell_simple(evtx_path: str):
    """Простое чтение EVTX файла через PowerShell - без JSON"""
    filename = os.path.basename(evtx_path)
    logger.info(f"Чтение {filename} через PowerShell (простой метод)...")

    events = []

    # Создаем простой PowerShell скрипт в виде строки
    ps_command = """
$ErrorActionPreference = 'Stop'
try {
    $events = Get-WinEvent -Path "%s" -MaxEvents 100 -ErrorAction SilentlyContinue
    
    if (-not $events) {
        Write-Host "ERROR: Файл пуст или недоступен"
        exit 1
    }
    
    $counter = 0
    foreach ($event in $events) {
        $counter++
        
        # Основные поля
        $eventId = $event.Id
        $timeCreated = $event.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
        $computer = $event.MachineName
        $provider = $event.ProviderName
        $recordId = $event.RecordId
        
        # Парсим XML
        $xml = [xml]$event.ToXml()
        $eventData = @{}
        
        if ($xml.Event.EventData -and $xml.Event.EventData.Data) {
            foreach ($data in $xml.Event.EventData.Data) {
                $name = $data.Name
                $value = $data.'#text'
                if ($name -and $value) {
                    $eventData[$name] = $value.ToString().Trim()
                }
            }
        }
        
        # Извлекаем имя пользователя
        $username = "SYSTEM"
        if ($eventData.ContainsKey('TargetUserName')) {
            $targetUser = $eventData['TargetUserName']
            if ($targetUser -and $targetUser -notmatch '^(NTLM|Kerberos|Negotiate|DIGEST)$') {
                $username = $targetUser
                # Если есть домен, удаляем его
                if ($username.Contains('\\')) {
                    $username = $username.Split('\\')[1]
                }
            }
        }
        
        # Формируем строку для вывода
        $output = @{
            EventID = $eventId
            TimeCreated = $timeCreated
            Computer = $computer
            Username = $username
            Provider = $provider
            RecordId = $recordId
            EventDataCount = $eventData.Count
        }
        
        # Добавляем основные поля EventData
        $output.TargetUserName = $eventData['TargetUserName']
        $output.SubjectUserName = $eventData['SubjectUserName']
        $output.IpAddress = $eventData['IpAddress']
        $output.WorkstationName = $eventData['WorkstationName']
        $output.LogonType = $eventData['LogonType']
        $output.TargetDomainName = $eventData['TargetDomainName']
        
        # Конвертируем в JSON
        $json = $output | ConvertTo-Json -Compress
        Write-Output $json
    }
    
    Write-Host "SUCCESS: Обработано $counter событий"
    
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
""" % evtx_path.replace("\\", "\\\\")

    try:
        # Запускаем PowerShell
        logger.info("Запуск PowerShell...")
        process = subprocess.Popen(
            ["powershell", "-Command", ps_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        stdout, stderr = process.communicate(timeout=60)

        if process.returncode != 0:
            logger.error(f"PowerShell ошибка: {stderr[:500]}")
            return []

        # Обрабатываем вывод
        for line in stdout.split("\n"):
            line = line.strip()
            if line and line.startswith("{"):
                try:
                    data = json.loads(line)

                    event = {
                        "event_id": data.get("EventID", 0),
                        "time_created": parse_iso_time(data.get("TimeCreated", "")),
                        "computer": data.get("Computer", "UNKNOWN"),
                        "username": data.get("Username", "SYSTEM"),
                        "xml_content": "",
                        "parsed_data": line[:8000],
                        "message": f"Event {data.get('EventID', 0)}",
                        "groups": [],
                        "evtx_file": filename,
                        "event_record_id": data.get("RecordId", 0),
                        "process_id": 0,
                        "thread_id": 0,
                        "level": 0,
                        "channel": "ForwardedEvents",
                        "provider_name": data.get("Provider", ""),
                        "keywords": "",
                        "task_category": "",
                        "provider_guid": "",
                        "source_name": data.get("Provider", ""),
                        "logon_type": str(data.get("LogonType", ""))[:50],
                        "ip_address": str(data.get("IpAddress", ""))[:50],
                        "workstation_name": str(data.get("WorkstationName", ""))[:255],
                        "target_domain": str(data.get("TargetDomainName", ""))[:255],
                        "subject_user_name": str(data.get("SubjectUserName", ""))[:255],
                        "subject_domain_name": "",
                        "subject_logon_id": "",
                        "target_logon_id": "",
                        "status": "",
                        "sub_status": "",
                        "failure_reason": "",
                        "authentication_package": "",
                    }

                    events.append(event)

                except json.JSONDecodeError as e:
                    logger.debug(f"Ошибка парсинга строки JSON: {e}")

        logger.info(f"Прочитано {len(events)} событий")

    except subprocess.TimeoutExpired:
        logger.error("PowerShell превысил время ожидания")
    except Exception as e:
        logger.error(f"Ошибка PowerShell: {e}")
        logger.debug(traceback.format_exc())

    return events


def read_evtx_with_powershell_xml(evtx_path: str):
    """Чтение EVTX файла через PowerShell с XML парсингом"""
    filename = os.path.basename(evtx_path)
    logger.info(f"Чтение {filename} через PowerShell (XML метод)...")

    events = []

    # PowerShell команда для извлечения XML
    ps_command = """
$events = Get-WinEvent -Path "%s" -MaxEvents 50 -ErrorAction SilentlyContinue
foreach ($event in $events) {
    $xml = $event.ToXml()
    # Заменяем специальные символы
    $xml = $xml -replace '[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F]', ''
    Write-Output "===EVENT_START==="
    Write-Output $xml
    Write-Output "===EVENT_END==="
}
""" % evtx_path.replace("\\", "\\\\")

    try:
        logger.info("Запуск PowerShell для извлечения XML...")
        process = subprocess.Popen(
            ["powershell", "-Command", ps_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        stdout, stderr = process.communicate(timeout=120)

        if stderr:
            logger.error(f"PowerShell stderr: {stderr[:500]}")

        # Парсим XML события
        current_xml = ""
        in_event = False

        for line in stdout.split("\n"):
            line = line.strip()
            if line == "===EVENT_START===":
                in_event = True
                current_xml = ""
            elif line == "===EVENT_END===" and in_event:
                in_event = False
                if current_xml:
                    try:
                        # Парсим XML
                        event_data = parse_xml_simple(current_xml)
                        if event_data:
                            event = create_event_from_data(event_data, filename)
                            events.append(event)
                    except Exception as e:
                        logger.debug(f"Ошибка парсинга XML: {e}")
            elif in_event:
                current_xml += line + "\n"

        logger.info(f"Извлечено {len(events)} событий из XML")

    except Exception as e:
        logger.error(f"Ошибка при чтении XML: {e}")
        logger.debug(traceback.format_exc())

    return events


def parse_xml_simple(xml_content: str) -> dict:
    """Простой парсинг XML"""
    data = {}

    try:
        # Извлекаем EventID
        event_id_match = re.search(
            r"<EventID[^>]*>(\d+)</EventID>", xml_content, re.IGNORECASE
        )
        if event_id_match:
            data["event_id"] = int(event_id_match.group(1))
        else:
            # Альтернативный поиск
            event_id_match = re.search(
                r"<EventID[^>]*>(\d+)<", xml_content, re.IGNORECASE
            )
            if event_id_match:
                data["event_id"] = int(event_id_match.group(1))
            else:
                return {}

        # Извлекаем время
        time_match = re.search(r"SystemTime[^>]*>([^<]+)<", xml_content, re.IGNORECASE)
        if time_match:
            data["time_created"] = time_match.group(1)

        # Извлекаем компьютер
        computer_match = re.search(
            r"<Computer[^>]*>([^<]+)<", xml_content, re.IGNORECASE
        )
        if computer_match:
            data["computer"] = computer_match.group(1)

        # Извлекаем данные из EventData
        event_data = {}

        # Поиск всех Data элементов
        data_matches = re.finditer(
            r'<Data[^>]*Name="([^"]+)"[^>]*>([^<]+)</Data>', xml_content, re.IGNORECASE
        )
        for match in data_matches:
            name, value = match.groups()
            event_data[name] = value

        data["event_data"] = event_data

        # Извлекаем имя пользователя
        username = "SYSTEM"
        if "TargetUserName" in event_data:
            username = event_data["TargetUserName"].strip()
            if username in ["NTLM", "Kerberos", "Negotiate", "DIGEST"]:
                username = "SYSTEM"
            elif "\\" in username:
                parts = username.split("\\")
                if len(parts) > 1:
                    username = parts[1]

        data["username"] = username

    except Exception as e:
        logger.debug(f"Ошибка простого парсинга XML: {e}")
        return {}

    return data


def create_event_from_data(data: dict, filename: str) -> dict:
    """Создает событие из данных"""
    event_data = data.get("event_data", {})

    event = {
        "event_id": data.get("event_id", 0),
        "time_created": parse_iso_time(data.get("time_created", "")),
        "computer": data.get("computer", "UNKNOWN")[:255],
        "username": data.get("username", "SYSTEM")[:255],
        "xml_content": "",
        "parsed_data": json.dumps(data, ensure_ascii=False)[:8000],
        "message": f"Event {data.get('event_id', 0)}",
        "groups": [],
        "evtx_file": filename,
        "event_record_id": 0,
        "process_id": 0,
        "thread_id": 0,
        "level": 0,
        "channel": "ForwardedEvents",
        "provider_name": "",
        "keywords": "",
        "task_category": "",
        "provider_guid": "",
        "source_name": "",
        "logon_type": str(event_data.get("LogonType", ""))[:50],
        "ip_address": str(event_data.get("IpAddress", ""))[:50],
        "workstation_name": str(event_data.get("WorkstationName", ""))[:255],
        "target_domain": str(event_data.get("TargetDomainName", ""))[:255],
        "subject_user_name": str(event_data.get("SubjectUserName", ""))[:255],
        "subject_domain_name": str(event_data.get("SubjectDomainName", ""))[:255],
        "subject_logon_id": str(event_data.get("SubjectLogonId", ""))[:255],
        "target_logon_id": str(event_data.get("TargetLogonId", ""))[:255],
        "status": "",
        "sub_status": "",
        "failure_reason": "",
        "authentication_package": str(event_data.get("AuthenticationPackageName", ""))[
            :255
        ],
    }

    return event


def test_powershell_access(evtx_path: str) -> bool:
    """Проверяет доступность файла через PowerShell"""
    logger.info("Тестирование доступа к файлу через PowerShell...")

    test_command = """
$test = Test-Path "%s"
if ($test) {
    Write-Host "SUCCESS: Файл существует"
    $size = (Get-Item "%s").Length / 1MB
    Write-Host "Размер файла: $size MB"
    exit 0
} else {
    Write-Host "ERROR: Файл не найден"
    exit 1
}
""" % (evtx_path.replace("\\", "\\\\"), evtx_path.replace("\\", "\\\\"))

    try:
        process = subprocess.Popen(
            ["powershell", "-Command", test_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        stdout, stderr = process.communicate(timeout=30)

        if process.returncode == 0:
            logger.info(f"Файл доступен: {stdout.strip()}")
            return True
        else:
            logger.error(f"Файл недоступен: {stderr[:500]}")
            return False

    except Exception as e:
        logger.error(f"Ошибка тестирования: {e}")
        return False


def process_evtx_file(evtx_path: str):
    """Обработка EVTX файла"""
    filename = os.path.basename(evtx_path)

    # Сначала проверяем доступность
    if not test_powershell_access(evtx_path):
        return []

    # Пробуем разные методы
    methods = [
        ("Простой JSON метод", read_evtx_with_powershell_simple),
        ("XML метод", read_evtx_with_powershell_xml),
    ]

    for method_name, method_func in methods:
        try:
            logger.info(f"Пробуем метод: {method_name}")
            events = method_func(evtx_path)
            if events:
                logger.info(f"Метод {method_name} нашел {len(events)} событий")

                # Проверяем, есть ли реальные имена пользователей
                real_users = [e for e in events if e["username"] != "SYSTEM"]
                logger.info(
                    f"Из них {len(real_users)} событий с реальными именами пользователей"
                )

                if real_users:
                    # Показываем примеры
                    logger.info("Примеры найденных пользователей:")
                    for i, event in enumerate(real_users[:5]):
                        logger.info(
                            f"  {i + 1}. {event['username']} (EventID: {event['event_id']})"
                        )

                return events
            else:
                logger.warning(f"Метод {method_name} не нашел событий")

        except Exception as e:
            logger.error(f"Метод {method_name} не сработал: {e}")
            continue

    logger.error(f"Все методы не сработали для файла {filename}")
    return []


# =========================================================
# ФУНКЦИИ ПРОВЕРКИ ДУБЛИКАТОВ
# =========================================================


async def check_duplicate_event(db: Database, event_data: dict) -> bool:
    """Проверяет, существует ли уже такое событие в БД"""
    try:
        async with db.Session() as session:
            # Основные поля для проверки дубликатов
            filters = []

            if (
                event_data.get("event_record_id")
                and event_data.get("event_record_id") > 0
            ):
                filters.append(
                    LoginEvent.event_record_id == event_data.get("event_record_id")
                )
                filters.append(LoginEvent.evtx_file == event_data.get("evtx_file", ""))
                filters.append(LoginEvent.event_id == event_data.get("event_id", 0))
            else:
                filters.append(LoginEvent.event_id == event_data.get("event_id", 0))
                filters.append(
                    LoginEvent.time_created == event_data.get("time_created")
                )
                filters.append(LoginEvent.computer == event_data.get("computer", ""))
                filters.append(
                    LoginEvent.username == event_data.get("username", "SYSTEM")
                )
                filters.append(LoginEvent.evtx_file == event_data.get("evtx_file", ""))

            # Дополнительные условия по другим важным полям
            if event_data.get("logon_type"):
                filters.append(
                    LoginEvent.logon_type == event_data.get("logon_type", "")
                )

            if event_data.get("ip_address"):
                filters.append(
                    LoginEvent.ip_address == event_data.get("ip_address", "")
                )

            # Ищем существующие события с использованием select
            if filters:
                stmt = select(LoginEvent).where(and_(*filters)).limit(1)
                result = await session.execute(stmt)
                existing_event = result.scalars().first()

                return existing_event is not None
            else:
                return False

    except Exception as e:
        logger.error(f"Ошибка при проверке дубликата: {e}")
        logger.debug(traceback.format_exc())
        return False


async def filter_duplicate_events(db: Database, events: list[dict]) -> list[dict]:
    """Фильтрует дубликаты из списка событий"""
    if not events:
        return []

    logger.info(f"Проверка {len(events)} событий на дубликаты...")

    unique_events = []
    duplicate_count = 0

    for i, event in enumerate(events):
        # Проверяем дубликат
        is_duplicate = await check_duplicate_event(db, event)

        if is_duplicate:
            duplicate_count += 1
            if duplicate_count <= 5:  # Логируем только первые 5 дубликатов
                logger.debug(
                    f"Дубликат пропущен: EventID={event.get('event_id')}, "
                    f"Время={event.get('time_created')}, "
                    f"Пользователь={event.get('username')}"
                )
        else:
            unique_events.append(event)

        # Логируем прогресс каждые 50 событий
        if (i + 1) % 50 == 0:
            logger.info(
                f"Проверено {i + 1}/{len(events)} событий, "
                f"найдено дубликатов: {duplicate_count}"
            )

    if duplicate_count > 0:
        logger.info(f"Всего найдено дубликатов: {duplicate_count}")
        logger.info(f"Уникальных событий для сохранения: {len(unique_events)}")

    return unique_events


# =========================================================
# СОХРАНЕНИЕ В БД
# =========================================================


async def save_events_batch(db: Database, events_batch: list[dict]):
    """Сохранение пачки событий в БД"""
    if not events_batch:
        return 0, 0

    saved_count = 0
    error_count = 0

    try:
        async with db.Session() as session:
            for e in events_batch:
                try:
                    # Проверка обязательных полей
                    if not e.get("event_id"):
                        error_count += 1
                        continue

                    # Преобразуем время
                    time_created = e.get("time_created")
                    if not isinstance(time_created, datetime):
                        time_created = datetime.now()

                    # Обработка event_record_id - ограничиваем для PostgreSQL integer
                    event_record_id = e.get("event_record_id", 0)
                    if (
                        event_record_id > 2147483647
                    ):  # Максимальное значение для PostgreSQL integer
                        logger.warning(
                            f"Record ID {event_record_id} превышает лимит int32, преобразуем"
                        )
                        # Используем остаток от деления или хэш
                        event_record_id = abs(hash(str(event_record_id))) % 1000000

                    # Создаем объект
                    login_event = LoginEvent(
                        event_id=e.get("event_id", 0),
                        time_created=time_created,
                        computer=e.get("computer", ""),
                        username=e.get("username", "SYSTEM"),
                        logon_type=e.get("logon_type", ""),
                        ip_address=e.get("ip_address", ""),
                        workstation_name=e.get("workstation_name", ""),
                        target_domain=e.get("target_domain", ""),
                        groups=e.get("groups", []),
                        source_name=e.get("source_name", ""),
                        task_category=e.get("task_category", ""),
                        level=e.get("level", 0),
                        keywords=e.get("keywords", ""),
                        event_record_id=event_record_id,  # Используем обработанное значение
                        process_id=e.get("process_id", 0),
                        thread_id=e.get("thread_id", 0),
                        channel=e.get("channel", ""),
                        provider_name=e.get("provider_name", ""),
                        provider_guid=e.get("provider_guid", ""),
                        target_logon_id=e.get("target_logon_id", ""),
                        subject_user_name=e.get("subject_user_name", ""),
                        subject_domain_name=e.get("subject_domain_name", ""),
                        subject_logon_id=e.get("subject_logon_id", ""),
                        status=e.get("status", ""),
                        sub_status=e.get("sub_status", ""),
                        failure_reason=e.get("failure_reason", ""),
                        authentication_package=e.get("authentication_package", ""),
                        xml_content=e.get("xml_content", ""),
                        parsed_data=e.get("parsed_data", ""),
                        message=e.get("message", ""),
                        evtx_file=e.get("evtx_file", ""),
                        inserted_at=datetime.utcnow(),
                    )

                    session.add(login_event)
                    saved_count += 1

                except IntegrityError:
                    # Дубликат на уровне базы данных (если есть уникальные ограничения)
                    error_count += 1
                    logger.debug(
                        f"Дубликат события (IntegrityError): EventID={e.get('event_id')}"
                    )
                    continue
                except Exception as ex:
                    error_count += 1
                    logger.debug(f"Ошибка сохранения события: {ex}")
                    logger.debug(traceback.format_exc())
                    continue

            await session.commit()

    except Exception as ex:
        logger.error(f"Ошибка при сохранении пачки: {ex}")
        logger.debug(traceback.format_exc())
        return saved_count, error_count

    return saved_count, error_count


async def save_all_events_to_db(db: Database, all_events: list[dict]):
    """Сохранение всех событий в БД"""
    if not all_events:
        logger.warning("Нет событий для сохранения")
        return 0, 0

    total_events = len(all_events)
    logger.info(f"Всего событий для обработки: {total_events}")

    # Фильтруем дубликаты
    unique_events = await filter_duplicate_events(db, all_events)

    if not unique_events:
        logger.info("Все события уже существуют в базе данных")
        return 0, 0

    logger.info(f"Сохранение {len(unique_events)} уникальных событий в БД...")

    total_saved = 0
    total_errors = 0

    # Разбиваем на батчи
    for i in range(0, len(unique_events), BATCH_SIZE):
        batch = unique_events[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(unique_events) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"Батч {batch_num}/{total_batches} ({len(batch)} событий)...")

        saved, errors = await save_events_batch(db, batch)
        total_saved += saved
        total_errors += errors

        logger.info(f"Батч {batch_num}: сохранено {saved}, ошибок {errors}")

    logger.info(f"СОХРАНЕНИЕ ЗАВЕРШЕНО: сохранено {total_saved}, ошибок {total_errors}")
    return total_saved, total_errors


# =========================================================
# ОСНОВНАЯ ФУНКЦИЯ
# =========================================================


async def main():
    """Основная функция"""
    logger.info("=" * 80)
    logger.info("ИМПОРТЕР FORWARDEDEVENTS В БАЗУ ДАННЫХ")
    logger.info("=" * 80)
    logger.info(f"Дата запуска: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

    # Проверяем файл
    evtx_path = EVTX_PATH

    if not os.path.exists(evtx_path):
        logger.error(f"Файл не найден: {evtx_path}")
        return

    filename = os.path.basename(evtx_path)
    file_size = os.path.getsize(evtx_path) / (1024 * 1024)
    logger.info(f"Файл: {filename}, Размер: {file_size:.2f} MB")

    # Подключение к БД
    logger.info("=" * 80)
    logger.info("ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ")

    db = Database()
    try:
        await db.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            database=DB_NAME,
            port=DB_PORT,
        )
        logger.info(
            "Подключение к БД успешно (%s@%s:%s/%s)",
            DB_USER,
            DB_HOST,
            DB_PORT,
            DB_NAME,
        )

        # Проверяем текущее количество записей
        async with db.Session() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM logins"))
            total_before = result.scalar()
            logger.info(f"Текущее количество записей в БД: {total_before}")

            # Проверяем количество событий из этого файла
            query = text("""
                SELECT COUNT(*) 
                FROM logins 
                WHERE evtx_file = :filename
            """)
            result = await session.execute(query, {"filename": filename})
            existing_file_events = result.scalar()
            logger.info(f"Событий из файла {filename} уже в БД: {existing_file_events}")

    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        logger.debug(traceback.format_exc())
        return

    # Обработка файла
    logger.info("=" * 80)
    logger.info(f"ОБРАБОТКА: {filename}")

    # Используем PowerShell для чтения событий
    all_events = process_evtx_file(evtx_path)

    if all_events:
        logger.info(f"НАЙДЕНО СОБЫТИЙ: {len(all_events)}")

        # Анализируем события
        event_stats = {}
        user_stats = {}

        for event in all_events:
            event_id = event.get("event_id", 0)
            username = event.get("username", "SYSTEM")

            # Статистика по EventID
            event_stats[event_id] = event_stats.get(event_id, 0) + 1

            # Статистика по пользователям
            if username != "SYSTEM":
                user_stats[username] = user_stats.get(username, 0) + 1

        logger.info("СТАТИСТИКА ПО ТИПАМ СОБЫТИЙ:")
        for event_id, count in sorted(
            event_stats.items(), key=lambda x: x[1], reverse=True
        )[:10]:
            logger.info(f"  EventID {event_id}: {count} событий")

        if user_stats:
            logger.info("СТАТИСТИКА ПО ПОЛЬЗОВАТЕЛЯМ:")
            for username, count in sorted(
                user_stats.items(), key=lambda x: x[1], reverse=True
            )[:10]:
                logger.info(f"  {username}: {count} событий")
        else:
            logger.warning("Не найдено событий с реальными именами пользователей!")

        # Показываем примеры событий
        logger.info("ПРИМЕРЫ СОБЫТИЙ (первые 5):")
        for i, event in enumerate(all_events[:5]):
            logger.info(
                f"  {i + 1}. EventID: {event['event_id']}, "
                f"Пользователь: {event['username']}, "
                f"Время: {event['time_created']}"
            )

        # Сохранение в БД
        saved, errors = await save_all_events_to_db(db, all_events)
        logger.info(f"Сохранено: {saved}, ошибок: {errors}")

    else:
        logger.warning(f"Не удалось извлечь события из файла {filename}")

        # Пробуем ручную команду PowerShell
        logger.info("Пробуем ручную команду PowerShell...")
        try:
            test_cmd = f'Get-WinEvent -Path "{evtx_path}" -MaxEvents 1 | Select-Object Id, TimeCreated, MachineName'
            process = subprocess.Popen(
                ["powershell", "-Command", test_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            stdout, stderr = process.communicate(timeout=30)

            if stdout:
                logger.info(f"Тестовая команда вернула: {stdout[:200]}")
            if stderr:
                logger.error(f"Ошибка тестовой команды: {stderr[:500]}")

        except Exception as e:
            logger.error(f"Ошибка тестовой команды: {e}")

    # Итоговая статистика
    logger.info("=" * 80)
    logger.info("ИТОГОВАЯ СТАТИСТИКА")

    try:
        async with db.Session() as session:
            # Общая статистика
            result = await session.execute(text("SELECT COUNT(*) FROM logins"))
            total_after = result.scalar()
            logger.info(f"ВСЕГО ЗАПИСЕЙ В БАЗЕ: {total_after}")
            logger.info(
                f"Добавлено записей в этом запуске: {total_after - total_before}"
            )

            # Статистика по ForwardedEvents
            query = text("""
                SELECT COUNT(*) 
                FROM logins 
                WHERE evtx_file = 'ForwardedEvents.evtx'
            """)
            result = await session.execute(query)
            forwarded_count = result.scalar()
            logger.info(f"Всего событий из ForwardedEvents: {forwarded_count}")

            if forwarded_count > 0:
                # Последние 5 событий
                query = text("""
                    SELECT event_id, username, time_created, computer
                    FROM logins 
                    WHERE evtx_file = 'ForwardedEvents.evtx'
                    ORDER BY id DESC
                    LIMIT 5
                """)
                result = await session.execute(query)
                recent_events = result.fetchall()

                if recent_events:
                    logger.info("ПОСЛЕДНИЕ 5 СОБЫТИЙ:")
                    for i, event in enumerate(recent_events):
                        logger.info(
                            f"  {i + 1}. EventID: {event[0]}, "
                            f"Пользователь: {event[1]}, "
                            f"Время: {event[2]}, "
                            f"Компьютер: {event[3]}"
                        )

            # Статистика по дубликатам
            query = text("""
                SELECT COUNT(DISTINCT event_record_id) as unique_by_record,
                       COUNT(*) as total,
                       COUNT(*) - COUNT(DISTINCT event_record_id) as duplicates
                FROM logins 
                WHERE evtx_file = 'ForwardedEvents.evtx'
                AND event_record_id > 0
            """)
            result = await session.execute(query)
            dup_stats = result.fetchone()

            if dup_stats:
                logger.info(f"Уникальных событий по RecordID: {dup_stats[0]}")
                logger.info(f"Всего событий: {dup_stats[1]}")
                if dup_stats[2] > 0:
                    logger.warning(f"Найдено дубликатов по RecordID: {dup_stats[2]}")

    except Exception as e:
        logger.error(f"Ошибка статистики: {e}")
        logger.debug(traceback.format_exc())

    logger.info("=" * 80)
    logger.info("ЗАВЕРШЕНО!")
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
