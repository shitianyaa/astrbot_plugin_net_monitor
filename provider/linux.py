"""Linux 系统级网络计数器数据源。

直接读取内核导出的 /proc/net/dev，无需 psutil、无需 root。
这是 Linux 上最稳定、开销最低的网卡字节数来源，且不受进程重启影响。
"""

from __future__ import annotations

import os
import time

from .base import NetProvider, NetProviderError, NetSnapshot
from .filtering import InterfaceFilter


class LinuxProcNetProvider(NetProvider):
    """读取 /proc/net/dev 的数据源。"""

    name = "linux_proc"

    def __init__(
        self,
        proc_path: str = "/proc/net/dev",
        *,
        interface_filter: InterfaceFilter | None = None,
    ) -> None:
        self._path = proc_path
        self._interface_filter = interface_filter or InterfaceFilter()

    def snapshot(self) -> NetSnapshot:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:  # 文件不存在/无权限
            raise NetProviderError(f"读取 {self._path} 失败: {e}") from e

        interfaces: dict[str, tuple[int, int]] = {}
        # /proc/net/dev 前两行是表头，从第三行起每行一个网卡
        for line in lines[2:]:
            if ":" not in line:
                continue
            name_part, stats_part = line.split(":", 1)
            iface = name_part.strip()
            stats = stats_part.split()
            if len(stats) < 16:
                continue
            try:
                # /proc/net/dev 字段顺序: recv(bytes packets errs ...) ... send(bytes packets errs ...)
                recv = int(stats[0])
                sent = int(stats[8])
            except (ValueError, IndexError) as e:
                raise NetProviderError(f"解析网卡 {iface} 统计失败: {e}") from e
            interfaces[iface] = (recv, sent)

        interfaces = self._interface_filter.apply(interfaces)
        if not interfaces:
            raise NetProviderError("未从 /proc/net/dev 解析到任何符合过滤规则的网卡")

        return NetSnapshot(timestamp=time.monotonic(), interfaces=interfaces)


def is_supported() -> bool:
    """当前平台是否可用 /proc/net/dev 数据源。"""
    return os.name == "posix" and os.path.exists("/proc/net/dev")
