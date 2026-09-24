"""MySQL 适配器（pymysql）。"""
from __future__ import annotations

from typing import Any

import pymysql
import pymysql.cursors

from .base import BaseAdapter, SqlResult


class MysqlAdapter(BaseAdapter):
    driver_errors = (pymysql.err.Error,)

    def connect(self, timeout_sec: float):
        inst = self.inst
        conn = pymysql.connect(
            host=inst["host"],
            port=int(inst["port"]),
            user=inst["user"],
            password=inst.get("password") or "",
            database=inst.get("database") or None,
            charset="utf8mb4",
            connect_timeout=int(inst["connect_timeout_sec"]),
            read_timeout=int(timeout_sec) + 5,   # 网络读取兜底（服务器端超时之外）
            write_timeout=int(timeout_sec) + 5,
            autocommit=False,
            cursorclass=pymysql.cursors.Cursor,
        )
        # MySQL 8.0+: 服务器端 SELECT 超时（毫秒）；老版本或非 SELECT 由 read_timeout 兜底
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(timeout_sec * 1000)}")
        except pymysql.err.Error:
            pass
        return conn

    def execute(self, conn, sql: str, max_rows: int | None = None) -> SqlResult:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is not None:
                columns = [d[0] for d in cur.description]
                if max_rows is None:
                    rows = list(cur.fetchall())
                else:
                    rows = list(cur.fetchmany(max_rows))
                return SqlResult(columns=columns, rows=rows, rowcount=len(rows))
            return SqlResult(columns=None, rows=[], rowcount=cur.rowcount)

    def server_info(self, conn) -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            row = cur.fetchone()
        return f"MySQL {row[0]}"

    def list_dbs(self, conn) -> list[tuple]:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = cur.fetchall()
        return [(r[0],) for r in rows]

    def list_tables(self, conn, database: str | None = None, schema: str | None = None) -> list[tuple]:
        db = database or self.inst.get("database")
        sql = (
            "SELECT table_name, table_rows, "
            "ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb "
            "FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        with conn.cursor() as cur:
            cur.execute(sql, (db,))
            return list(cur.fetchall())

    def describe_table(self, conn, table: str, database: str | None = None) -> dict[str, Any]:
        db, name = _split_qualified(table, self.inst.get("database"))
        cols_sql = (
            "SELECT column_name, column_type, is_nullable, column_default, "
            "column_key, extra "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position"
        )
        idx_sql = (
            "SELECT index_name, non_unique, "
            "GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols "
            "FROM information_schema.statistics "
            "WHERE table_schema = %s AND table_name = %s "
            "GROUP BY index_name, non_unique "
            "ORDER BY index_name"
        )
        with conn.cursor() as cur:
            cur.execute(cols_sql, (db, name))
            columns = [
                {"列": c[0], "类型": c[1], "可空": c[2],
                 "默认值": _fmt(c[3]), "键": c[4] or "", "额外": c[5] or ""}
                for c in cur.fetchall()
            ]
            cur.execute(idx_sql, (db, name))
            indexes = [
                {"索引": i[0], "唯一": "否" if i[1] else "是", "列": i[2]}
                for i in cur.fetchall()
            ]
        if not columns:
            raise LookupError(f"表不存在或没有列: {db}.{name}")
        return {"columns": columns, "indexes": indexes, "database": db, "table": name}


def _split_qualified(table: str, default_db: str | None) -> tuple[str, str]:
    """拆 `db.table` 或 `table`（去反引号）。"""
    parts = [p.strip("`\" ") for p in table.split(".")]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return default_db or "", parts[0]


def _fmt(v) -> str:
    return "" if v is None else str(v)
