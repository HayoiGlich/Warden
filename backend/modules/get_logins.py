import json
from datetime import datetime
import re
import traceback
import xml.etree.ElementTree as ET
import sys
import os
import socket
import platform

try:
    import win32evtlog
    import win32evtlogutil
    WINDOWS_EVTLOG_AVAILABLE = True
except ImportError:
    WINDOWS_EVTLOG_AVAILABLE = False

try:
    from Evtx.Evtx import Evtx
    EVTX_AVAILABLE = True
except ImportError:
    EVTX_AVAILABLE = False

def get_current_system_info():
    """Получение информации о текущей системе"""
    try:
        hostname = platform.node() or socket.gethostname()
        
        # Получаем IP адрес
        ip_address = "Не определен"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_address = s.getsockname()[0]
            s.close()
        except:
            try:
                hostname_ip = socket.gethostbyname(hostname)
                if hostname_ip and hostname_ip != "127.0.0.1":
                    ip_address = hostname_ip
            except:
                pass
        
        return {
            "hostname": hostname,
            "ip_address": ip_address
        }
    except Exception as e:
        return {
            "hostname": "Не определен",
            "ip_address": "Не определен"
        }

def safe_xml_parse(xml_content):
    """Безопасный парсинг XML с обработкой ошибок"""
    try:
        # Убираем namespace для упрощения парсинга
        xml_content = re.sub(r'xmlns="[^"]+"', '', xml_content, count=1)
        root = ET.fromstring(xml_content)
        return root
    except ET.ParseError as e:
        # Пробуем исправить XML
        try:
            # Убираем все namespaces
            xml_content = re.sub(r'xmlns(:[^=]+)?="[^"]+"', '', xml_content)
            # Убираем недопустимые символы
            xml_content = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', xml_content)
            root = ET.fromstring(xml_content)
            return root
        except:
            return None
    except Exception:
        return None

def parse_event_xml(xml_content):
    """Парсинг XML события и извлечение нужных полей"""
    try:
        root = safe_xml_parse(xml_content)
        if root is None:
            return {}
        
        event_data = {}
        
        # EventID
        event_id_elem = root.find('.//EventID')
        if event_id_elem is not None and event_id_elem.text:
            try:
                event_data['Id'] = int(event_id_elem.text)
            except:
                event_data['Id'] = 0
        
        # Время
        time_elem = root.find('.//TimeCreated')
        if time_elem is not None:
            system_time = time_elem.get('SystemTime', '')
            event_data['TimeCreated'] = system_time
        
        # Компьютер
        computer_elem = root.find('.//Computer')
        if computer_elem is not None and computer_elem.text:
            event_data['Computer'] = computer_elem.text.strip()
        
        # Данные события
        data_elements = root.findall('.//EventData/Data')
        for data in data_elements:
            name = data.get('Name')
            if name and data.text:
                event_data[name] = data.text.strip()
            elif data.text:
                # Если нет атрибута Name, используем значение как есть
                idx = len([k for k in event_data.keys() if k.startswith('Data_')])
                event_data[f'Data_{idx}'] = data.text.strip()
        
        return event_data
        
    except Exception as e:
        return {'Id': 0, 'TimeCreated': '', 'Computer': '', 'Message': f'Ошибка парсинга XML: {str(e)}'}

def extract_groups_from_event(event_data):
    """Извлечение информации о группах из данных события"""
    groups = []
    
    # Пытаемся извлечь группы из различных полей
    group_fields = ['TargetGroupSid', 'PrimaryGroupId', 'MemberName', 
                   'MemberSid', 'Group', 'GroupName', 'GroupSid']
    
    for field in group_fields:
        if field in event_data and event_data[field]:
            if field == 'PrimaryGroupId':
                # Преобразуем ID основной группы в название
                group_id = event_data[field]
                group_names = {
                    '512': 'Domain Admins',
                    '513': 'Domain Users',
                    '514': 'Domain Guests',
                    '515': 'Domain Computers',
                    '516': 'Domain Controllers',
                    '520': 'Group Policy Creator Owners',
                    '544': 'Administrators',
                    '545': 'Users',
                    '546': 'Guests',
                    '547': 'Power Users',
                    '548': 'Account Operators',
                    '549': 'Server Operators',
                    '550': 'Print Operators',
                    '551': 'Backup Operators',
                    '552': 'Replicators'
                }
                group_name = group_names.get(group_id, f"Группа ID: {group_id}")
                groups.append(f"Основная группа: {group_name}")
            else:
                groups.append(f"{field}: {event_data[field]}")
    
    # Если есть SID пользователя, можем попытаться определить доменную группу
    if 'TargetUserSid' in event_data and event_data['TargetUserSid']:
        sid = event_data['TargetUserSid']
        if sid.startswith('S-1-5-21-'):
            groups.append("Доменный пользователь")
        elif sid.startswith('S-1-5-18'):
            groups.append("Системная учетная запись (LOCAL SYSTEM)")
        elif sid.startswith('S-1-5-19'):
            groups.append("Служба Local Service")
        elif sid.startswith('S-1-5-20'):
            groups.append("Служба Network Service")
    
    # Определяем тип входа
    if 'LogonType' in event_data and event_data['LogonType']:
        logon_type = event_data['LogonType']
        logon_types = {
            '0': 'Система',
            '2': 'Интерактивный (локальный вход)',
            '3': 'Сеть',
            '4': 'Пакетный',
            '5': 'Служба',
            '7': 'Разблокировка',
            '8': 'Сеть (Cleartext)',
            '9': 'Новые учетные данные',
            '10': 'Удаленный интерактивный (RDP)',
            '11': 'Интерактивный (кешированные учетные данные)'
        }
        logon_desc = logon_types.get(logon_type, f"Тип {logon_type}")
        groups.append(f"Тип входа: {logon_desc}")
    
    return groups

def get_user_groups_from_ad(username):
    """Получение групп пользователя из Active Directory"""
    if not username or username == '':
        return []
    
    # В реальной системе здесь должен быть код для подключения к AD
    # В данной версии возвращаем пустой список
    return []

def parse_iso_time(time_str):
    """Парсинг времени в ISO формате"""
    try:
        # Убираем Z и миллисекунды если есть
        time_str = time_str.replace('Z', '+00:00')
        if '.' in time_str:
            time_str = time_str.split('.')[0] + '+00:00'
        return datetime.fromisoformat(time_str)
    except:
        try:
            # Пробуем другой формат
            return datetime.strptime(time_str[:19], "%Y-%m-%dT%H:%M:%S")
        except:
            return datetime.now()

def sort_events_by_time(events, reverse=True):
    """Сортировка событий по времени"""
    def get_event_time(event):
        time_str = event.get('TimeCreated', '')
        try:
            # Пытаемся распарсить в ISO формате
            if 'T' in time_str:
                return parse_iso_time(time_str)
            else:
                # Пытаемся распарсить в формате DD.MM.YYYY HH:MM:SS
                return datetime.strptime(time_str, "%d.%m.%Y %H:%M:%S")
        except:
            return datetime.min
    
    return sorted(events, key=get_event_time, reverse=reverse)

def get_logins_evtx(evtx_path, username=None, computer=None):
    """Основная функция получения событий входа через EVTX файл"""
    print(f"Начало обработки журнала: {evtx_path}")
    print(f"Пользователь: {username or 'Все'}")
    print(f"Компьютер: {computer or 'Все'}")
    
    try:
        events = []
        
        # Пробуем использовать Evtx библиотеку
        if EVTX_AVAILABLE:
            print("Попытка использования метода: python-evtx")
            evtx_events = get_logins_with_evtx(evtx_path, username, computer)
            events.extend(evtx_events)
            print(f"Найдено событий (python-evtx): {len(evtx_events)}")
        else:
            print("Библиотека python-evtx не доступна")
        
        # Если Evtx не сработал или не доступен, пробуем win32evtlog
        if WINDOWS_EVTLOG_AVAILABLE and len(events) == 0:
            print("Попытка использования метода: win32evtlog")
            win32_events = get_logins_with_win32evtlog(evtx_path, username, computer)
            events.extend(win32_events)
            print(f"Найдено событий (win32evtlog): {len(win32_events)}")
        elif not WINDOWS_EVTLOG_AVAILABLE:
            print("Библиотека win32evtlog не доступна")
        
        # Если оба метода не сработали, возвращаем информационное сообщение
        if not events:
            print(f"Событий не найдено в файле: {evtx_path}")
            system_info = get_current_system_info()
            
            # Проверяем, какие EventID есть в файле
            all_events = check_available_event_ids(evtx_path)
            if all_events:
                event_ids = list(all_events.keys())
                print(f"Доступные EventID в файле: {event_ids}")
                
                events = [{
                    "Id": 0, 
                    "TimeCreated": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    "Computer": system_info["hostname"],
                    "Username": "Информация",
                    "Groups": ["Нет событий входа"],
                    "IpAddress": system_info["ip_address"],
                    "Message": f"В файле журнала {evtx_path} найдены следующие EventID: {', '.join(map(str, event_ids[:20]))}. "
                              f"События входа (4624, 4625) отсутствуют или не могут быть прочитаны."
                }]
            else:
                events = [{
                    "Id": 0, 
                    "TimeCreated": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    "Computer": system_info["hostname"],
                    "Username": "Информация",
                    "Groups": ["Файл недоступен"],
                    "IpAddress": system_info["ip_address"],
                    "Message": f"Не удалось прочитать файл журнала: {evtx_path}. "
                              f"Проверьте права доступа и существование файла."
                }]
        
        # Сортируем события по времени (от новых к старым)
        events = sort_events_by_time(events, reverse=True)
        print(f"Всего событий после сортировки: {len(events)}")
        
        return json.dumps(events, ensure_ascii=False, indent=2)
        
    except Exception as e:
        error_msg = f"Ошибка в get_logins_evtx: {str(e)}\n{traceback.format_exc()}"
        print(f"Ошибка: {error_msg}")
        
        system_info = get_current_system_info()
        events = [{
            "Id": 0, 
            "TimeCreated": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            "Computer": system_info["hostname"],
            "Username": "Ошибка",
            "Groups": ["Ошибка обработки"],
            "IpAddress": system_info["ip_address"],
            "Message": error_msg[:500]
        }]
        return json.dumps(events, ensure_ascii=False, indent=2)

def check_available_event_ids(evtx_path):
    """Проверка доступных EventID в файле"""
    eventid_stats = {}
    
    if EVTX_AVAILABLE:
        try:
            with Evtx(evtx_path) as log:
                record_count = 0
                for record in log.records():
                    try:
                        xml_content = record.xml()
                        root = safe_xml_parse(xml_content)
                        if root is not None:
                            event_id_elem = root.find('.//EventID')
                            if event_id_elem is not None and event_id_elem.text:
                                try:
                                    event_id = int(event_id_elem.text)
                                    eventid_stats[event_id] = eventid_stats.get(event_id, 0) + 1
                                except:
                                    pass
                        record_count += 1
                        if record_count > 1000:  # Ограничиваем для скорости
                            break
                    except:
                        continue
        except:
            pass
    
    return eventid_stats

def get_logins_with_evtx(evtx_path, username=None, computer=None):
    """Получение событий через библиотеку Evtx"""
    events = []
    error_count = 0
    processed_count = 0
    max_errors = 50
    max_events = 50000
    
    try:
        if not os.path.exists(evtx_path):
            print(f"Файл не существует: {evtx_path}")
            return events
        
        file_size = os.path.getsize(evtx_path)
        print(f"Открытие EVTX файла: {evtx_path} (размер: {file_size / (1024*1024):.2f} MB)")
        
        with Evtx(evtx_path) as log:
            print("Чтение записей...")
            
            for record in log.records():
                try:
                    processed_count += 1
                    
                    # Получаем XML записи
                    xml_content = record.xml()
                    
                    # Проверяем EventID (только события входа)
                    if "<EventID>4624</EventID>" not in xml_content and "<EventID>4625</EventID>" not in xml_content:
                        continue
                    
                    # Парсим XML для извлечения данных
                    event_data = parse_event_xml(xml_content)
                    if not event_data:
                        continue
                    
                    # Получаем EventID
                    event_id = event_data.get('Id', 0)
                    if event_id not in [4624, 4625]:
                        continue
                    
                    # Получаем имя пользователя
                    target_user = event_data.get('TargetUserName', '')
                    
                    # Фильтрация по пользователю
                    if username and target_user:
                        username_lower = username.lower()
                        target_user_lower = target_user.lower()
                        
                        if username_lower not in target_user_lower:
                            continue
                    
                    # Получаем имя компьютера
                    comp_name = event_data.get('Computer', '')
                    
                    # Фильтрация по компьютеру
                    if computer and comp_name:
                        computer_lower = computer.lower()
                        comp_name_lower = comp_name.lower()
                        
                        if computer_lower not in comp_name_lower:
                            continue
                    
                    # Получаем время события
                    time_str = event_data.get('TimeCreated', '')
                    try:
                        if time_str:
                            event_time = parse_iso_time(time_str)
                        else:
                            event_time = datetime.now()
                    except:
                        event_time = datetime.now()
                    
                    # Извлекаем группы из данных события
                    groups_from_event = extract_groups_from_event(event_data)
                    
                    # Получаем группы из AD
                    ad_groups = []
                    if target_user:
                        ad_groups = get_user_groups_from_ad(target_user)
                    
                    # Объединяем группы
                    all_groups = list(set(groups_from_event + ad_groups))
                    
                    # Форматируем для вывода
                    formatted_event = {
                        "Id": event_id,
                        "TimeCreated": event_time.strftime("%d.%m.%Y %H:%M:%S"),
                        "Computer": comp_name,
                        "Username": target_user,
                        "LogonType": event_data.get('LogonType', ''),
                        "IpAddress": event_data.get('IpAddress', ''),
                        "WorkstationName": event_data.get('WorkstationName', ''),
                        "TargetDomainName": event_data.get('TargetDomainName', ''),
                        "ProcessName": event_data.get('ProcessName', ''),
                        "Groups": all_groups,
                        "Message": format_event_message(event_data)
                    }
                    
                    events.append(formatted_event)
                    
                    # Ограничение по количеству событий
                    if len(events) >= max_events:
                        print(f"Достигнут лимит в {max_events} событий")
                        break
                    
                except AttributeError as e:
                    # Обработка специфической ошибки с NullTypeNode
                    error_count += 1
                    if error_count > max_errors:
                        print(f"Слишком много ошибок ({error_count}). Прерываем обработку...")
                        break
                    continue
                except Exception as e:
                    error_count += 1
                    if error_count > max_errors:
                        print(f"Слишком много ошибок ({error_count}). Прерываем обработку...")
                        break
                    continue
            
            print(f"Обработано записей: {processed_count}, найдено событий входа: {len(events)}, ошибок: {error_count}")
            
    except Exception as e:
        print(f"Ошибка при открытии EVTX файла: {str(e)}")
        print(f"Трассировка: {traceback.format_exc()}")
    
    return events

def get_logins_with_win32evtlog(evtx_path, username=None, computer=None):
    """Получение событий через Windows Event Log API"""
    events = []
    
    if not WINDOWS_EVTLOG_AVAILABLE:
        return events
    
    try:
        print(f"Открытие журнала через win32evtlog: {evtx_path}")
        handle = win32evtlog.OpenBackupEventLog(None, evtx_path)
        if not handle:
            print("Не удалось открыть журнал через win32evtlog")
            return events
        
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        
        event_count = 0
        max_events = 50000
        
        print("Чтение событий...")
        while event_count < max_events:
            try:
                raw_events = win32evtlog.ReadEventLog(handle, flags, 0)
                if not raw_events:
                    print("Больше нет событий для чтения")
                    break
                
                for ev in raw_events:
                    event_count += 1
                    
                    # Фильтруем по EventID
                    if ev.EventID not in (4624, 4625):
                        continue
                    
                    # Получаем данные события
                    msg_parts = []
                    if ev.StringInserts:
                        msg_parts = list(ev.StringInserts)
                    
                    # Извлекаем имя пользователя
                    target_user = ""
                    if len(msg_parts) > 5:
                        target_user = msg_parts[5] if msg_parts[5] else ""
                    
                    # Фильтрация по пользователю
                    if username and target_user:
                        username_lower = username.lower()
                        target_user_lower = target_user.lower()
                        
                        if username_lower not in target_user_lower:
                            continue
                    
                    # Извлекаем имя компьютера
                    comp_name = ev.ComputerName if hasattr(ev, 'ComputerName') else ""
                    
                    # Фильтрация по компьютеру
                    if computer and comp_name:
                        computer_lower = computer.lower()
                        comp_name_lower = comp_name.lower()
                        
                        if computer_lower not in comp_name_lower:
                            continue
                    
                    # Получаем группы
                    groups = []
                    if target_user:
                        groups = get_user_groups_from_ad(target_user)
                    
                    # Форматируем сообщение
                    message = format_win32_event_message(ev, msg_parts)
                    
                    event = {
                        "Id": ev.EventID,
                        "TimeCreated": ev.TimeGenerated.strftime("%d.%m.%Y %H:%M:%S"),
                        "Computer": comp_name,
                        "Username": target_user,
                        "LogonType": extract_logon_type(msg_parts),
                        "IpAddress": extract_ip_address(msg_parts),
                        "Groups": groups,
                        "Message": message
                    }
                    
                    events.append(event)
                    
                    if len(events) >= max_events:
                        print(f"Достигнут лимит в {max_events} событий")
                        break
                    
            except Exception as e:
                print(f"Ошибка при чтении события win32evtlog: {str(e)}")
                break
        
        win32evtlog.CloseEventLog(handle)
        print(f"Обработано событий win32evtlog: {event_count}, найдено событий входа: {len(events)}")
        
    except Exception as e:
        print(f"Ошибка win32evtlog: {str(e)}")
    
    return events

def format_event_message(event_data):
    """Форматирование сообщения события из распарсенных данных"""
    lines = []
    
    # Основные поля
    if event_data.get('Id'):
        lines.append(f"EventID: {event_data['Id']}")
    
    if event_data.get('TargetUserName'):
        lines.append(f"Пользователь: {event_data['TargetUserName']}")
    
    if event_data.get('TargetDomainName'):
        lines.append(f"Домен: {event_data['TargetDomainName']}")
    
    if event_data.get('LogonType'):
        lines.append(f"Тип входа: {event_data['LogonType']}")
        logon_types = {
            '0': 'Система',
            '2': 'Интерактивный (локальный вход)',
            '3': 'Сеть',
            '4': 'Пакетный',
            '5': 'Служба',
            '7': 'Разблокировка',
            '8': 'Сеть (Cleartext)',
            '9': 'Новые учетные данные',
            '10': 'Удаленный интерактивный (RDP)',
            '11': 'Интерактивный (кешированные учетные данные)'
        }
        logon_desc = logon_types.get(event_data['LogonType'], f"Неизвестный тип {event_data['LogonType']}")
        lines.append(f"Тип входа (расшифровка): {logon_desc}")
    
    if event_data.get('IpAddress'):
        lines.append(f"IP адрес: {event_data['IpAddress']}")
    
    if event_data.get('WorkstationName'):
        lines.append(f"Рабочая станция: {event_data['WorkstationName']}")
    
    if event_data.get('ProcessName'):
        lines.append(f"Процесс: {event_data['ProcessName']}")
    
    if event_data.get('TargetUserSid'):
        lines.append(f"SID пользователя: {event_data['TargetUserSid']}")
    
    if event_data.get('TargetLogonId'):
        lines.append(f"ID сессии: {event_data['TargetLogonId']}")
    
    # Дополнительные поля
    for key, value in event_data.items():
        if key not in ['Id', 'TimeCreated', 'Computer', 'TargetUserName', 
                      'TargetDomainName', 'LogonType', 'IpAddress', 
                      'WorkstationName', 'ProcessName', 'TargetUserSid',
                      'TargetLogonId'] and key.startswith('Data_'):
            if value and isinstance(value, str):
                lines.append(f"{key.replace('Data_', 'Данные ')}: {value}")
    
    return "\n".join(lines) if lines else "Нет дополнительных данных"

def format_win32_event_message(event, msg_parts):
    """Форматирование сообщения из win32evtlog события"""
    lines = []
    
    # EventID
    lines.append(f"EventID: {event.EventID}")
    
    # Информация из StringInserts
    field_names = [
        "SubjectUserSid", "SubjectUserName", "SubjectDomainName", "SubjectLogonId",
        "TargetUserSid", "TargetUserName", "TargetDomainName", "TargetLogonId",
        "LogonType", "LogonProcessName", "AuthenticationPackageName",
        "WorkstationName", "LogonGuid", "TransmittedServices", "LmPackageName",
        "KeyLength", "ProcessId", "ProcessName", "IpAddress", "IpPort"
    ]
    
    for i, part in enumerate(msg_parts):
        if part and i < len(field_names):
            lines.append(f"{field_names[i]}: {part}")
        elif part:
            lines.append(f"Данные[{i}]: {part}")
    
    # SourceName и др.
    if hasattr(event, 'SourceName') and event.SourceName:
        lines.append(f"Источник: {event.SourceName}")
    
    return "\n".join(lines) if lines else "Нет данных"

def extract_logon_type(msg_parts):
    """Извлечение типа входа из StringInserts"""
    # LogonType обычно в Data[8] для 4624
    if len(msg_parts) > 8 and msg_parts[8]:
        return msg_parts[8]
    return ""

def extract_ip_address(msg_parts):
    """Извлечение IP адреса из StringInserts"""
    # IpAddress обычно в Data[18] для 4624
    if len(msg_parts) > 18 and msg_parts[18]:
        return msg_parts[18]
    return ""

def main():
    """Тестирование функции"""
    import sys
    
    if len(sys.argv) > 1:
        evtx_path = sys.argv[1]
        username = sys.argv[2] if len(sys.argv) > 2 else None
        computer = sys.argv[3] if len(sys.argv) > 3 else None
    else:
        # Попробуем разные пути
        possible_paths = [
            r"C:\Windows\System32\winevt\Logs\Security.evtx",
            r"C:\Windows\System32\winevt\Logs\System.evtx",
            r"C:\Windows\System32\winevt\Logs\ForwardedEvents.evtx",
            r"C:\Windows\System32\winevt\Logs\Application.evtx",
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                evtx_path = path
                print(f"Используем файл: {evtx_path}")
                break
        else:
            print("Не найден ни один файл журнала")
            return
        
        username = None
        computer = None
    
    system_info = get_current_system_info()
    print(f"Текущая система:")
    print(f"  Имя ПК: {system_info['hostname']}")
    print(f"  IP адрес: {system_info['ip_address']}")
    print(f"Поиск событий входа...")
    print(f"Файл: {evtx_path}")
    print(f"Пользователь: {username or 'Все'}")
    print(f"Компьютер: {computer or 'Все'}")
    print("-" * 50)
    
    result = get_logins_evtx(evtx_path, username, computer)
    
    try:
        events = json.loads(result)
        print(f"Найдено событий: {len(events)}")
        
        if not events:
            print("Событий не найдено.")
            return
        
        for i, event in enumerate(events[:5], 1):  # Показываем первые 5
            print(f"\nСобытие #{i}:")
            print(f"  Время: {event.get('TimeCreated')}")
            print(f"  EventID: {event.get('Id')}")
            print(f"  Пользователь: {event.get('Username')}")
            print(f"  Компьютер: {event.get('Computer')}")
            print(f"  IP адрес: {event.get('IpAddress')}")
            print(f"  Группы: {', '.join(event.get('Groups', []))}")
            print(f"  Тип входа: {event.get('LogonType')}")
        
        if len(events) > 5:
            print(f"\n... и еще {len(events) - 5} событий")
            
    except Exception as e:
        print(f"Ошибка при выводе результатов: {str(e)}")
        print(f"Сырые данные: {result[:500]}...")

if __name__ == "__main__":
    main()
