"""exec 子命令：写通道（确认机制 + 多语句事务）。"""
from __future__ import annotations

from ..config import get_instance
from ..runner import run_one
from ..tx import run_tx


def cmd_exec(cfg: dict, name: str, sqls: list[str], *, confirm, all_rows: bool,
             timeout: float | None, dry_run: bool) -> int:
    inst = get_instance(cfg, name)
    defaults = cfg["defaults"]

    # argparse nargs="?" 的 --confirm: None / True / "短语"
    confirm_value = confirm if isinstance(confirm, str) else None
    confirm_flag = confirm is True

    if len(sqls) == 1:
        return run_one(
            inst, defaults, sqls[0],
            channel="exec",
            confirm_value=confirm_value, confirm_flag=confirm_flag,
            all_rows=all_rows, dry_run=dry_run, timeout_sec=timeout,
        )
    return run_tx(
        inst, defaults, sqls,
        confirm_value=confirm_value, confirm_flag=confirm_flag,
        all_rows=all_rows, timeout_sec=timeout, dry_run=dry_run,
    )
