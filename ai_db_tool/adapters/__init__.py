"""数据库适配器工厂。"""
from __future__ import annotations

from ..errors import ConfigError
from .base import BaseAdapter


def get_adapter(inst: dict) -> BaseAdapter:
    """按实例配置的 dialect 选择适配器。"""
    dialect = str(inst["dialect"]).lower()
    if dialect == "mysql":
        from .mysql_adapter import MysqlAdapter
        return MysqlAdapter(inst)
    if dialect == "kingbase":
        from .kingbase_adapter import KingbaseAdapter
        return KingbaseAdapter(inst)
    raise ConfigError(f"不支持的 dialect: {dialect}")
