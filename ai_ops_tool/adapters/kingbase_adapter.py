"""Kingbase（人大金仓 V8R6）适配器。

Kingbase 兼容 PostgreSQL wire 协议，使用 psycopg2 连接。
服务器通常配置为 MySQL 兼容模式：SQL 按 MySQL 方言书写由服务器解析，
guard 同样按 MySQL 方言分析（见 config 的 sql_dialect）。
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from .base import BaseAdapter, SqlResult


class KingbaseAdapter(BaseAdapter):
    driver_errors = (psycopg2.Error,)

    def connect(self, timeout_sec: float):
        inst = self.inst
        schema = inst.get("schema")
        options = f"-c statement_timeout={int(timeout_sec * 1000)}"
        if schema:
            options += f" -c search_path={schema}"
        conn = psycopg2.connect(
            host=inst["host"],
            port=int(inst["port"]),
            user=inst["user"],
            password=inst.get("password") or "",
            dbname=inst.get("database") or "test",
            options=options,
            connect_timeout=int(inst["connect_timeout_sec"]),
            application_name="ai-db-tool",
        )
        # 双保险：显式 SET 一次 search_path
        if schema:
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}"')
            conn.commit()
        return conn

    def execute(self, conn, sql: str, max_rows: int | None = None) -> SqlResult:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is not None:
                columns = [d[0] for d in cur.description]
                if max_rows is None:
                    rows = [tuple(r) for r in cur.fetchall()]
                else:
                    rows = [tuple(r) for r in cur.fetchmany(max_rows)]
                return SqlResult(columns=columns, rows=rows, rowcount=len(rows))
            return SqlResult(columns=None, rows=[], rowcount=cur.rowcount)

    def server_info(self, conn) -> str:
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            row = cur.fetchone()
        ver = row[0] if row else "?"
        return f"Kingbase (server_version={ver})"

    def list_dbs(self, conn) -> list[tuple]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database "
                "WHERE datallowconn ORDER BY datname"
            )
            return [(r[0],) for r in cur.fetchall()]

    def _current_schema(self, conn) -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT current_schema()")
            return cur.fetchone()[0]

    def list_tables(self, conn, database: str | None = None, schema: str | None = None) -> list[tuple]:
        # database 参数在 PG 系无意义（连库即定库），schema 才是命名空间
        target = schema or self.inst.get("schema") or self._current_schema(conn)
        excludes = tuple(self.inst.get("schema_excludes") or ["information_schema", "pg_catalog"])
        # DISTINCT: 金仓的 information_schema.tables 对同名表可能返回重复行
        sql = (
            "SELECT DISTINCT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        with conn.cursor() as cur:
            cur.execute(sql, (target,))
            return [(r[0],) for r in cur.fetchall()]

    def describe_table(self, conn, table: str, database: str | None = None) -> dict[str, Any]:
        from .mysql_adapter import _split_qualified
        schema, name = _split_qualified(table, self.inst.get("schema"))
        # GROUP BY 全列去重: 金仓系统视图可能返回重复行
        cols_sql = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "GROUP BY column_name, data_type, is_nullable, column_default, ordinal_position "
            "ORDER BY ordinal_position"
        )
        idx_sql = (
            "SELECT DISTINCT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = %s ORDER BY indexname"
        )
        with conn.cursor() as cur:
            cur.execute(cols_sql, (schema, name))
            columns = [
                {"列": c[0], "类型": c[1], "可空": c[2], "默认值": c[3] or ""}
                for c in cur.fetchall()
            ]
            cur.execute(idx_sql, (schema, name))
            indexes = [
                {"索引": i[0], "定义": i[1]}
                for i in cur.fetchall()
            ]
        if not columns:
            raise LookupError(f"表不存在或没有列: {schema}.{name}")
        return {"columns": columns, "indexes": indexes, "database": schema, "table": name}
