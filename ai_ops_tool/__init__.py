"""ai-ops-tool：供 AI agent 通过 Bash 安全执行远程运维的 CLI 工具。

主功能：SSH 远程命令执行 / 文件传输（哈希双端门禁），Linux 与 Windows 主机双适配，
主机白名单制（config/hosts.yaml）。
附加功能：数据库安全操作（原 ai-db-tool，命令 ai-ops db ...，兼容入口 ai-db ...）。
"""

__version__ = "2.0.0"
