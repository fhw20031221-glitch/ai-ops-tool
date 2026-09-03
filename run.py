"""根启动器：python E:/codes/ai-db-tool/run.py <子命令>

免安装直接运行（把项目根目录加入 sys.path 后调用 cli.main）。
也可 pip install -e . 后使用 ai-db 命令，或 python -m ai_db_tool。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_db_tool.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
