from __future__ import annotations

import platform
import socket
from datetime import datetime, timezone

from backend.modules.ad_connector import get_ad_connector
from backend.modules.collector_pool import CollectorPool


def get_system_info() -> dict:
    hostname = platform.node() or socket.gethostname()
    ip_address = "Не определен"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "platform": platform.system(),
        "release": platform.release(),
    }


def get_ad_status() -> str:
    ad = get_ad_connector()
    return (
        "connected"
        if (ad and ad.connection and ad.connection.bound)
        else "disconnected"
    )


def get_health(pool: CollectorPool) -> dict:
    return {
        "status": "running",
        "timestamp": datetime.now(timezone.utc),
        "database": (
            "connected" if (pool and pool.any_connected) else "disconnected"
        ),
        "active_directory": get_ad_status(),
    }
