# -*- coding: utf-8 -*-
"""公共默认配置与本机运行态配置的隔离存储。"""

import json
import os


RUNTIME_KEYS = ("package", "process_pattern")


def runtime_config_path(config_path):
    """返回与基础配置同目录、但不会入库的 ``*.local.json`` 路径。"""
    root, ext = os.path.splitext(config_path)
    return root + ".local" + (ext or ".json")


def load_config(config_path, log=print):
    """读取公共配置，并用本机目标选择覆盖；本机文件损坏时安全降级。"""
    with open(config_path, encoding="utf-8") as stream:
        config = json.load(stream)
    local_path = runtime_config_path(config_path)
    try:
        with open(local_path, encoding="utf-8") as stream:
            local = json.load(stream)
        if not isinstance(local, dict):
            raise ValueError("根节点必须是对象")
        overrides = {}
        for key in RUNTIME_KEYS:
            if key in local:
                if not isinstance(local[key], str):
                    raise ValueError(f"{key} 必须是字符串")
                overrides[key] = local[key]
        config.update(overrides)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as exc:
        log(f"[!] 本机目标配置读取失败，已使用公共默认值: {exc}")
    return config


def persist_runtime_target(config_path, package, process_pattern):
    """原子保存本机目标选择；不修改受版本控制的公共配置。"""
    local_path = runtime_config_path(config_path)
    tmp_path = local_path + ".tmp"
    payload = {
        "package": (package or "").strip(),
        "process_pattern": process_pattern or "",
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, local_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise
    return local_path
