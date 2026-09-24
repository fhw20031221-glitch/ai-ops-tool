"""hosts.yaml 主机注册表。

安全规则：只有 config/hosts.yaml 中登记的主机才允许连接（与数据库 instances.yaml 同哲学）。

主机字段：
  type: linux | windows        # 决定远端命令模板（哈希、建目录等）
  host / port / user / password
  desc: 说明（list-hosts 展示）
defaults 段（可省略）：
  retry: 6            # 连接重试次数
  retry-wait: 10      # 重试间隔秒
  timeout: 60         # 远端命令默认超时秒
  keepalive: 15       # SSH keepalive 秒
  algo: md5           # 传输校验算法 md5 | sha256
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_HOSTS_FILE = PROJECT_ROOT / "config" / "hosts.yaml"

KNOWN_TYPES = ("linux", "windows")

_REQUIRED_FIELDS = ("type", "host", "user")

_DEFAULT_DEFAULTS = {
    "retry": 6,
    "retry-wait": 10,
    "timeout": 60,
    "keepalive": 15,
    "algo": "md5",
}


def load_hosts(path: str | None = None) -> dict[str, Any]:
    """加载 hosts.yaml，返回 {"defaults": {...}, "hosts": {name: cfg}}。"""
    cfg_path = _resolve_path(path)
    if not cfg_path.is_file():
        raise ConfigError(
            f"主机配置文件不存在: {cfg_path}",
            hint="用 --hosts 指定路径，或复制 config/hosts.example.yaml 为 config/hosts.yaml",
        )
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"hosts.yaml 解析失败: {cfg_path}\n{e}") from e

    defaults = dict(_DEFAULT_DEFAULTS)
    defaults.update(data.get("defaults") or {})

    hosts = data.get("hosts") or {}
    if not isinstance(hosts, dict) or not hosts:
        raise ConfigError(
            "hosts.yaml 中没有任何主机登记",
            hint=f"请在 {cfg_path} 的 hosts: 段下登记主机",
        )
    for name, hc in hosts.items():
        if not isinstance(hc, dict):
            raise ConfigError(f"主机 {name} 的配置段必须是映射")
        missing = [k for k in _REQUIRED_FIELDS if not hc.get(k)]
        if missing:
            raise ConfigError(f"主机 {name} 缺少必填字段: {', '.join(missing)}")
        if hc["type"] not in KNOWN_TYPES:
            raise ConfigError(
                f"主机 {name} 的 type 非法: {hc['type']}",
                hint=f"支持的类型: {' / '.join(KNOWN_TYPES)}",
            )
        hc["port"] = int(hc.get("port") or 22)
    return {"defaults": defaults, "hosts": hosts}


def get_host(hc: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """取主机配置（未登记即报错），返回 (主机配置, 合并 defaults 后的生效参数)。"""
    host = hc["hosts"].get(name)
    if host is None:
        raise ConfigError(
            f"主机未登记: {name}",
            hint="用 list-hosts 查看已登记主机",
        )
    eff = {k: host.get(k, v) for k, v in hc["defaults"].items()}
    return host, eff


def _resolve_path(path: str | None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("AIOPS_HOSTS")
    if env:
        return Path(env)
    return DEFAULT_HOSTS_FILE
