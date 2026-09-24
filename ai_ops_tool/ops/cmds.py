"""ops 子命令实现：list-hosts / exec / put / get。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from ..errors import AidbError, GuardError, RemoteError
from .client import connect, ensure_parent_dir, local_hash, remote_hash, run_remote
from .hosts import get_host

CMD_SEPARATOR = "\n---\n"

# Windows 盘符路径特征（E:/x 或 E:\x）：linux 主机上出现即视为 MSYS 路径转换污染
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")
# git-bash 盘符风格（/e/x）：本地路径自愈的转换依据
_MSYS_DRIVE = re.compile(r"^/([A-Za-z])/(.+)$")


def cmd_list_hosts(hc: dict) -> int:
    from rich.console import Console
    from rich.table import Table as RichTable

    table = RichTable(title="已登记主机（密码脱敏）")
    for col in ("名称", "类型", "地址", "说明"):
        table.add_column(col)
    for name, h in hc["hosts"].items():
        addr = f"{h['host']}:{h.get('port', 22)}"
        table.add_row(name, h["type"], addr, str(h.get("desc", "")))
    Console().print(table)
    print("默认参数: " + "  ".join(f"{k}={v}" for k, v in hc["defaults"].items()))
    return 0


def cmd_exec(hc: dict, name: str, command: str | None, cmd_file: str | None,
             timeout: int | None, raw: bool) -> int:
    """执行远端命令。--file 多命令时一条失败即停，退出码取该条。"""
    if bool(command) == bool(cmd_file):
        raise AidbError("exec 需要 <command> 或 --file 之一（且只能一个）")
    host_cfg, eff = get_host(hc, name)
    timeout = int(timeout or eff["timeout"])

    if cmd_file:
        cmd_file = _resolve_local(cmd_file, must_exist=True)
        text = Path(cmd_file).read_text(encoding="utf-8")
        commands = [c.strip() for c in text.split(CMD_SEPARATOR.strip()) if c.strip()]
        if raw and len(commands) > 1:
            raise AidbError("--raw 仅支持单条命令")
    else:
        commands = [command]

    client = connect(host_cfg, eff)
    try:
        final = 0
        for cmd in commands:
            if not raw:
                print(f"$ {cmd}")
            code, out, err = run_remote(client, cmd, timeout)
            if raw:
                sys.stdout.write(out)
                if out and not out.endswith("\n"):
                    print()
            else:
                if out:
                    print(out, end="" if out.endswith("\n") else "\n")
                if err.strip():
                    print("[stderr] " + err.strip()[:3000])
                print(f"[exit={code}]")
            final = code
            if code != 0:
                break
        return final
    finally:
        client.close()


def cmd_put(hc: dict, name: str, local: str, remote: str, algo: str,
            timeout: int | None) -> int:
    """上传文件 + 哈希双端门禁：不一致即删远端半截文件并拦截。"""
    return _transfer(hc, name, local, remote, algo, timeout, direction="put")


def cmd_get(hc: dict, name: str, remote: str, local: str, algo: str,
            timeout: int | None) -> int:
    """下载文件 + 哈希双端门禁：不一致即删本地半截文件并拦截。"""
    return _transfer(hc, name, local, remote, algo, timeout, direction="get")


def _resolve_local(path: str, must_exist: bool) -> str:
    """本地路径自愈：MSYS_NO_PATHCONV=1 时 git-bash 不再转换 /e/x 风格参数，
    Windows python 会把它解析为当前盘 \\e\\x 导致找不到文件——此处还原盘符写法。

    must_exist=True（put 源文件）：转换后须真实存在才采用；
    must_exist=False（get 目标文件）：尚不存在属正常，匹配盘符风格即转换。"""
    if os.path.isfile(path) or os.path.isdir(path):
        return path
    m = _MSYS_DRIVE.match(path)
    if m:
        cand = f"{m.group(1).upper()}:/{m.group(2)}"
        if not must_exist or os.path.isfile(cand):
            return cand
    return path


def _transfer(hc: dict, name: str, local: str, remote: str, algo: str,
              timeout: int | None, direction: str) -> int:
    host_cfg, eff = get_host(hc, name)
    timeout = int(timeout or eff["timeout"])
    algo = (algo or eff.get("algo", "md5")).lower()

    # git-bash(MSYS) 会把以 / 开头的参数自动转换成本地 Windows 路径再传给本工具，
    # 若不拦截会把文件传到远端的怪路径下。linux 主机 + 远端路径带盘符 = 明确的污染特征。
    if host_cfg["type"] == "linux" and _WIN_DRIVE.match(remote):
        raise GuardError(
            f"远端路径疑似被 git-bash 路径转换污染: {remote}",
            hint="MSYS 会把 /tmp/x 之类参数改写为本地 Windows 路径。"
                 "请在命令前加 MSYS_NO_PATHCONV=1 重试，"
                 "例如: MSYS_NO_PATHCONV=1 ai-ops put demo-linux a.war /root/deploy/a.war",
        )

    if direction == "put":
        local = _resolve_local(local, must_exist=True)
        if not os.path.isfile(local):
            raise RemoteError(f"本地文件不存在: {local}")
    else:
        local = _resolve_local(local, must_exist=False)
        parent = Path(local).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    client = connect(host_cfg, eff)
    try:
        if direction == "put":
            size = os.path.getsize(local)
            h_local = local_hash(local, algo)
            ensure_parent_dir(client, host_cfg["type"], remote, timeout)
            sftp = client.open_sftp()
            try:
                sftp.put(local, remote)
            finally:
                sftp.close()
        else:
            sftp = client.open_sftp()
            try:
                sftp.get(remote, local)
            finally:
                sftp.close()
            h_local = local_hash(local, algo)
            size = os.path.getsize(local)

        h_remote = remote_hash(client, host_cfg["type"], remote, algo, timeout)
    finally:
        client.close()

    # 门禁：哈希不一致 → 删半截文件 + 拦截（exit 3）
    if h_local != h_remote:
        if direction == "put":
            _silent_delete_remote(host_cfg, remote, timeout)
        else:
            Path(local).unlink(missing_ok=True)
        deleted = "远端" if direction == "put" else "本地"
        raise GuardError(
            f"{direction} 后哈希不一致（已删除{deleted}半截文件）\n"
            f"本地 {algo}: {h_local}\n远端 {algo}: {h_remote}",
            hint="多为链路中断所致，重新执行本命令即可（工具自带连接重试）",
        )

    print(f"{direction.upper()} OK size={size}  {algo}={h_local}\n"
          f"  local={local}\n  remote={remote}")
    return 0


def _silent_delete_remote(host_cfg: dict, remote_path: str, timeout: int) -> None:
    """删除远端半截文件（清理失败不掩盖门禁错误本身）。"""
    try:
        client = connect(host_cfg, {"retry": 2, "retry-wait": 2, "timeout": timeout,
                                    "keepalive": 15})
        try:
            if host_cfg["type"] == "linux":
                run_remote(client, f'rm -f "{remote_path}"', timeout)
            else:
                run_remote(client, "powershell -NoProfile -Command \"Remove-Item "
                                   f"-LiteralPath '{remote_path}' -Force\"", timeout)
        finally:
            client.close()
    except Exception:  # noqa: BLE001
        pass
