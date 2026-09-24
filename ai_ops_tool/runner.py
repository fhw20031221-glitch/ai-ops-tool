"""单语句执行流水：guard 校验 → 连接 → 超时 → 执行原样 SQL → 行截断。"""
from __future__ import annotations

import time

from . import guard, output
from .adapters import get_adapter
from .errors import DbError


def guard_context(inst: dict, defaults: dict):
    """构造 guard.check 所需的公共参数。"""
    default_db = inst.get("database") if inst["dialect"] == "mysql" else inst.get("schema")
    return {
        "sql_dialect": inst.get("sql_dialect", "mysql"),
        "deny_functions": defaults.get("deny_functions", []),
        "default_db": default_db or "",
    }


def run_one(
    inst: dict,
    defaults: dict,
    sql: str,
    *,
    channel: str,
    confirm_value: str | None,
    confirm_flag: bool,
    all_rows: bool,
    max_rows: int | None = None,
    fmt: str = "table",
    dry_run: bool = False,
    timeout_sec: float | None = None,
) -> int:
    """执行单条语句（query/exec 单语句形态）。返回退出码。"""
    # 1. 安全判定（无论如何都先做）
    decision = guard.check(sql, channel=channel, **guard_context(inst, defaults))

    if dry_run:
        Console_print_dry_run(decision, confirm_value, confirm_flag, all_rows)
        return 0

    # 2. 确认参数校验
    guard.verify_confirm(
        decision,
        confirm_value=confirm_value,
        confirm_flag=confirm_flag,
        all_rows=all_rows,
    )

    # 3. 连接并执行
    adapter = get_adapter(inst)
    timeout = float(timeout_sec or inst["statement_timeout_sec"])
    conn = None
    try:
        conn = adapter.connect(timeout)
        t0 = time.perf_counter()
        if decision.category == guard.READ:
            limit = (max_rows or defaults["max_rows"]) + 1  # 多取 1 行用于截断判定
            result = adapter.execute(conn, sql, max_rows=limit)
            truncated = len(result.rows) > (max_rows or defaults["max_rows"])
            if truncated:
                keep = max_rows or defaults["max_rows"]
                result.rows = result.rows[:keep]
            elapsed = (time.perf_counter() - t0) * 1000
            output.print_result(result.columns, result.rows, fmt=fmt,
                                truncated=truncated, elapsed_ms=elapsed)
        else:
            result = adapter.execute(conn, sql)
            adapter.commit(conn)
            elapsed = (time.perf_counter() - t0) * 1000
            output.print_affected(result.rowcount, elapsed_ms=elapsed)
        return 0
    except LookupError as e:
        raise DbError(str(e))
    except adapter.driver_errors as e:
        if conn is not None:
            adapter.rollback(conn)
        raise DbError(f"{type(e).__name__}: {e}")
    finally:
        adapter.close(conn)


def Console_print_dry_run(decision, confirm_value, confirm_flag, all_rows) -> None:
    from rich.console import Console
    console = Console()
    console.print("[bold]--dry-run 守卫判定（不连接数据库、不执行）[/bold]")
    console.print(decision.describe())
    # 顺带校验当前确认参数是否满足
    try:
        guard.verify_confirm(decision, confirm_value=confirm_value,
                             confirm_flag=confirm_flag, all_rows=all_rows)
        console.print("[green]当前提供的确认参数: 已满足要求[/green]")
    except Exception as e:
        console.print(f"[yellow]当前提供的确认参数: 不满足 — {e}[/yellow]")
