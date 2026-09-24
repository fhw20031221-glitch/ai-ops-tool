"""guard.py 离线单元测试（不连数据库）。

运行: python -m unittest discover tests -v   （在项目根目录）
"""
import unittest

from ai_ops_tool.errors import GuardError
from ai_ops_tool import guard


DENY = ["load_file", "benchmark", "sleep", "pg_sleep", "pg_read_file"]


def check(sql, channel="exec", dialect="mysql", default_db="mydb"):
    return guard.check(
        sql, sql_dialect=dialect, deny_functions=DENY,
        default_db=default_db, channel=channel,
    )


class TestParseAndSingle(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(GuardError):
            check("")

    def test_multiple_statements(self):
        with self.assertRaises(GuardError) as cm:
            check("SELECT 1; SELECT 2")
        self.assertIn("单条语句", str(cm.exception))

    def test_trailing_semicolon_ok(self):
        d = check("SELECT 1;")
        self.assertEqual(d.category, guard.READ)

    def test_unparseable(self):
        with self.assertRaises(GuardError):
            check("SELECT FROM WHERE")

    def test_unknown_command(self):
        with self.assertRaises(GuardError) as cm:
            check("KILL 123")
        self.assertEqual(cm.exception.exit_code, 3)


class TestRead(unittest.TestCase):
    def test_select(self):
        d = check("SELECT * FROM t1 WHERE id = 1")
        self.assertEqual(d.category, guard.READ)
        self.assertFalse(d.needs_confirm)
        self.assertIn("mydb.t1", d.targets)

    def test_show(self):
        for sql in ("SHOW DATABASES", "SHOW TABLES", "SHOW CREATE TABLE t1"):
            self.assertEqual(check(sql).category, guard.READ, sql)

    def test_desc(self):
        self.assertEqual(check("DESC t1").category, guard.READ)
        self.assertEqual(check("DESCRIBE t1").category, guard.READ)

    def test_explain(self):
        self.assertEqual(check("EXPLAIN SELECT * FROM t1").category, guard.READ)

    def test_union(self):
        d = check("SELECT id FROM a UNION SELECT id FROM b")
        self.assertEqual(d.category, guard.READ)

    def test_select_into_outfile_blocked(self):
        with self.assertRaises(GuardError) as cm:
            check("SELECT * FROM t1 INTO OUTFILE '/tmp/x'")
        self.assertIn("OUTFILE", str(cm.exception))

    def test_for_update_escalates(self):
        d = check("SELECT * FROM t1 WHERE id = 1 FOR UPDATE")
        self.assertEqual(d.category, guard.WRITE)

    def test_deny_function_in_select(self):
        with self.assertRaises(GuardError) as cm:
            check("SELECT load_file('/etc/passwd')")
        self.assertIn("函数被禁止", str(cm.exception))

    def test_deny_sleep(self):
        with self.assertRaises(GuardError):
            check("SELECT sleep(5)")

    def test_where_in_string_not_treated_as_clause(self):
        # 字符串常量里的 WHERE 不影响（AST 判定顶层 where）
        d = check("UPDATE t1 SET name = 'no where clause' WHERE id = 1")
        self.assertFalse(d.needs_all_rows)


class TestQueryChannel(unittest.TestCase):
    def test_query_channel_rejects_write(self):
        with self.assertRaises(GuardError) as cm:
            check("DELETE FROM t1", channel="query")
        self.assertIn("只读通道", str(cm.exception))

    def test_query_channel_rejects_ddl(self):
        with self.assertRaises(GuardError):
            check("DROP TABLE t1", channel="query")


class TestWrite(unittest.TestCase):
    def test_insert(self):
        d = check("INSERT INTO t1 (a) VALUES (1)")
        self.assertEqual(d.category, guard.WRITE)
        self.assertTrue(d.needs_confirm)
        self.assertEqual(d.confirm_phrase, "")

    def test_insert_backtick_qualified(self):
        d = check("INSERT INTO `mydb`.`t 1` (a) VALUES (1)")
        self.assertIn("mydb.t 1", d.targets)

    def test_update_with_where(self):
        d = check("UPDATE t1 SET a = 1 WHERE id = 2")
        self.assertEqual(d.category, guard.WRITE)
        self.assertFalse(d.needs_all_rows)

    def test_update_without_where(self):
        d = check("UPDATE t1 SET a = 1")
        self.assertTrue(d.needs_all_rows)

    def test_delete_without_where(self):
        d = check("DELETE FROM t1")
        self.assertTrue(d.needs_all_rows)

    def test_delete_with_where(self):
        d = check("DELETE FROM t1 WHERE id = 1")
        self.assertFalse(d.needs_all_rows)

    def test_delete_join_with_where(self):
        d = check("DELETE t1 FROM t1 JOIN t2 ON t1.id = t2.id WHERE t2.x = 1")
        self.assertFalse(d.needs_all_rows)

    def test_update_subquery_where_counts(self):
        d = check("UPDATE t1 SET a = 1 WHERE id IN (SELECT id FROM t2)")
        self.assertFalse(d.needs_all_rows)

    def test_create_table(self):
        d = check("CREATE TABLE t2 (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(50))")
        self.assertEqual(d.category, guard.WRITE)
        self.assertTrue(d.needs_confirm)

    def test_create_index(self):
        d = check("CREATE INDEX idx_name ON t1 (name)")
        self.assertEqual(d.category, guard.WRITE)

    def test_create_view(self):
        d = check("CREATE VIEW v1 AS SELECT * FROM t1")
        self.assertEqual(d.category, guard.WRITE)

    def test_alter_add(self):
        d = check("ALTER TABLE t1 ADD COLUMN c INT")
        self.assertEqual(d.category, guard.WRITE)

    def test_alter_modify(self):
        d = check("ALTER TABLE t1 MODIFY COLUMN c BIGINT")
        self.assertEqual(d.category, guard.WRITE)


class TestDangerous(unittest.TestCase):
    def test_drop_table_default_db(self):
        d = check("DROP TABLE _aidb_smoke")
        self.assertEqual(d.category, guard.DANGEROUS)
        self.assertEqual(d.confirm_phrase, "mydb._aidb_smoke")

    def test_drop_table_qualified(self):
        d = check("DROP TABLE otherdb._aidb_smoke")
        self.assertEqual(d.confirm_phrase, "otherdb._aidb_smoke")

    def test_drop_database(self):
        d = check("DROP DATABASE junkdb")
        self.assertEqual(d.category, guard.DANGEROUS)
        self.assertEqual(d.confirm_phrase, "junkdb")

    def test_truncate(self):
        d = check("TRUNCATE TABLE t1")
        self.assertEqual(d.category, guard.DANGEROUS)
        self.assertEqual(d.confirm_phrase, "mydb.t1")

    def test_alter_drop_column(self):
        d = check("ALTER TABLE t1 DROP COLUMN c")
        self.assertEqual(d.category, guard.DANGEROUS)
        self.assertEqual(d.confirm_phrase, "mydb.t1")

    def test_rename_table(self):
        # sqlglot 将 RENAME TABLE 解析为 Command → 按黑名单拦截（比 DANGEROUS 更严格）
        with self.assertRaises(GuardError) as cm:
            check("RENAME TABLE a TO b")
        self.assertIn("语句被禁止", str(cm.exception))

    def test_create_function(self):
        d = check("CREATE FUNCTION f1() RETURNS INT RETURN 1")
        self.assertEqual(d.category, guard.DANGEROUS)


class TestBlacklist(unittest.TestCase):
    def test_use(self):
        with self.assertRaises(GuardError):
            check("USE otherdb")

    def test_set(self):
        with self.assertRaises(GuardError):
            check("SET GLOBAL max_connections = 1000")

    def test_grant(self):
        with self.assertRaises(GuardError):
            check("GRANT ALL ON *.* TO 'x'@'%'")

    def test_call(self):
        with self.assertRaises(GuardError):
            check("CALL p1()")

    def test_begin(self):
        with self.assertRaises(GuardError):
            check("BEGIN")

    def test_load_data(self):
        with self.assertRaises(GuardError):
            check("LOAD DATA INFILE '/tmp/x' INTO TABLE t1")


class TestVerifyConfirm(unittest.TestCase):
    def test_write_ok_with_flag(self):
        d = check("INSERT INTO t1 (a) VALUES (1)")
        guard.verify_confirm(d, confirm_value=None, confirm_flag=True)

    def test_write_missing(self):
        d = check("INSERT INTO t1 (a) VALUES (1)")
        with self.assertRaises(GuardError) as cm:
            guard.verify_confirm(d, confirm_value=None)
        self.assertIn("--confirm", str(cm.exception))

    def test_dangerous_missing(self):
        d = check("DROP TABLE t1")
        with self.assertRaises(GuardError) as cm:
            guard.verify_confirm(d, confirm_value=None)
        self.assertIn('mydb.t1', str(cm.exception))
        self.assertIn("用户", str(cm.exception))

    def test_dangerous_wrong_phrase(self):
        d = check("DROP TABLE t1")
        with self.assertRaises(GuardError):
            guard.verify_confirm(d, confirm_value="wrong")

    def test_dangerous_case_insensitive_match(self):
        d = check("DROP TABLE T1")
        guard.verify_confirm(d, confirm_value="MYDB.t1")

    def test_all_rows_missing(self):
        d = check("DELETE FROM t1")
        with self.assertRaises(GuardError) as cm:
            guard.verify_confirm(d, confirm_value=None, confirm_flag=True)
        self.assertIn("--confirm-all-rows", str(cm.exception))

    def test_all_rows_ok(self):
        d = check("DELETE FROM t1")
        guard.verify_confirm(d, confirm_value=None, confirm_flag=True, all_rows=True)


class TestKingbaseDialect(unittest.TestCase):
    """kingbase 实例也按 mysql 方言解析（服务器为 MySQL 兼容模式）。"""

    def test_select(self):
        d = check("SELECT * FROM t1 LIMIT 10", default_db="mydb")
        self.assertEqual(d.category, guard.READ)

    def test_drop(self):
        d = check("DROP TABLE t1", default_db="mydb")
        self.assertEqual(d.confirm_phrase, "mydb.t1")


if __name__ == "__main__":
    unittest.main()
