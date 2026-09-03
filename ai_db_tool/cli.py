"""CLI 入口：全部子命令注册与分发。

调用方式（三选一）:
  python E:/codes/ai-db-tool/run.py <子命令> ...
  python -m ai_db_tool <子命令> ...
  ai-db <子命令> ...        (pip install -e . 之后)
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import load_config
from .errors import AidbError

EPILOG = """\
示例:
  %(prog)s list-instances                          # 查看已登记的数据库实例
  %(prog)s ping --db mysql-test                    # 测试连通性
  %(prog)s tables --db mysql-test                  # 列出表
  %(prog)s schema --db mysql-test 表名             # 查看表结构
  %(prog)s query --db mysql-test "SELECT * FROM t LIMIT 10"
  %(prog)s exec --db mysql-test "INSERT INTO t(id) VALUES (1)" --confirm
  %(prog)s exec --db mysql-test "DROP TABLE t" --confirm "mydb.t"
                                                   # 危险操作须先征得用户同意!
安全分级:
  只读(SELECT/SHOW/DESC/EXPLAIN) 直接执行; 写操作(INSERT/UPDATE/DELETE/CREATE 等)
  需 --confirm; 危险操作(DROP/TRUNCATE/ALTER..DROP) 需 --confirm "<对象完整名>"。
退出码: 0 成功 | 1 DB错误 | 2 参数错误 | 3 被安全守卫拦截 | 4 配置错误
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-db",
        description="AI 数据库安全操作工具 (MySQL / Kingbase)。所有命令先经安全守卫分级, "
                    "危险操作必须携带确认参数。",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", metavar="PATH", help="配置文件路径 (默认: 项目内 "
                                                    "config/instances.yaml, 或环境变量 AIDB_CONFIG)")

    sub = p.add_subparsers(dest="cmd", metavar="<子命令>")

    # ── list-instances ──
    sp = sub.add_parser("list-instances", help="列出已配置的数据库实例",
                        description="列出配置文件中登记的全部实例（密码脱敏）。"
                                    "只有登记过的实例才允许连接。")
    sp.set_defaults(func="list_instances")

    # ── ping ──
    sp = sub.add_parser("ping", help="测试实例连通性",
                        description="建立连接并查询服务器版本, 验证实例可用。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名 (见 list-instances)")
    sp.set_defaults(func="ping")

    # ── list-dbs ──
    sp = sub.add_parser("list-dbs", help="列出服务器上的所有数据库",
                        description="列出该实例所在服务器上的全部数据库。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.set_defaults(func="list_dbs")

    # ── tables ──
    sp = sub.add_parser("tables", help="列出库中的表",
                        description="列出表（MySQL 按库、Kingbase 按 schema）。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("--database", metavar="D", help="MySQL: 指定库 (默认用实例配置的库)")
    sp.add_argument("--schema", metavar="S", help="Kingbase: 指定 schema (默认用实例配置)")
    sp.set_defaults(func="tables")

    # ── schema ──
    sp = sub.add_parser("schema", help="查看表结构",
                        description="显示列（类型/可空/默认值/键）与索引。表名可带库名前缀。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("table", metavar="TABLE", help="表名 (可带前缀, 如 mydb.t1)")
    sp.add_argument("--database", metavar="D", help="MySQL: 指定库 (默认用实例配置的库)")
    sp.set_defaults(func="schema")

    # ── query ──
    sp = sub.add_parser(
        "query", help="执行只读 SQL（SELECT/SHOW/DESC/EXPLAIN）",
        description="只读通道: 仅放行 SELECT/SHOW/DESC/EXPLAIN, 永不写库。"
                    "结果默认最多 200 行, 超出自动截断。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("sql", metavar="SQL", help="单条只读 SQL")
    sp.add_argument("--max-rows", type=int, metavar="N", default=None,
                    help=f"返回行数上限 (默认 200, 硬上限 5000)")
    sp.add_argument("--format", choices=["table", "tsv", "csv", "json"], default="table",
                    help="输出格式 (默认 table; json 适合程序处理)")
    sp.add_argument("--timeout", type=float, metavar="SEC", default=None,
                    help="语句超时秒数 (默认 30)")
    sp.add_argument("--dry-run", action="store_true",
                    help="只打印守卫判定, 不连库不执行")
    sp.set_defaults(func="query")

    # ── exec ──
    sp = sub.add_parser(
        "exec", help="执行写操作 / DDL（需确认）",
        description="写通道。写操作(INSERT/UPDATE/DELETE/CREATE TABLE 等)需 --confirm; "
                    "危险操作(DROP/TRUNCATE/ALTER..DROP 等)需 --confirm \"<对象完整名>\", "
                    "且必须先把完整 SQL 展示给用户并征得明确同意。"
                    "传入多条 SQL 时自动作为一个事务: 全部成功才提交, 任一失败全部回滚。"
                    "UPDATE/DELETE 无 WHERE 条件时另需 --confirm-all-rows。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("sqls", nargs="+", metavar="SQL",
                    help="一条或多条 SQL (多条=一个事务)")
    sp.add_argument("--confirm", nargs="?", const=True, default=None,
                    metavar="PHRASE",
                    help="确认参数: 写操作用 --confirm 即可; 危险操作须 "
                         '--confirm "库.表" (精确匹配)')
    sp.add_argument("--confirm-all-rows", action="store_true",
                    help="确认 UPDATE/DELETE 影响全表（无 WHERE 时必需）")
    sp.add_argument("--timeout", type=float, metavar="SEC", default=None,
                    help="语句超时秒数 (默认 30)")
    sp.add_argument("--dry-run", action="store_true",
                    help="只打印守卫判定与所需确认参数, 不连库不执行")
    sp.set_defaults(func="exec")

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台中文输出保障
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        cfg = load_config(args.config)
        if args.func == "list_instances":
            from .cmds.info import cmd_list_instances
            return cmd_list_instances(cfg)
        if args.func == "ping":
            from .cmds.info import cmd_ping
            return cmd_ping(cfg, args.db)
        if args.func == "list_dbs":
            from .cmds.info import cmd_list_dbs
            return cmd_list_dbs(cfg, args.db)
        if args.func == "tables":
            from .cmds.info import cmd_tables
            return cmd_tables(cfg, args.db, args.database, args.schema)
        if args.func == "schema":
            from .cmds.info import cmd_schema
            return cmd_schema(cfg, args.db, args.table, args.database)
        if args.func == "query":
            from .cmds.query import cmd_query
            return cmd_query(cfg, args.db, args.sql, max_rows=args.max_rows,
                             fmt=args.format, timeout=args.timeout, dry_run=args.dry_run)
        if args.func == "exec":
            from .cmds.exec_ import cmd_exec
            return cmd_exec(cfg, args.db, args.sqls, confirm=args.confirm,
                            all_rows=args.confirm_all_rows,
                            timeout=args.timeout, dry_run=args.dry_run)
        parser.error(f"未知子命令: {args.func}")
        return 2
    except AidbError as e:
        print(e.render(), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("\n[中断] 已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
