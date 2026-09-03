# ai-db-tool

供 AI agent（Claude Code 等）通过 Bash 安全操作外部数据库的 CLI 工具。
支持 **MySQL** 与 **Kingbase（人大金仓，PG wire 协议）**。

核心设计：**所有 SQL 先经安全守卫（sqlglot AST 级解析）分级，再按级别要求确认**，
危险操作（DROP/TRUNCATE/ALTER..DROP）必须携带精确确认短语才能执行。

## 快速开始

```bash
git clone <本仓库> && cd ai-db-tool
pip install -e .                 # 安装依赖并注册 ai-db 命令

cp config/instances.example.yaml config/instances.yaml
# 编辑 instances.yaml 填入你的数据库连接信息

# 方式一：全局命令
ai-db list-instances

# 方式二：免安装直接运行（需自行 pip install 依赖）
python run.py list-instances

# 方式三
python -m ai_db_tool list-instances
```

依赖：Python ≥3.10，`pymysql`、`psycopg2-binary`、`sqlglot`、`PyYAML`、`rich`。

## 命令一览

| 子命令 | 用途 | 示例 |
|---|---|---|
| `list-instances` | 列出已登记实例（密码脱敏） | `ai-db list-instances` |
| `ping` | 连通性测试 | `ai-db ping --db mysql-test` |
| `list-dbs` | 列服务器上所有库 | `ai-db list-dbs --db mysql-test` |
| `tables` | 列表（mysql 按库 / kingbase 按 schema） | `ai-db tables --db mysql-test` |
| `schema` | 查看表结构（列+索引） | `ai-db schema --db mysql-test 表名` |
| `query` | **只读通道**：SELECT/SHOW/DESC/EXPLAIN | `ai-db query --db mysql-test "SELECT * FROM t LIMIT 10"` |
| `exec` | **写通道**：INSERT/UPDATE/DELETE/DDL | `ai-db exec --db mysql-test "INSERT ..." --confirm` |

通用参数：`query`/`exec` 支持 `--format table|tsv|csv|json`（query）、
`--max-rows N`（默认 200，硬上限 5000）、`--timeout SEC`（默认 30）、
`--dry-run`（只看守卫判定，不连库不执行）。

## 安全分级与确认协议

| 级别 | 语句 | 需要的确认 |
|---|---|---|
| READ | SELECT / SHOW / DESC / EXPLAIN | 无，直接执行 |
| WRITE | INSERT / UPDATE / DELETE / CREATE TABLE / CREATE INDEX / CREATE VIEW / ALTER（无 DROP） / ANALYZE | `--confirm` |
| DANGEROUS | DROP / TRUNCATE / ALTER..DROP / DROP COLUMN / CREATE FUNCTION/PROCEDURE/TRIGGER/EVENT | `--confirm "<对象完整名>"` 精确匹配 |
| 特殊 | UPDATE / DELETE 无顶层 WHERE | 在上述确认之外追加 `--confirm-all-rows` |

对象完整名规则：
- DROP TABLE/VIEW/INDEX、TRUNCATE、ALTER：`<库或schema>.<表名>`，如 `mydb.t1`
  （SQL 未限定时自动补实例默认库/schema；Kingbase 实例的默认是 `mydb` schema）
- DROP DATABASE：`<库名>`

**AI agent 必须遵守的危险操作协议**：
执行 DANGEROUS 语句前，必须先把完整 SQL 展示给用户、说明影响范围，
征得用户**明确同意**后，才携带确认短语执行。确认短语在拦截错误信息中会给出。

被守卫直接禁止（无论怎么确认都不行）的语句：
USE / SET / GRANT / REVOKE / CALL / PREPARE / EXECUTE / COPY / LOAD DATA /
BEGIN..COMMIT（事务由工具管理，用 exec 多参数）/ RENAME TABLE / KILL / FLUSH /
含 `load_file`/`pg_sleep` 等黑名单函数的语句 / SELECT ... INTO OUTFILE / 多语句。

## 事务

`exec` 传多条 SQL 参数时自动作为一个事务：

```bash
ai-db exec --db kingbase-test \
  "INSERT INTO t1(a) VALUES (1)" \
  "UPDATE t2 SET b = 2 WHERE id = 1" \
  --confirm
# 全部成功才 COMMIT；任一失败全部 ROLLBACK 并报错（exit 1）
```

注意：MySQL 的 DDL 会隐式提交（不可回滚，MySQL 服务器行为）；Kingbase 属 PG 系，
DDL 可回滚。

## 退出码

| 码 | 前缀 | 含义 |
|---|---|---|
| 0 | — | 成功 |
| 1 | `[DB]` | 数据库/连接错误 |
| 2 | — | 命令行参数错误 |
| 3 | `[GUARD]` | 被安全守卫拦截（看错误信息中的补救参数） |
| 4 | `[CONFIG]` | 实例未登记或配置非法 |

## 配置实例（config/instances.yaml）

只有登记过的实例才允许连接；新增数据库 = 加一段 YAML，无需改代码。
**从 `config/instances.example.yaml` 复制为 `config/instances.yaml` 后填入真实连接信息**
（`instances.yaml` 已被 .gitignore 忽略，不会误提交）：

```yaml
instances:
  my-mysql:
    dialect: mysql              # 驱动: pymysql
    host: your-mysql-host
    port: 3306
    user: your_user
    password: "your_password"
    database: your_db
  my-kingbase:
    dialect: kingbase           # 驱动: psycopg2 (PG wire 协议)
    host: your-kingbase-host
    port: 54321
    user: your_user
    password: "your_password"
    database: your_db
    schema: your_schema         # 连接期设为 search_path
```

`defaults` 段可调：`max_rows` / `absolute_max_rows` / `statement_timeout_sec` /
`connect_timeout_sec` / `deny_functions`（危险函数黑名单）。

## 项目结构

```
run.py                  根启动器（免安装）
ai_db_tool/
  cli.py                子命令注册与分发（--help 即使用说明）
  guard.py              安全核心：sqlglot AST 分类 / 确认短语 / 拦截
  runner.py             单语句执行流水
  tx.py                 多语句事务
  output.py             table/tsv/csv/json 输出
  config.py             实例注册表加载
  errors.py             异常与退出码
  adapters/
    base.py             适配器接口
    mysql_adapter.py    pymysql 实现
    kingbase_adapter.py psycopg2 实现（search_path / statement_timeout）
  cmds/                 各子命令实现
tests/test_guard.py     guard 离线单测（python -m unittest discover tests）
```

## Kingbase 说明

- Kingbase V8/V9 走 PostgreSQL wire 协议，用 `psycopg2` 连接。
- 若 Kingbase 服务器为 **MySQL 兼容模式**：SQL 按 MySQL 语法书写即可
  （guard 也按 MySQL 方言解析），连接后 `search_path` 自动指向配置的 schema。
- 金仓系统视图（`information_schema.tables` 等）可能返回重复行，工具已去重。

## 测试与验证

```bash
python -m unittest discover tests -v   # guard 离线单测（52 项）
ai-db ping --db <你的实例名>            # 连通性
```
