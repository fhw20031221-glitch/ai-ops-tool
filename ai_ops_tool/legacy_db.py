"""ai-db 兼容入口：`ai-db <子命令>` 等价于 `ai-ops db <子命令>`。

保留旧命令名，使历史文档/脚本/肌肉记忆继续可用。
"""
from __future__ import annotations

from .cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    import sys

    rest = list(sys.argv[1:] if argv is None else argv)
    return cli_main(["db"] + rest)
