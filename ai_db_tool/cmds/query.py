"""query 子命令：只读通道。"""
from __future__ import annotations

from ..config import get_instance
from ..errors import GuardError
from ..runner import run_one


def cmd_query(cfg: dict, name: str, sql: str, *, max_rows: int | None,
              fmt: str, timeout: float | None, dry_run: bool) -> int:
    inst = get_instance(cfg, name)
    defaults = cfg["defaults"]

    # --max-rows 硬上限
    if max_rows is not None and max_rows > defaults["absolute_max_rows"]:
        raise GuardError(
            f"--max-rows {max_rows} 超过硬上限 {defaults['absolute_max_rows']}。",
            hint="更大的结果请给 SQL 自行加 LIMIT/OFFSET 分页。",
        )
    return run_one(
        inst, defaults, sql,
        channel="query",
        confirm_value=None, confirm_flag=False, all_rows=False,
        max_rows=max_rows, fmt=fmt, dry_run=dry_run, timeout_sec=timeout,
    )
