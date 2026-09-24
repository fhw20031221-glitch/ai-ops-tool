"""结果输出：rich 表格 / tsv / csv / json。"""
from __future__ import annotations

import csv
import io
import json

from rich.console import Console
from rich.table import Table as RichTable


def _stringify(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"0x{value.hex()[:64]}{'…' if len(value) > 32 else ''}"
    return str(value)


def print_result(
    columns: list[str] | None,
    rows: list[tuple],
    *,
    fmt: str = "table",
    truncated: bool = False,
    elapsed_ms: float | None = None,
) -> None:
    """打印结果集。fmt: table | tsv | csv | json"""
    if fmt == "json":
        payload = {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
        _print_footer(truncated, elapsed_ms)
        return

    if fmt == "tsv":
        if columns:
            print("\t".join(columns))
        for r in rows:
            print("\t".join(_stringify(v) for v in r))
        _print_footer(truncated, elapsed_ms)
        return

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        if columns:
            writer.writerow(columns)
        for r in rows:
            writer.writerow([_stringify(v) for v in r])
        print(buf.getvalue(), end="")
        _print_footer(truncated, elapsed_ms)
        return

    # 默认 rich 表格
    console = Console()
    if columns is None:
        console.print("[dim]（无结果集）[/dim]")
    elif not rows:
        console.print(f"[dim]0 行（列: {', '.join(columns)}）[/dim]")
    else:
        table = RichTable(show_lines=False, header_style="bold cyan")
        for c in columns:
            table.add_column(str(c), overflow="fold", max_width=60)
        for r in rows[:200]:  # rich 渲染上限保护（数据已在上游截断）
            table.add_row(*[_stringify(v) for v in r])
        console.print(table)
    _print_footer(truncated, elapsed_ms)


def _print_footer(truncated: bool, elapsed_ms: float | None) -> None:
    console = Console()
    if truncated:
        console.print(
            "[yellow]⚠ 结果已截断（超过 --max-rows 上限）。"
            "如需更多行: 加 --max-rows N, 或给 SQL 自行加 LIMIT/OFFSET。[/yellow]"
        )
    if elapsed_ms is not None:
        console.print(f"[dim]{elapsed_ms:.0f} ms[/dim]")


def print_affected(rowcount: int, elapsed_ms: float | None = None, note: str = "") -> None:
    """exec 写操作的结果提示。"""
    console = Console()
    parts = [f"[green]受影响行数: {rowcount}[/green]"]
    if note:
        parts.append(note)
    if elapsed_ms is not None:
        parts.append(f"[dim]{elapsed_ms:.0f} ms[/dim]")
    console.print("  ".join(parts))
