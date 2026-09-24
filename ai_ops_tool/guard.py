"""安全守卫：SQL 语句分类与确认协议。

设计原则：
  1. 默认拒绝——无法解析、未知语句类型一律拦截。
  2. sqlglot 只做分析分类，执行永远使用原始 SQL 字符串（不做 parse→generate 往返）。
  3. 三级分类：
       READ      只读，直接执行（SELECT/SHOW/DESC/EXPLAIN）
       WRITE     写操作，需 --confirm（INSERT/UPDATE/DELETE/CREATE TABLE 等）
       DANGEROUS 危险操作，需 --confirm "<对象完整名>" 精确匹配
                 （DROP/TRUNCATE/ALTER..DROP/RENAME/CREATE FUNCTION 等）
  4. UPDATE/DELETE 无顶层 WHERE → 追加 --confirm-all-rows。
  5. 语句黑名单（USE/SET/GRANT/CALL/PREPARE/COPY/LOAD DATA 等绕过通道）一律拦截。

本模块为纯函数集合，不 import 任何数据库驱动，可离线单元测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .errors import GuardError

READ = "READ"
WRITE = "WRITE"
DANGEROUS = "DANGEROUS"

# ── 确认协议提示（写进错误信息，agent 的"使用说明书"）────────────
USER_CONSENT_PROTOCOL = (
    "危险操作协议: 必须先把完整 SQL 展示给用户、说明影响, 并征得用户明确同意后, "
    "才允许执行本语句。"
)

_INTO_FILE_RE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)
_FOR_UPDATE_RE = re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE)


@dataclass
class GuardDecision:
    """guard 对单条语句的判定。"""

    sql: str
    category: str                      # READ / WRITE / DANGEROUS
    kind: str                          # 语句类型描述, 如 "DROP TABLE"
    targets: list[str] = field(default_factory=list)   # 涉及对象全名
    confirm_phrase: str = ""           # DANGEROUS: 要求的确认短语
    needs_all_rows: bool = False       # UPDATE/DELETE 无 WHERE
    notes: list[str] = field(default_factory=list)

    # ── 确认需求判定 ──
    @property
    def needs_confirm(self) -> bool:
        return self.category in (WRITE, DANGEROUS)

    def describe(self) -> str:
        lines = [f"语句类别: {self.category} ({self.kind})"]
        if self.targets:
            lines.append(f"目标对象: {', '.join(self.targets)}")
        if self.category == READ:
            lines.append("只读操作, 无需确认, 直接执行")
        elif self.category == WRITE:
            need = ["--confirm"]
            if self.needs_all_rows:
                need.append("--confirm-all-rows")
            lines.append(f"写操作, 需要: {' '.join(need)}")
        else:
            need = [f'--confirm "{self.confirm_phrase}"']
            if self.needs_all_rows:
                need.append("--confirm-all-rows")
            lines.append(f"危险操作, 需要: {' '.join(need)}")
        for n in self.notes:
            lines.append(f"备注: {n}")
        return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# 入口
# ────────────────────────────────────────────────────────────────

def check(
    sql: str,
    *,
    sql_dialect: str = "mysql",
    deny_functions: list[str] | None = None,
    default_db: str = "",
    channel: str = "exec",
) -> GuardDecision:
    """解析并分类一条 SQL。

    channel="query" 时只放行 READ（读通道永不写）。
    任何违规直接抛 GuardError（含补救参数说明）。
    """
    sql = sql.strip()
    if not sql:
        raise GuardError("SQL 为空")

    deny = {f.lower() for f in (deny_functions or [])}

    statements = _parse(sql, sql_dialect)
    if len(statements) != 1:
        raise GuardError(
            f"仅允许单条语句（解析到 {len(statements)} 条）。"
            "多条写操作请作为 exec 的多个参数传入, 会自动纳入一个事务。",
        )

    stmt = statements[0]
    decision = _classify(stmt, sql, deny, default_db)

    # 通道隔离：query 只读
    if channel == "query" and decision.category != READ:
        raise GuardError(
            f"query 是只读通道, 禁止执行{decision.category}操作（{decision.kind}）。",
            hint=f'请改用: exec --db <实例> "{sql}" --confirm',
        )
    return decision


def verify_confirm(
    decision: GuardDecision,
    *,
    confirm_value: str | None,
    confirm_flag: bool = False,
    all_rows: bool = False,
) -> None:
    """校验调用方提供的确认参数是否满足判定要求。不满足抛 GuardError。"""
    # 1. 基本确认
    if decision.needs_confirm:
        if decision.category == DANGEROUS:
            expected = decision.confirm_phrase
            got = (confirm_value or "").strip()
            if not got:
                raise GuardError(
                    f'危险操作({decision.kind}) 需要 --confirm "{expected}"。\n'
                    f"{USER_CONSENT_PROTOCOL}\n"
                    f'用户明确同意后, 追加参数重新执行: --confirm "{expected}"'
                )
            if got.lower() != expected.lower():
                raise GuardError(
                    f'确认短语不匹配: 期望 "{expected}", 实际 "{got}"。\n'
                    f'请原样使用: --confirm "{expected}"'
                )
        else:  # WRITE
            if not (confirm_flag or confirm_value):
                target = ", ".join(decision.targets) or "（见 SQL）"
                raise GuardError(
                    f"写操作({decision.kind})需要确认。目标: {target}\n"
                    f"确认无误后追加参数重新执行: --confirm"
                )
    # 2. 全表更新确认
    if decision.needs_all_rows and not all_rows:
        raise GuardError(
            f"{decision.kind} 没有顶层 WHERE 条件, 将影响目标表全部分行!\n"
            f"确认要影响全表时, 在所需确认参数之外追加: --confirm-all-rows"
        )


# ────────────────────────────────────────────────────────────────
# 解析与分类
# ────────────────────────────────────────────────────────────────

def _parse(sql: str, dialect: str) -> list[exp.Expression]:
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        if _INTO_FILE_RE.search(sql):
            raise GuardError(
                "SELECT ... INTO OUTFILE/DUMPFILE 涉及服务器文件写入, 一律禁止。",
            )
        raise GuardError(
            f"SQL 解析失败（默认拒绝）: {e}",
            hint="请检查语法；本工具用 sqlglot 做安全分类, 无法解析的语句不会执行。",
        )
    return stmts


def _classify(stmt: exp.Expression, sql: str, deny: set[str], default_db: str) -> GuardDecision:
    root = type(stmt)

    # ---- READ ----（sqlglot 30.x: EXPLAIN 也解析为 Describe）
    if root in (exp.Select, exp.Union, exp.Subquery, exp.Show, exp.Describe):
        decision = GuardDecision(sql=sql, category=READ, kind=_kind_name(stmt))
        # SELECT 危险特征
        if root in (exp.Select, exp.Union):
            if _INTO_FILE_RE.search(sql):
                raise GuardError(
                    "SELECT ... INTO OUTFILE/DUMPFILE 涉及服务器文件写入, 一律禁止。",
                )
            if _FOR_UPDATE_RE.search(sql):
                decision.category = WRITE
                decision.kind += " (FOR UPDATE 取锁, 按写操作对待)"
                decision.notes.append("FOR UPDATE 会加锁, 升级为写操作")
        decision.targets = _target_tables(stmt, default_db)
        _check_deny_functions(stmt, deny)
        return decision

    # ---- WRITE: DML ----
    if root in (exp.Insert, exp.Update, exp.Delete, exp.Merge):
        decision = GuardDecision(sql=sql, category=WRITE, kind=_kind_name(stmt))
        decision.targets = _target_tables(stmt, default_db)
        if root in (exp.Update, exp.Delete) and stmt.args.get("where") is None:
            decision.needs_all_rows = True
            decision.notes.append("顶层没有 WHERE 条件, 影响全表")
        _check_deny_functions(stmt, deny)
        return decision

    if root is exp.Analyze:
        return GuardDecision(sql=sql, category=WRITE, kind="ANALYZE",
                             targets=_target_tables(stmt, default_db))

    # ---- WRITE: CREATE（良性对象）----
    if root is exp.Create:
        return _classify_create(stmt, sql, default_db, deny)

    # ---- ALTER ----
    if root is exp.Alter:
        return _classify_alter(stmt, sql, default_db)

    # ---- DANGEROUS ----
    if root is exp.Drop:
        return _classify_drop(stmt, sql, default_db)
    if root is exp.TruncateTable:
        # sqlglot 30.x: 目标表在 expressions 列表
        targets = [_table_phrase(t, default_db) for t in (stmt.args.get("expressions") or [])]
        targets = [t for t in targets if t]
        return GuardDecision(
            sql=sql, category=DANGEROUS, kind="TRUNCATE TABLE",
            targets=targets,
            confirm_phrase=targets[0] if targets else "(无法确定目标, 拒绝执行)",
        )
    # ---- 黑名单 / 未知 ----（按类名匹配, 免受 sqlglot 版本节点增减影响）
    banned_reasons = {
        "Use": "USE（请用子命令的 --database/--schema 参数切换目标库）",
        "Set": "SET（会话参数修改, 绕过守卫的通道）",
        "Grant": "GRANT（权限变更）",
        "Revoke": "REVOKE（权限变更）",
        "Call": "CALL（存储过程可执行任意逻辑）",
        "Prepare": "PREPARE（预编译绕过守卫）",
        "Execute": "EXECUTE（预编译绕过守卫）",
        "Copy": "COPY（文件读写）",
        "LoadData": "LOAD DATA（文件读取）",
        "Lock": "LOCK（锁表）",
        "Transaction": "BEGIN/START TRANSACTION（事务由工具管理, 请用 exec 多参数）",
        "Commit": "COMMIT（事务由工具管理）",
        "Rollback": "ROLLBACK（事务由工具管理）",
        "Command": "未知/不支持的命令（RENAME TABLE/KILL/SHUTDOWN/FLUSH 等, 默认拒绝）",
        "Flush": "FLUSH（默认拒绝）",
        "Kill": "KILL（默认拒绝）",
    }
    reason = banned_reasons.get(root.__name__)
    if reason:
        raise GuardError(f"语句被禁止: {reason}")

    raise GuardError(
        f"未知语句类型（默认拒绝）: {root.__name__}",
        hint="如确为安全操作请检查语法; 支持的语句见 README。",
    )


def _classify_create(stmt: exp.Create, sql: str, default_db: str, deny: set[str]) -> GuardDecision:
    kind = str(stmt.args.get("kind") or "").upper()
    dangerous_kinds = {"FUNCTION", "PROCEDURE", "TRIGGER", "EVENT", "EXTENSION", "RULE"}
    targets = _target_tables(stmt, default_db)
    _check_deny_functions(stmt, deny)
    if kind in dangerous_kinds:
        return GuardDecision(
            sql=sql, category=DANGEROUS, kind=f"CREATE {kind}",
            targets=targets,
            confirm_phrase=_first_identifier(stmt, default_db),
        )
    return GuardDecision(sql=sql, category=WRITE, kind=f"CREATE {kind or 'OBJECT'}",
                         targets=targets)


def _classify_alter(stmt: exp.Alter, sql: str, default_db: str) -> GuardDecision:
    targets = _target_tables(stmt, default_db)
    phrase = _table_phrase(stmt.args.get("this"), default_db)
    actions = stmt.args.get("actions") or []
    # 子句级危险判定: DROP / RENAME（含 sqlglot 30.x 的 AlterRename 等变体）
    action_names = [type(a).__name__.lower() for a in actions]
    has_drop_or_rename = any(
        "drop" in n or "rename" in n for n in action_names
    )
    if has_drop_or_rename:
        return GuardDecision(
            sql=sql, category=DANGEROUS, kind="ALTER (含 DROP/RENAME 子句)",
            targets=targets, confirm_phrase=phrase,
        )
    return GuardDecision(sql=sql, category=WRITE, kind="ALTER", targets=targets)


def _classify_drop(stmt: exp.Drop, sql: str, default_db: str) -> GuardDecision:
    kind = str(stmt.args.get("kind") or "").upper()
    node = stmt.args.get("this")
    if kind in ("DATABASE", "SCHEMA"):
        phrase = _bare_name(node) or "(未知库)"
        targets = [phrase]
    else:
        phrase = _table_phrase(node, default_db)
        targets = [phrase] if phrase else []
    return GuardDecision(
        sql=sql, category=DANGEROUS, kind=f"DROP {kind or 'OBJECT'}",
        targets=targets, confirm_phrase=phrase or "(无法确定目标, 拒绝执行)",
    )


# ────────────────────────────────────────────────────────────────
# 对象名提取
# ────────────────────────────────────────────────────────────────

def _norm_identifier(x) -> str:
    """从 Identifier/Table 等节点取不带引号的名称。"""
    if x is None:
        return ""
    if isinstance(x, exp.Identifier):
        return x.name
    if isinstance(x, exp.Table):
        return x.name
    if hasattr(x, "name"):
        return x.name
    return str(x)


def _table_phrase(node, default_db: str) -> str:
    """构造 表/对象 的限定全名 `db.table`；db 缺省时补默认库。"""
    if node is None:
        return ""
    # Create 的 this 可能是 Schema(this=Table); CREATE FUNCTION 则是 UserDefinedFunction
    if isinstance(node, (exp.Schema, exp.UserDefinedFunction)):
        node = node.this
    if isinstance(node, exp.Table):
        db = node.db or default_db
        name = _norm_identifier(node)
        return f"{db}.{name}" if db else name
    if isinstance(node, exp.Column):
        tbl = node.table
        return f"{default_db}.{tbl}" if (default_db and tbl) else tbl
    name = _norm_identifier(node)
    if not name:
        return ""
    return f"{default_db}.{name}" if default_db else name


def _bare_name(node) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Schema):
        node = node.this
    return _norm_identifier(node)


def _first_identifier(stmt: exp.Expression, default_db: str) -> str:
    """取语句主对象名（用于 CREATE FUNCTION 等的确认短语）。"""
    node = stmt.args.get("this")
    return _table_phrase(node, default_db) or "(未知对象)"


def _target_tables(stmt: exp.Expression, default_db: str, limit: int = 8) -> list[str]:
    """列出语句直接涉及的表全名（供展示; 上限 limit 个）。"""
    seen: list[str] = []
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, exp.Table):
            phrase = _table_phrase(node, default_db)
            if phrase and phrase not in seen:
                seen.append(phrase)
        if len(seen) >= limit:
            break
    return seen


def _check_deny_functions(stmt: exp.Expression, deny: set[str]) -> None:
    if not deny:
        return
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, exp.Anonymous):
            fn = str(node.this or "").lower()
            if fn in deny:
                raise GuardError(
                    f"函数被禁止: {fn.upper()}（危险函数黑名单）。",
                    hint="如需调整黑名单, 修改 config/instances.yaml 的 deny_functions。",
                )


def _kind_name(stmt: exp.Expression) -> str:
    name = type(stmt).__name__.upper()
    alias = {"TRUNCATETABLE": "TRUNCATE TABLE"}
    return alias.get(name, name)
