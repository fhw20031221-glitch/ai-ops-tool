"""适配器接口。

职责：封装驱动差异（连接参数、超时、元数据 SQL、错误类型）。
不包含任何安全逻辑——安全判定全部在 guard.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SqlResult:
    """一次语句执行的统一结果。"""

    columns: list[str] | None = None          # None = 无结果集（纯 DML/DDL）
    rows: list[tuple] = field(default_factory=list)
    rowcount: int = 0                          # 受影响行数（无结果集时）


class BaseAdapter:
    """各数据库适配器的公共接口。"""

    def __init__(self, inst: dict):
        self.inst = inst

    # ── 连接 ────────────────────────────────────────
    def connect(self, timeout_sec: float):
        """建立连接并完成会话初始化（字符集 / search_path / 语句超时）。"""
        raise NotImplementedError

    def close(self, conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def commit(self, conn):
        conn.commit()

    def rollback(self, conn):
        try:
            conn.rollback()
        except Exception:
            pass

    # ── 执行（原样下发 SQL，安全判定已在 guard 完成）──
    def execute(self, conn, sql: str, max_rows: int | None = None) -> SqlResult:
        """执行单条语句。max_rows=None 表示全量取；否则最多取 max_rows 行。"""
        raise NotImplementedError

    # ── 元数据（信息查询子命令用）────────────────────
    def server_info(self, conn) -> str:
        raise NotImplementedError

    def list_dbs(self, conn) -> list[tuple]:
        """[(database,)] 形式。"""
        raise NotImplementedError

    def list_tables(self, conn, database: str | None = None, schema: str | None = None) -> list[tuple]:
        """列出表。database 用于 mysql，schema 用于 kingbase/pg 系。"""
        raise NotImplementedError

    def describe_table(self, conn, table: str, database: str | None = None) -> dict[str, Any]:
        """{'columns': [...], 'indexes': [...]}"""
        raise NotImplementedError

    # ── 错误映射 ────────────────────────────────────
    driver_errors: tuple[type[Exception], ...] = (Exception,)
