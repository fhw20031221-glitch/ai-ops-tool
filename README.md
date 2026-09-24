# ai-ops-tool

供 AI agent（Claude Code 等）通过 Bash 执行**远程运维**的 CLI 工具：SSH 命令执行、
文件传输（哈希双端门禁），**Linux 与 Windows 主机双适配**。
数据库安全操作（原 ai-db-tool）作为附加功能保留（`ai-ops db ...`，兼容入口 `ai-db ...`）。

核心设计：

1. **主机白名单制**——只有 `config/hosts.yaml` 登记的主机才允许连接。
2. **传输哈希门禁**——put/get 自动做本地+远端哈希比对，不一致即删除半截文件并以
   exit 3 拦截；"md5 双端校验"从纪律变成工具行为。
3. **命令原样直达**——工具不解析、不包装远端命令内容，本地只有一层 shell 解析，
   多层引号嵌套（`su -c "ksql ... \"SQL\""` 之类）直进直出。
4. **连接自带重试**——链路抖动（VPN / 防火墙类边界设备）导致的 banner 超时自动重试
   （默认 6 次 × 10s，可按主机覆盖），SFTP 假错同样被重试吸收。
5. **OS 适配点收敛**——工具核心与操作系统无关，差异收敛为远端命令模板
   （哈希/建目录），由 hosts.yaml 的 `type: linux|windows` 一个字段切换。
   新增 Linux 服务器只需加一条配置，零代码改动。

## 快速开始

```bash
git clone <本仓库> && cd ai-ops-tool
pip install -e .                 # 安装依赖并注册 ai-ops / ai-db 命令

cp config/hosts.example.yaml config/hosts.yaml
# 编辑 hosts.yaml 填入主机信息（该文件已被 .gitignore 排除，勿入库）

ai-ops list-hosts                # 查看已登记主机
ai-ops exec demo-linux "hostname"   # 远程执行
```

依赖：Python ≥3.10，`paramiko`、`PyYAML`、`rich`（数据库附加功能另需 `pymysql`、
`psycopg2-binary`、`sqlglot`）。

## 远程运维命令一览

| 子命令 | 用途 | 示例 |
|---|---|---|
| `list-hosts` | 列出已登记主机（密码脱敏） | `ai-ops list-hosts` |
| `exec` | 执行远端命令（原样直达 + 连接重试） | `ai-ops exec demo-linux "systemctl is-active nginx"` |
| `exec --file` | 按文件批量执行 | `ai-ops exec demo-linux --file cmds.txt` |
| `put` | 上传 + 哈希双端门禁 | `ai-ops put demo-linux app.war /root/deploy/app.war` |
| `get` | 下载 + 哈希双端门禁 | `ai-ops get demo-linux /var/log/app.log ./app.log` |

通用参数：`--timeout SEC`（单条命令超时，默认取 defaults.timeout=60）、
`--algo md5|sha256`（校验算法，默认 md5）、`--hosts PATH`（主机配置，环境变量
`AIOPS_HOSTS` 同效）。

### exec 细节

- **命令整体直达**：`exec <host> "<cmd>"` 中 `<cmd>` 是一个参数，建议引号包裹；
  Linux 主机走 bash，Windows 主机走其默认 shell（Win32-OpenSSH 常为 cmd，
  管理命令可写 `powershell -Command "..."`）。
- **--file 批量**：文件内容以**独立行 `---`** 分隔为多条命令，一条失败即停（退出码
  取该条）；不含 `---` 时整个文件视为**单条多行命令**（可放 heredoc/复合语句）。
- **--raw**：只回传 stdout 原文，便于管道与脚本解析（仅单条命令）。
- 非_raw 输出格式：`$ <命令>` 头、stdout、`[stderr] ...`、`[exit=N]` 尾。

### Windows 主机实战模式（多层 shell 的坑）

Windows 路径本地 bash → cmd → powershell 三层解析，`$` 变量与引号极易被中间层吃掉。实战结论：

1. **复杂 PowerShell 逻辑一律用 `-EncodedCommand`**（base64 UTF-16LE，零引号零 `$` 冲突）：
   ```bash
   # 本地生成: python -c "import base64;print(base64.b64encode('<命令>'.encode('utf-16-le')).decode())"
   ai-ops exec wku-app "powershell -NoProfile -EncodedCommand <b64>"
   ```
2. **非交互会话没有控制台**：`Test-NetConnection` 的 Write-Progress 会抛 ReadConsoleOutput
   异常——端口探测改用 `.NET TcpClient`（`ConnectAsync(...).Wait(4000)`，失败/超时用 try/catch 区分）。
3. **中文 Windows 的 cmd 默认代码页 936(GBK)**：工具已内置 UTF-8→GBK 自动降级解码，
   中文输出（`ver`、`ping` 统计等）可直接阅读。
4. 探测端口连通性（只读、无依赖）：
   ```powershell
   $c=New-Object Net.Sockets.TcpClient; $c.ConnectAsync('目标IP',端口).Wait(4000); $c.Close()
   ```

### put/get 细节

- 上传前自动创建远端父目录（`mkdir -p` / `New-Item`，按主机 type 适配）。
- 哈希门禁：传输完成后分别计算两端哈希并比对——不一致即删除
  （put 删远端 / get 删本地）半截文件并 exit 3，提示重新执行即可。
- **git-bash(MSYS) 路径污染防护**：MSYS 会把 `/tmp/x` 风格参数自动改写为本地
  Windows 路径。linux 主机 + 远端路径带盘符时工具直接拦截（exit 3）并提示；
  本地路径的 `/e/x` 写法在 `MSYS_NO_PATHCONV=1` 下无法被转换时，工具自动还原
  盘符写法——两种姿势均可正常工作：
  ```bash
  ai-ops put demo-linux /e/proj/dist/a.war /root/deploy/a.war        # MSYS 自动转换本地路径, 但远端路径会被污染 → 工具拦截
  MSYS_NO_PATHCONV=1 ai-ops put demo-linux /e/proj/dist/a.war /root/deploy/a.war  # 推荐: 远端路径保真, 本地路径工具自愈
  ```

### 退出码（与 ai-db 一致，可编程化处理）

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 远程错误（连接失败、远端命令失败、哈希获取失败） |
| 2 | 命令行参数错误 |
| 3 | 被门禁拦截（哈希不一致 / 路径污染） |
| 4 | 配置错误（主机未登记、hosts.yaml 非法） |

## 数据库附加功能（原 ai-db-tool）

```bash
ai-ops db list-instances                          # 等价旧 ai-db list-instances
ai-ops db query --db mysql-test "SELECT 1"        # 只读
ai-ops db exec --db mysql-test "INSERT ..." --confirm   # 写需确认
ai-db query --db mysql-test "SELECT 1"            # 兼容入口(等价 ai-ops db query)
```

安全分级与确认协议不变：只读直接执行；写操作需 `--confirm`；危险操作
（DROP/TRUNCATE/ALTER..DROP）需 `--confirm "<库.表>"` 精确匹配，且必须先把完整
SQL 展示给用户并征得明确同意；UPDATE/DELETE 无 WHERE 另需 `--confirm-all-rows`。
配置文件 `config/instances.yaml`（模板 `instances.example.yaml`，同样不入库）。

## AI agent 使用纪律

1. **只连已登记主机**；需要新增主机时先向用户确认凭据来源。
2. **exec 的远端命令内容**（跑什么、怎么停服、怎么验证）不在本工具职责内——
   遵循各项目部署文档的作业流与禁令（如 AAS 三步重启、cp -i 别名规避）。
3. **put/get 的哈希门禁失败不要绕过**：直接重试即可；连续失败说明链路有问题，
   停下来排查而不是 `--force`（本工具不提供该选项）。
4. 大文件（GB 级）传输优先让用户走 Xftp/SFTP 客户端多线程，工具传输适合
   ≤100MB 的部署件；上传部署包后先 `ai-ops exec <host> "md5sum <路径>"`
   人工复核再进入部署步骤。
5. 密码只存在于本机 `config/hosts.yaml`；不得出现在聊天、命令输出、日志、
   截图或任何入库文件中。
