from __future__ import annotations

import csv
import subprocess
from io import StringIO
from typing import Any

import psutil


def clamp_percent(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(100.0, number)), 1)


def _metric(available: bool, percent: float | None) -> dict:
    return {
        "available": available,
        "percent": clamp_percent(percent) if percent is not None else None,
    }


def _query_nvidia_smi(selected_gpu_index: int | None = None) -> dict | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except Exception:
        return None

    rows = list(csv.reader(StringIO(completed.stdout.strip())))
    if not rows:
        return None

    parsed_rows = []
    for row in rows:
        try:
            parsed_rows.append(
                {
                    "device_index": int(str(row[0]).strip()),
                    "device_name": str(row[1]).strip(),
                    "gpu_percent": clamp_percent(row[2]),
                    "memory_used": float(row[3]),
                    "memory_total": float(row[4]),
                }
            )
        except (IndexError, TypeError, ValueError):
            continue

    if not parsed_rows:
        return None

    selected_row = next(
        (row for row in parsed_rows if row["device_index"] == selected_gpu_index),
        parsed_rows[0],
    )

    vram_percent = None
    if selected_row["memory_total"] > 0:
        vram_percent = clamp_percent((selected_row["memory_used"] / selected_row["memory_total"]) * 100)

    return {
        "gpu": _metric(True, selected_row["gpu_percent"]),
        "vram": _metric(vram_percent is not None, vram_percent),
        "gpu_devices": [
            {
                "device_index": row["device_index"],
                "device_name": row["device_name"],
            }
            for row in parsed_rows
        ],
        "selected_gpu_index": selected_row["device_index"],
    }


def collect_resource_usage(selected_gpu_index: int | None = None) -> dict:
    gpu = _query_nvidia_smi(selected_gpu_index)
    return {
        "cpu": _metric(True, psutil.cpu_percent(interval=None)),
        "ram": _metric(True, psutil.virtual_memory().percent),
        "gpu": gpu["gpu"] if gpu else _metric(False, None),
        "vram": gpu["vram"] if gpu else _metric(False, None),
        "gpu_devices": gpu["gpu_devices"] if gpu else [],
        "selected_gpu_index": gpu["selected_gpu_index"] if gpu else None,
    }
