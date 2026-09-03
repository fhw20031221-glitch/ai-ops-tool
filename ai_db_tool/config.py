"""配置加载：YAML 实例注册表。

安全规则 1 的实现：只有 config/instances.yaml 中登记的实例才允许连接。

加载优先级：--config PATH > 环境变量 AIDB_CONFIG > 项目内默认 config/instances.yaml
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

# 项目根目录（ai_db_tool/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "instances.yaml"

# 必填字段（dialect 决定驱动）
_REQUIRED_FIELDS = ("dialect", "host", "port", "user")

_KNOWN_DIALECTS = ("mysql", "kingbase")

# guard 默认参数的键与默认值（与 instances.yaml 的 defaults 段对应）
_DEFAULT_GUARD = {
    "max_rows": 200,
    "absolute_max_rows": 5000,
    "statement_timeout_sec": 30,
    "connect_timeout_sec": 8,
    "deny_functions": [
        "load_file", "benchmark", "sleep",
        "pg_sleep", "pg_read_file", "pg_read_binary_file",
        "pg_ls_dir", "pg_terminate_backend", "lo_import", "lo_export",
    ],
}


def load_config(path: str | None = None) -> dict[str, Any]:
    """加载完整配置（defaults + instances）。"""
    cfg_path = _resolve_path(path)
    if not cfg_path.is_file():
        raise ConfigError(
            f"配置文件不存在: {cfg_path}",
            hint="用 --config 指定路径，或创建默认 config/instances.yaml",
        )
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 YAML 解析失败: {cfg_path}\n{e}")

    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射: {cfg_path}")

    instances = data.get("instances")
    if not isinstance(instances, dict) or not instances:
        raise ConfigError(
            "配置中没有任何实例（instances 段为空）",
            hint=f"在 {cfg_path} 的 instances: 下登记数据库实例",
        )

    defaults = _merge_defaults(data.get("defaults") or {})
    for name, inst in instances.items():
        _validate_instance(name, inst, defaults)
    return {"defaults": defaults, "instances": instances, "path": str(cfg_path)}


def _resolve_path(path: str | None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env = os.environ.get("AIDB_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_CONFIG


def _merge_defaults(overrides: dict) -> dict:
    merged = dict(_DEFAULT_GUARD)
    deny = list(_DEFAULT_GUARD["deny_functions"])
    for k, v in overrides.items():
        if k == "deny_functions":
            deny = [str(f).lower() for f in v] if v else deny
        elif k in merged:
            merged[k] = v
    merged["deny_functions"] = [f.lower() for f in deny]
    return merged


def _validate_instance(name: str, inst: Any, defaults: dict) -> None:
    if not isinstance(inst, dict):
        raise ConfigError(f"实例 {name} 配置必须是映射")
    for field in _REQUIRED_FIELDS:
        if field not in inst:
            raise ConfigError(f"实例 {name} 缺少必填字段: {field}")
    dialect = str(inst["dialect"]).lower()
    if dialect not in _KNOWN_DIALECTS:
        raise ConfigError(
            f"实例 {name} 的 dialect={dialect!r} 不受支持（可选: {', '.join(_KNOWN_DIALECTS)}）"
        )
    # 实例级可覆盖的 guard 参数
    for key in ("max_rows", "absolute_max_rows", "statement_timeout_sec", "connect_timeout_sec"):
        if key not in inst:
            inst[key] = defaults[key]
    inst.setdefault("sql_dialect", "mysql" if dialect == "mysql" else "postgres")


def get_instance(cfg: dict, name: str) -> dict:
    """按名字取实例；不存在即 ConfigError（安全规则 1）。"""
    inst = cfg["instances"].get(name)
    if inst is None:
        known = ", ".join(sorted(cfg["instances"]))
        raise ConfigError(
            f"实例不存在: {name}",
            hint=f"已配置的实例: {known}。"
                 f"新实例需在 {cfg['path']} 的 instances: 段登记后再使用。",
        )
    return inst


def mask_password(password: str | None) -> str:
    """密码脱敏显示：保留首尾字符。"""
    if not password:
        return "(空)"
    if len(password) <= 2:
        return "*" * len(password)
    return f"{password[0]}***{password[-1]}"
