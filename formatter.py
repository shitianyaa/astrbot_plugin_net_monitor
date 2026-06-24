"""纯格式化工具：字节/速率/时长转人类可读。

独立成模块是因为这些函数无副作用、易单测，且指令输出与告警文案都要复用。
"""

from __future__ import annotations


def human_bytes(n: int | float) -> str:
    """字节 → KB/MB/GB/TB 字符串，2 位小数。"""
    n = float(n)
    if n < 0:
        n = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def human_speed(bytes_per_sec: float) -> str:
    """速率 → KB/s 或 MB/s，自动单位。"""
    if bytes_per_sec < 0:
        bytes_per_sec = 0
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.2f} KB/s"
    return f"{bytes_per_sec / 1024 / 1024:.2f} MB/s"


def human_duration(seconds: float) -> str:
    """秒 → X天Y时Z分。"""
    seconds = int(seconds)
    if seconds < 0:
        seconds = 0
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}天{hours}时{minutes}分"
    if hours:
        return f"{hours}时{minutes}分"
    if minutes:
        return f"{minutes}分"
    return f"{seconds}秒"


def bar(value: float, maximum: float, width: int = 10) -> str:
    """生成简易进度条：value/maximum 占满 width 格。"""
    if maximum <= 0:
        return "░" * width
    ratio = max(0.0, min(1.0, value / maximum))
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)
