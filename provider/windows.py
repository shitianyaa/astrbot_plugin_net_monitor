"""Windows 系统级网络计数器数据源。

Windows 上读取网卡累计字节的标准方式是 IP Helper API 的 GetIfTable2。
psutil.net_io_counters(pernic=True) 底层正是调用它，
既保证了内核级数据的正确性，又封装了跨 Windows 版本的结构体差异，
因此本插件在 Windows 上统一走 psutil，不自行 ctypes 解析 MIB_IF_ROW2
（其字段偏移随 SDK 版本变化，脆弱且易错）。
"""

from __future__ import annotations

from .base import NetProvider
from .filtering import InterfaceFilter
from .psutil_provider import PsutilNetProvider


class WindowsPsutilProvider(PsutilNetProvider):
    """基于 psutil.net_io_counters(pernic=True) 的 Windows 数据源。"""

    def __init__(self, interface_filter: InterfaceFilter | None = None) -> None:
        super().__init__(
            interface_filter=interface_filter,
            provider_name="windows_psutil",
        )


def is_supported() -> bool:
    """当前平台是否为 Windows。"""
    import sys

    return sys.platform.startswith("win")


def default_provider(interface_filter: InterfaceFilter | None = None) -> NetProvider:
    """Windows 默认（也是唯一）数据源。"""
    return WindowsPsutilProvider(interface_filter)
