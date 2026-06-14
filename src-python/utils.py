import base64
import importlib
import os
import sys
from typing import Any, List, Dict, Optional
import json
import traceback
import logging
from logging.handlers import RotatingFileHandler


def _getLogDir() -> str:
    """Return the absolute directory where log files should live.

    Anchored to the executable when frozen by PyInstaller, otherwise to
    this source file. Guarantees logs do not land in whatever CWD the
    parent process happened to spawn us with (e.g. C:\\Windows\\system32
    when started by the Tauri sidecar mechanism).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

_TORCH_MODULE = None
_TORCH_IMPORT_ATTEMPTED = False
_CTRANSLATE2_GET_SUPPORTED_COMPUTE_TYPES = None
_CTRANSLATE2_IMPORT_ATTEMPTED = False

import requests
import ipaddress
import socket


def _getTorch():
    global _TORCH_MODULE, _TORCH_IMPORT_ATTEMPTED
    if _TORCH_IMPORT_ATTEMPTED is False:
        _TORCH_IMPORT_ATTEMPTED = True
        try:
            _TORCH_MODULE = importlib.import_module("torch")
        except Exception:
            _TORCH_MODULE = None
    return _TORCH_MODULE


def _getSupportedComputeTypes(device: str, device_index: int) -> List[str]:
    global _CTRANSLATE2_GET_SUPPORTED_COMPUTE_TYPES, _CTRANSLATE2_IMPORT_ATTEMPTED
    if _CTRANSLATE2_IMPORT_ATTEMPTED is False:
        _CTRANSLATE2_IMPORT_ATTEMPTED = True
        try:
            ctranslate2_module = importlib.import_module("ctranslate2")
            _CTRANSLATE2_GET_SUPPORTED_COMPUTE_TYPES = ctranslate2_module.get_supported_compute_types
        except Exception:
            _CTRANSLATE2_GET_SUPPORTED_COMPUTE_TYPES = None

    if _CTRANSLATE2_GET_SUPPORTED_COMPUTE_TYPES is None:
        return []
    return list(_CTRANSLATE2_GET_SUPPORTED_COMPUTE_TYPES(device, device_index))

def validateDictStructure(data: dict, structure: dict) -> bool:
    """
    辞書とその期待される構造（型）が完全に一致するかを判別する関数
    Args:
        data (dict): 検証対象の辞書
        structure (dict): 期待される構造を定義した辞書値には型（str, int, bool等）や入れ子の辞書を指定

    Returns:
        bool: 構造が完全に一致する場合True、そうでなければFalse
    """

    if not isinstance(data, dict) or not isinstance(structure, dict):
        return False

    # キーの数と名前が完全に一致するかチェック
    if set(data.keys()) != set(structure.keys()):
        return False

    # 各キーの値の型または構造をチェック
    for key, expected_type_or_structure in structure.items():
        if key not in data:
            return False

        value = data[key]
        # 期待される型が辞書の場合（入れ子構造）
        if isinstance(expected_type_or_structure, dict):
            # 再帰的に検証（多重入れ子に対応）
            if not validateDictStructure(value, expected_type_or_structure):
                return False
        # 期待される型が型オブジェクトの場合
        else:
            if not isinstance(value, expected_type_or_structure):
                return False
    return True

def isConnectedNetwork(url="https://clients3.google.com/generate_204", timeout=1.5) -> bool:
    """Quick network connectivity check using one or more lightweight probes.

    The default Google probe commonly returns HTTP 204, so any non-error
    success response in the 2xx/3xx range should be considered connected.
    When a single URL is supplied it is tried first, followed by a few
    built-in fallbacks to avoid false offline detections.
    """
    probe_urls = []
    if url:
        probe_urls.append(url)
    probe_urls.extend(
        probe
        for probe in (
            "https://www.google.com/generate_204",
            "https://www.cloudflare.com/cdn-cgi/trace",
            "http://www.msftconnecttest.com/connecttest.txt",
        )
        if probe not in probe_urls
    )

    for probe_url in probe_urls:
        try:
            response = requests.get(probe_url, timeout=timeout, allow_redirects=True)
            if 200 <= response.status_code < 400:
                return True
        except requests.RequestException:
            continue
    return False

def isAvailableWebSocketServer(host: str, port: int) -> bool:
    """Return True if the given host/port appear available for binding.

    Note: This attempts to bind a TCP socket to the address. If bind
    succeeds the function returns True (meaning the address was available).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as chk:
            chk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            chk.bind((host, port))
        return True
    except Exception:
        return False

def isValidIpAddress(ip_address: str) -> bool:
    """Return True if `ip_address` is a valid IPv4/IPv6 address."""
    try:
        ipaddress.ip_address(ip_address)
        return True
    except ValueError:
        return False

def getComputeDeviceList() -> List[Dict[str, Any]]:
    """Return a list of available compute devices and supported compute types.

    The returned list contains dicts describing CPU and (if available)
    CUDA devices. This function is defensive to missing optional packages.
    """
    compute_types: List[Dict[str, Any]] = [
        {
            "device": "cpu",
            "device_index": 0,
            "device_name": "cpu",
            "compute_types": ["auto"] + sorted(_getSupportedComputeTypes("cpu", 0)),
        }
    ]

    try:
        torch = _getTorch()
        if torch is not None and hasattr(torch, "cuda") and torch.cuda.is_available():
            for device_index in range(torch.cuda.device_count()):
                gpu_device_name = torch.cuda.get_device_name(device_index)
                gpu_compute_types = ["auto"] + sorted(_getSupportedComputeTypes("cuda", device_index))

                # デバイスごとの計算タイプの制限
                if "GTX" in gpu_device_name:
                    unsupported_types = {"int8_bfloat16", "bfloat16", "float16", "int8"}
                    gpu_compute_types = [t for t in gpu_compute_types if t not in unsupported_types]
                elif not any(keyword in gpu_device_name for keyword in ["RTX", "Tesla", "A100", "Quadro"]):
                    gpu_compute_types = ["float32"]

                compute_types.append(
                    {
                        "device": "cuda",
                        "device_index": device_index,
                        "device_name": gpu_device_name,
                        "compute_types": gpu_compute_types,
                    }
                )
    except Exception:
        # If querying GPU devices fails, return at least the CPU entry
        errorLogging()

    return compute_types

def getBestComputeType(device: str, device_index: int) -> str:
    """Pick the best available compute type for a device.

    Falls back to "float32" when no preferred type is available.
    """
    try:
        compute_types = set(_getSupportedComputeTypes(device, device_index))
    except Exception:
        compute_types = set()

    try:
        torch = _getTorch()
        device_name = "cpu" if device == "cpu" else (torch.cuda.get_device_name(device_index) if torch is not None else "")
    except Exception:
        device_name = ""

    # デバイスごとの優先計算タイプ
    preferred_types = {
        "default": ["int8_bfloat16", "int8_float16", "int8", "bfloat16", "float16", "int8_float32", "float32"],
        "GTX": ["float32"],
        "RTX": ["int8_bfloat16", "int8_float16", "int8", "bfloat16", "float16", "int8_float32", "float32"],
        "Tesla": ["int8_bfloat16", "int8_float16", "int8", "bfloat16", "float16", "int8_float32", "float32"],
        "A100": ["int8_bfloat16", "int8_float16", "int8", "bfloat16", "float16", "int8_float32", "float32"],
        "Quadro": ["int8_bfloat16", "int8_float16", "int8", "bfloat16", "float16", "int8_float32", "float32"],
    }

    # デバイス名に基づいて優先タイプを選択
    selected_types = preferred_types["default"]
    for key in preferred_types:
        if key in device_name:
            selected_types = preferred_types[key]
            break

    # 利用可能な計算タイプを返す
    for compute_type in selected_types:
        if compute_type in compute_types:
            return compute_type

    return "float32"

def encodeBase64(data: str) -> Dict[str, Any]:
    """Decode a base64-encoded JSON string and return the parsed object.

    Returns an empty dict on failure.
    """
    try:
        return json.loads(base64.b64decode(data).decode('utf-8'))
    except Exception:
        errorLogging()
        return {}

def removeLog() -> None:
    """Truncate the process log file (process.log) if present."""
    try:
        with open(os.path.join(_getLogDir(), 'process.log'), 'w', encoding="utf-8") as f:
            f.write("")
    except Exception:
        errorLogging()

def setupLogger(name: str, log_file: str, level: int = logging.INFO) -> logging.Logger:
    """
    特定の名前とログファイルを持つロガーを設定します。
    """
    # ロガーを作成
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # 親ロガーへの伝播を防ぐ

    # filled with 10MB logs
    max_log_size = 10 * 1024 * 1024  # 10MB

    # ハンドラーを作成
    log_path = log_file if os.path.isabs(log_file) else os.path.join(_getLogDir(), log_file)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_log_size,
        backupCount=1,
        encoding="utf-8",
        delay=True
        )
    file_handler.setLevel(level)

    # フォーマッターを設定
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # ロガーにハンドラーを追加（重複追加を避ける）
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', None) == getattr(file_handler, 'baseFilename', None) for h in logger.handlers):
        logger.addHandler(file_handler)

    return logger

process_logger: Optional[logging.Logger] = None


def printLog(log: str, data: Any = None) -> None:
    """Log and print a structured process log message."""
    global process_logger
    if process_logger is None:
        process_logger = setupLogger("process", "process.log", logging.INFO)

    response = {
        "status": 348,
        "log": log,
        "data": str(data),
    }
    process_logger.info(response)
    serialized = json.dumps(response)
    print(serialized, flush=True)

def printResponse(status: int, endpoint: str, result: Any = None) -> None:
    """Log and print a structured response object.

    If JSON serialization fails, record the error and emit a generic error payload.
    """
    global process_logger
    response = {
        "status": status,
        "endpoint": endpoint,
        "result": result,
    }

    try:
        serialized_response = json.dumps(response)
    except Exception as e:
        errorLogging()  # Log the full traceback of the exception
        try:
            process_logger.error(f"Problematic response object before json.dumps: {response}")
            process_logger.error(f"Exception during json.dumps: {e}")
        except Exception:
            pass
        # Fallback generic error payload
        error_json = json.dumps({
            "status": 500,
            "endpoint": endpoint,
            "result": {"error": "Failed to serialize response", "details": str(e)},
        })
        print(error_json, flush=True)
    else:
        print(serialized_response, flush=True)

    # The UI is waiting on stdout, so keep file logging best-effort and after the
    # response is emitted. This prevents a blocked log handler from leaving the UI
    # spinner stuck even though the handler already finished.
    try:
        if process_logger is None:
            process_logger = setupLogger("process", "process.log", logging.INFO)
        process_logger.info(response)
    except Exception:
        errorLogging()

error_logger: Optional[logging.Logger] = None


def errorLogging() -> None:
    """Log the current exception traceback to the error logger."""
    global error_logger
    if error_logger is None:
        error_logger = setupLogger("error", "error.log", logging.ERROR)

    try:
        error_logger.error(traceback.format_exc())
    except Exception:
        # As a last resort, print the traceback to stdout
        print(traceback.format_exc(), flush=True)

if __name__ == "__main__":
    print(getComputeDeviceList())
