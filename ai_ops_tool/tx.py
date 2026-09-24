"""事务执行：exec 传多条 SQL → 一个事务（全部成功才 COMMIT，任一失败 ROLLBACK）。

每条语句都独立过 guard；确认参数对全部语句生效
（多条里只要有一条 DANGEROUS，就按其确认短语要求校验）。
"""
from __future__ import annotations

import time

from . import guard, output
from .adapters import get_adapter
from .errors import DbError, GuardError


def run_tx(
    inst: dict,
    defaults: dict,
    sqls: list[str],
    *,
    confirm_value: str | None,
    confirm_flag: bool,
    all_rows: bool,
    timeout_sec: float | None = None,
    dry_run: bool = False,
) -> int:
    ctx = guard_context_of(inst, defaults)

    # 1. 全部语句先判定 + 校验确认（未连库即可拦截）
    decisions = []
    for sql in sqls:
        d = guard.check(sql, channel="exec", **ctx)
        guard.verify_confirm(d, confirm_value=confirm_value,
                             confirm_flag=confirm_flag, all_rows=all_rows)
        decisions.append(d)

    if dry_run:
        print_dry_run_tx(decisions, confirm_value, confirm_flag, all_rows)
        return 0

    adapter = get_adapter(inst)
    timeout = float(timeout_sec or inst["statement_timeout_sec"])
    conn = None
    t0 = time.perf_counter()
    try:
        conn = adapter.connect(timeout)
        for i, (sql, d) in enumerate(zip(sqls, decisions), 1):
            # 事务内带结果集的语句全量取（行数硬保护）
            result = adapter.execute(conn, sql, max_rows=defaults["absolute_max_rows"] + 1)
            if result.columns is not None:
                if len(result.rows) > defaults["absolute_max_rows"]:
                    raise GuardError(
                        f"第 {i} 条语句结果集超过 {defaults['absolute_max_rows']} 行, "
                        "事务内不允许超大结果集, 已回滚。",
                    )
                output.print_result(result.columns, result.rows, fmt="table")
            else:
                output.print_affected(result.rowcount, note=f"[第 {i}/{len(sqls)} 条 {d.kind}]")
        adapter.commit(conn)
        elapsed = (time.perf_counter() - t0) * 1000
        from rich.console import Console
        Console().print(f"[green]✓ 事务已提交（{len(sqls)} 条语句, {elapsed:.0f} ms）[/green]")
        return 0
    except adapter.driver_errors as e:
        if conn is not None:
            adapter.rollback(conn)
        raise DbError(
            f"事务失败已回滚（ROLLBACK）: {type(e).__name__}: {e}",
            hint="本次 exec 的所有语句均未生效。",
        )
    except GuardError:
        if conn is not None:
            adapter.rollback(conn)
        raise
    finally:
        adapter.close(conn)


def guard_context_of(inst, defaults):
    from .runner import guard_context
    return guard_context(inst, defaults)


def print_dry_run_tx(decisions, confirm_value, confirm_flag, all_rows) -> None:
    from rich.console import Console
    console = Console()
    console.print("[bold]--dry-run 守卫判定（不连接数据库、不执行）[/bold]")
    for i, d in enumerate(decisions, 1):
        console.print(f"[bold]第 {i}/{len(decisions)} 条[/bold]")
        console.print(d.describe())
    ok = True
    for d in decisions:
        try:
            guard.verify_confirm(d, confirm_value=confirm_value,
                                 confirm_flag=confirm_flag, all_rows=all_rows)
        except GuardError as e:
            ok = False
            console.print(f"[yellow]第 {d.kind} 条确认参数不满足: {e}[/yellow]")
    if ok:
        console.print("[green]当前提供的确认参数: 已满足全部语句要求[/green]")
