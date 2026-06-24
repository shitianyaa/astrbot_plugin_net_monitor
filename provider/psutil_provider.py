"""通用 psutil 网络计数器数据源。

psutil.net_io_counters(pernic=True) 在各平台下封装对应系统 API：
Windows 下走 IP Helper API，macOS/BSD/Linux 下走各自内核计数器。
"""

from __future__ import annotations

import sys
import time

from .base import NetProvider, NetProviderError, NetSnapshot
from .filtering import InterfaceFilter


class PsutilNetProvider(NetProvider):
    """基于 psutil.net_io_counters(pernic=True) 的跨平台数据源。"""

    def __init__(
        self,
        *,
        interface_filter: InterfaceFilter | None = None,
        provider_name: str | None = None,
    ) -> None:
        try:
            import psutil

            self._psutil = psutil
        except ImportError as e:
            raise NetProviderError("psutil 数据源需要安装 psutil") from e
        self._interface_filter = interface_filter or InterfaceFilter()
        self.name = provider_name or f"psutil_{sys.platform}"

    def snapshot(self) -> NetSnapshot:
        try:
            per_nic = self._psutil.net_io_counters(pernic=True)
        except Exception as e:
            raise NetProviderError(f"psutil 读取网卡失败: {e}") from e

        interfaces = {
            iface: (io.bytes_recv, io.bytes_sent) for iface, io in per_nic.items()
        }
        interfaces = self._interface_filter.apply(interfaces)
        if not interfaces:
            raise NetProviderError("psutil 未返回任何符合过滤规则的网卡")

        return NetSnapshot(timestamp=time.monotonic(), interfaces=interfaces)
