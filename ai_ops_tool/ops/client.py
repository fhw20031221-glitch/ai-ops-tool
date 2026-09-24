"""SSH 连接与远端命令执行。

内置纪律（来自多现场实战）：
- 连接重试（VPN/边界设备链路抖动常表现为 banner 超时，重试即成）
- keepalive（长传输链路保活）
- 远端命令原样直达：工具不解析、不包装命令内容，输出与退出码可信返回
- 哈希命令按主机 type 适配（linux=md5sum/sha256sum, windows=Get-FileHash）
"""
from __future__ import annotations

import hashlib
import sys
import time
from typing import Any

import paramiko

from ..errors import ConfigError, RemoteError

_CONNECT_TIMEOUT = 25


def connect(host_cfg: dict[str, Any], eff: dict[str, Any]) -> paramiko.SSHClient:
    """建立 SSH 连接（带重试与 keepalive）。"""
    retries = max(1, int(eff.get("retry", 6)))
    wait = max(0, int(eff.get("retry-wait", 10)))
    keepalive = int(eff.get("keepalive", 15))

    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                host_cfg["host"],
                port=int(host_cfg.get("port", 22)),
                username=host_cfg["user"],
                password=host_cfg.get("password"),
                timeout=_CONNECT_TIMEOUT,
                banner_timeout=_CONNECT_TIMEOUT,
                auth_timeout=_CONNECT_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
            )
            client.get_transport().set_keepalive(keepalive)
            return client
        except Exception as e:  # noqa: BLE001 - paramiko 异常族 diverse
            last = e
            if attempt < retries:
                print(f"[retry {attempt}/{retries}] {e}", file=sys.stderr)
                time.sleep(wait)
    raise RemoteError(
        f"连接 {host_cfg['user']}@{host_cfg['host']}:{host_cfg.get('port', 22)} "
        f"失败（已重试 {retries} 次）: {last}"
    )


def _decode(data: bytes) -> str:
    """远端输出解码：UTF-8 失败时回退 GBK（中文 Windows 的 cmd 默认 OEM 代码页 936）。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk")
        except UnicodeDecodeError:
            return data.decode("utf-8", "replace")


def run_remote(client: paramiko.SSHClient, cmd: str, timeout: int) -> tuple[int, str, str]:
    """执行远端命令，返回 (exit_code, stdout, stderr)。命令原样直达不二次包装。"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = _decode(stdout.read())
    err = _decode(stderr.read())
    return code, out, err


# ── OS 适配点：远端命令模板（新增 OS 类型只需在此扩充） ──

HASH_CMD = {
    "linux": {
        "md5": 'md5sum "{path}"',
        "sha256": 'sha256sum "{path}"',
    },
    "windows": {
        "md5": "powershell -NoProfile -Command \"(Get-FileHash -LiteralPath '{path}' "
               "-Algorithm MD5).Hash.ToLower()\"",
        "sha256": "powershell -NoProfile -Command \"(Get-FileHash -LiteralPath '{path}' "
                  "-Algorithm SHA256).Hash.ToLower()\"",
    },
}

MKDIR_CMD = {
    "linux": 'mkdir -p "{path}"',
    "windows": "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force "
               "-Path '{path}' | Out-Null\"",
}

_ALGO_HEXLEN = {"md5": 32, "sha256": 64}


def remote_hash(client: paramiko.SSHClient, host_type: str, path: str, algo: str,
                timeout: int) -> str:
    """取远端文件哈希（小写 hex）。host_type 决定命令模板。"""
    try:
        tmpl = HASH_CMD[host_type][algo]
    except KeyError:
        raise ConfigError(f"不支持的组合: type={host_type}, algo={algo}") from None
    code, out, err = run_remote(client, tmpl.format(path=path), timeout)
    value = out.strip().split()[0] if out.strip() else ""
    if code != 0 or len(value) != _ALGO_HEXLEN[algo]:
        raise RemoteError(
            f"获取远端哈希失败: {path}\nexit={code} {err.strip()[:300] or out.strip()[:300]}"
        )
    return value.lower()


def local_hash(path: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_parent_dir(client: paramiko.SSHClient, host_type: str, remote_path: str,
                      timeout: int) -> None:
    """确保远端父目录存在（put 前置；linux 用 mkdir -p，windows 用 New-Item）。"""
    parent = remote_path.replace("\\", "/").rsplit("/", 1)[0]
    if not parent or parent == remote_path:
        return
    tmpl = MKDIR_CMD[host_type]
    code, _, err = run_remote(client, tmpl.format(path=parent), timeout)
    if code != 0:
        raise RemoteError(f"创建远端目录失败: {parent}\n{err.strip()[:300]}")
