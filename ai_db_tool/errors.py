"""异常与退出码约定。

退出码：
  0  成功
  1  [DB]     数据库错误（连接失败、SQL 语法/执行错误、对象不存在等）
  2  [USAGE]  命令行参数错误（argparse 原生）
  3  [GUARD]  被安全守卫拦截（缺少确认参数、语句被禁止等）
  4  [CONFIG] 配置错误（实例未登记、配置文件非法等）

agent 可凭退出码 + stderr 前缀编程化处理。
"""

EXIT_OK = 0
EXIT_DB = 1
EXIT_USAGE = 2
EXIT_GUARD = 3
EXIT_CONFIG = 4


class AidbError(Exception):
    """基类：带退出码与统一前缀。"""

    exit_code = 1
    prefix = "[ERROR]"

    def __init__(self, message: str, hint: str = ""):
        self.hint = hint
        super().__init__(message)

    def render(self) -> str:
        lines = []
        for line in str(self).splitlines() or [""]:
            lines.append(f"{self.prefix} {line}")
        if self.hint:
            for line in self.hint.splitlines():
                lines.append(f"{self.prefix} 提示: {line}")
        return "\n".join(lines)


class ConfigError(AidbError):
    exit_code = EXIT_CONFIG
    prefix = "[CONFIG]"


class GuardError(AidbError):
    exit_code = EXIT_GUARD
    prefix = "[GUARD]"


class DbError(AidbError):
    exit_code = EXIT_DB
    prefix = "[DB]"
