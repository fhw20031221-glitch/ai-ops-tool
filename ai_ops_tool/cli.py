"""CLI 入口：子命令注册与分发。

调用方式:
  ai-ops <子命令> ...            主入口（远程运维）
  ai-ops db <子命令> ...         数据库附加功能
  ai-db <子命令> ...             兼容入口（等价 ai-ops db ...）
  python run.py / python -m ai_ops_tool 同样可用
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .errors import AidbError

EPILOG_OPS = """\
远程运维示例:
  %(prog)s list-hosts                                        # 查看已登记主机
  %(prog)s exec demo-linux "systemctl is-active nginx"     # 执行远端命令
  %(prog)s exec demo-linux --file cmds.txt                      # 按文件批量执行(--- 分隔)
  %(prog)s put demo-linux dist/app.war /root/deploy/app.war     # 上传+哈希双端门禁
  %(prog)s get  demo-linux /var/log/app.log ./app.log           # 下载+哈希双端门禁
设计约定:
  命令原样直达远端 shell(工具不解析不包装); put/get 自动做本地+远端哈希比对,
  不一致即删除半截文件并拦截(exit 3)。连接失败自动重试(链路抖动场景)。
退出码: 0 成功 | 1 远程错误 | 2 参数错误 | 3 被门禁拦截 | 4 配置错误
"""

EPILOG_DB = """\
数据库示例:
  %(prog)s db list-instances                          # 查看已登记的数据库实例
  %(prog)s db query --db mysql-test "SELECT 1"        # 只读查询
  %(prog)s db exec --db mysql-test "INSERT ..." --confirm   # 写操作需确认
兼容入口:
  ai-db <子命令> 等价于 %(prog)s db <子命令>
"""


def _add_db_commands(sub) -> None:
    """注册数据库子命令（挂到给定 subparsers，兼容历史 ai-db 全部用法）。"""
    sp = sub.add_parser("list-instances", help="列出已配置的数据库实例",
                        description="列出配置文件中登记的全部实例（密码脱敏）。")
    sp.set_defaults(group="db", func="list_instances")

    sp = sub.add_parser("ping", help="测试实例连通性")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.set_defaults(group="db", func="ping")

    sp = sub.add_parser("list-dbs", help="列出服务器上的所有数据库")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.set_defaults(group="db", func="list_dbs")

    sp = sub.add_parser("tables", help="列出库中的表")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("--database", metavar="D", help="MySQL: 指定库")
    sp.add_argument("--schema", metavar="S", help="Kingbase: 指定 schema")
    sp.set_defaults(group="db", func="tables")

    sp = sub.add_parser("schema", help="查看表结构")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("table", metavar="TABLE", help="表名 (可带前缀, 如 mydb.t1)")
    sp.add_argument("--database", metavar="D", help="MySQL: 指定库")
    sp.set_defaults(group="db", func="schema")

    sp = sub.add_parser("query", help="执行只读 SQL（SELECT/SHOW/DESC/EXPLAIN）")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("sql", metavar="SQL", help="单条只读 SQL")
    sp.add_argument("--max-rows", type=int, metavar="N", default=None)
    sp.add_argument("--format", choices=["table", "tsv", "csv", "json"], default="table")
    sp.add_argument("--timeout", type=float, metavar="SEC", default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(group="db", func="query")

    sp = sub.add_parser("exec", help="执行写操作 / DDL（需确认）",
                        description="写操作需 --confirm; 危险操作(DROP/TRUNCATE/ALTER..DROP) "
                                    '需 --confirm "<对象完整名>"，且必须先把完整 SQL 展示给用户'
                                    "并征得明确同意。多条 SQL 作为一个事务。")
    sp.add_argument("--db", required=True, metavar="NAME", help="实例名")
    sp.add_argument("sqls", nargs="+", metavar="SQL")
    sp.add_argument("--confirm", nargs="?", const=True, default=None, metavar="PHRASE")
    sp.add_argument("--confirm-all-rows", action="store_true")
    sp.add_argument("--timeout", type=float, metavar="SEC", default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(group="db", func="exec")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AI 远程运维工具：SSH 命令执行 / 文件传输(哈希双端门禁)，"
                    "Linux/Windows 双适配；数据库安全操作为附加功能(ai-ops db)。",
        epilog=EPILOG_OPS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--hosts", metavar="PATH",
                   help="主机配置文件 (默认: config/hosts.yaml 或环境变量 AIOPS_HOSTS)")
    p.add_argument("--config", metavar="PATH",
                   help="数据库实例配置 (仅 db 子命令使用, 同旧 ai-db)")

    sub = p.add_subparsers(dest="cmd", metavar="<子命令>")

    # ── 远程运维 ──
    sp = sub.add_parser("list-hosts", help="列出已登记主机（密码脱敏）")
    sp.set_defaults(group="ops", func="list_hosts")

    sp = sub.add_parser("exec", help="执行远端命令（原样直达，自动重试连接）",
                        description="命令作为一个整体直达远端 shell：Linux=bash, "
                                    "Windows=cmd/powershell。--file 支持多命令(--- 分隔)，"
                                    "一条失败即停。--raw 只回传 stdout 便于管道。")
    sp.add_argument("host", metavar="HOST", help="主机名 (见 list-hosts)")
    sp.add_argument("command", nargs="?", metavar="COMMAND",
                    help="远端命令(整体一个参数, 建议引号包裹)")
    sp.add_argument("--file", metavar="PATH", help="命令文件(多命令以 --- 行分隔)")
    sp.add_argument("--timeout", type=int, metavar="SEC", default=None,
                    help="单条命令超时秒 (默认取 hosts.yaml defaults.timeout)")
    sp.add_argument("--raw", action="store_true",
                    help="只回传 stdout 原文（便于管道/脚本解析）")
    sp.set_defaults(group="ops", func="exec")

    sp = sub.add_parser("put", help="上传文件（哈希双端门禁）",
                        description="SFTP 上传后自动比对本地与远端哈希，不一致即删除远端"
                                    "半截文件并以 exit 3 拦截。远端父目录自动创建。")
    sp.add_argument("host", metavar="HOST", help="主机名")
    sp.add_argument("local", metavar="LOCAL", help="本地文件路径")
    sp.add_argument("remote", metavar="REMOTE", help="远端目标路径")
    sp.add_argument("--algo", choices=["md5", "sha256"], default=None,
                    help="校验算法 (默认取 hosts.yaml defaults.algo=md5)")
    sp.add_argument("--timeout", type=int, metavar="SEC", default=None)
    sp.set_defaults(group="ops", func="put")

    sp = sub.add_parser("get", help="下载文件（哈希双端门禁）",
                        description="SFTP 下载后自动比对哈希，不一致即删除本地半截文件"
                                    "并以 exit 3 拦截。")
    sp.add_argument("host", metavar="HOST", help="主机名")
    sp.add_argument("remote", metavar="REMOTE", help="远端文件路径")
    sp.add_argument("local", metavar="LOCAL", help="本地保存路径")
    sp.add_argument("--algo", choices=["md5", "sha256"], default=None)
    sp.add_argument("--timeout", type=int, metavar="SEC", default=None)
    sp.set_defaults(group="ops", func="get")

    # ── 数据库（附加功能） ──
    sp = sub.add_parser("db", help="数据库安全操作（原 ai-db-tool 全部功能）",
                        epilog=EPILOG_DB,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    dbsub = sp.add_subparsers(dest="dbcmd", metavar="<db子命令>")
    _add_db_commands(dbsub)

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "group", None):
        parser.print_help()
        return 0

    try:
        if args.group == "ops":
            from .ops.cmds import cmd_exec, cmd_get, cmd_list_hosts, cmd_put
            from .ops.hosts import load_hosts

            hc = load_hosts(args.hosts)
            if args.func == "list_hosts":
                return cmd_list_hosts(hc)
            if args.func == "exec":
                return cmd_exec(hc, args.host, args.command, args.file,
                                args.timeout, args.raw)
            if args.func == "put":
                return cmd_put(hc, args.host, args.local, args.remote,
                               args.algo, args.timeout)
            if args.func == "get":
                return cmd_get(hc, args.host, args.remote, args.local,
                               args.algo, args.timeout)
            parser.error(f"未知 ops 子命令: {args.func}")
            return 2

        # ── 数据库（group == "db"）──
        from .config import load_config

        cfg = load_config(getattr(args, "config", None))
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
    except Exception as e:  # noqa: BLE001 - 不向 agent 暴露裸 traceback
        print(f"[ERROR] 未预期异常: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[中断] 已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
