"""信息查询子命令：list-instances / ping / list-dbs / tables / schema。"""
from __future__ import annotations

import time

from rich.console import Console
from rich.table import Table as RichTable

from ..adapters import get_adapter
from ..config import mask_password
from ..errors import DbError, GuardError

console = Console()


def cmd_list_instances(cfg: dict) -> int:
    table = RichTable(header_style="bold cyan")
    table.add_column("实例名")
    table.add_column("类型")
    table.add_column("主机")
    table.add_column("默认库/Schema")
    table.add_column("用户")
    table.add_column("密码")
    for name, inst in sorted(cfg["instances"].items()):
        target = inst.get("database") or ""
        if inst.get("schema"):
            target = f"{target}/{inst['schema']}"
        table.add_row(
            name,
            inst["dialect"],
            f"{inst['host']}:{inst['port']}",
            target,
            inst["user"],
            mask_password(inst.get("password")),
        )
    console.print(table)
    console.print(f"[dim]配置文件: {cfg['path']}[/dim]")
    return 0


def _open(cfg: dict, name: str, timeout_sec: float | None = None):
    from ..config import get_instance
    inst = get_instance(cfg, name)
    adapter = get_adapter(inst)
    timeout = float(timeout_sec or 5)
    try:
        conn = adapter.connect(timeout)
    except adapter.driver_errors as e:
        raise DbError(f"连接失败（{name}）: {type(e).__name__}: {e}")
    return inst, adapter, conn


def cmd_ping(cfg: dict, name: str) -> int:
    t0 = time.perf_counter()
    inst, adapter, conn = _open(cfg, name)
    try:
        info = adapter.server_info(conn)
    except adapter.driver_errors as e:
        raise DbError(f"查询服务器信息失败: {e}")
    finally:
        adapter.close(conn)
    ms = (time.perf_counter() - t0) * 1000
    console.print(f"[green]✓ OK[/green] {name} → {inst['host']}:{inst['port']}  "
                  f"{info}  ({ms:.0f} ms)")
    return 0


def cmd_list_dbs(cfg: dict, name: str) -> int:
    inst, adapter, conn = _open(cfg, name)
    try:
        rows = adapter.list_dbs(conn)
    except adapter.driver_errors as e:
        raise DbError(f"列库失败: {e}")
    finally:
        adapter.close(conn)
    current = inst.get("database")
    table = RichTable(header_style="bold cyan")
    table.add_column("数据库")
    for (db,) in rows:
        mark = " [cyan](默认)[/cyan]" if db == current else ""
        table.add_row(str(db) + mark)
    console.print(table)
    return 0


def cmd_tables(cfg: dict, name: str, database: str | None, schema: str | None) -> int:
    inst, adapter, conn = _open(cfg, name)
    try:
        rows = adapter.list_tables(conn, database=database, schema=schema)
    except adapter.driver_errors as e:
        raise DbError(f"列表失败: {e}")
    finally:
        adapter.close(conn)
    if not rows:
        target = schema or database or inst.get("schema") or inst.get("database")
        console.print(f"[yellow]库 {target} 中没有表[/yellow]")
        return 0
    table = RichTable(header_style="bold cyan")
    headers = ["表名", "估计行数", "大小(MB)"] if len(rows[0]) >= 3 else ["表名"]
    for h in headers:
        table.add_column(h)
    for r in rows:
        table.add_row(*[str(v) if v is not None else "" for v in r])
    console.print(table)
    console.print(f"[dim]共 {len(rows)} 张表[/dim]")
    return 0


def cmd_schema(cfg: dict, name: str, table: str, database: str | None) -> int:
    inst, adapter, conn = _open(cfg, name)
    try:
        info = adapter.describe_table(conn, table, database=database)
    except LookupError as e:
        raise DbError(str(e), hint="先用 tables 子命令查看有哪些表")
    except adapter.driver_errors as e:
        raise DbError(f"查询表结构失败: {e}")
    finally:
        adapter.close(conn)

    console.print(f"[bold]{info['database']}.{info['table']}[/bold] 列信息:")
    cols = RichTable(header_style="bold cyan")
    for h in info["columns"][0].keys():
        cols.add_column(h)
    for c in info["columns"]:
        cols.add_row(*[str(v) for v in c.values()])
    console.print(cols)

    if info.get("indexes"):
        console.print("索引:")
        idx = RichTable(header_style="bold cyan")
        for h in info["indexes"][0].keys():
            idx.add_column(h)
        for i in info["indexes"]:
            idx.add_row(*[str(v) for v in i.values()])
        console.print(idx)
    return 0
