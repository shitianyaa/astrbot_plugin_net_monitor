"""跨平台网络流量数据源抽象层。

每个 Provider 只负责一件事：在某个平台上读取系统级网络计数器，
返回各网卡的累计字节数。差值累计、速率平滑、持久化都不在 Provider 职责内，
所以更换数据源（/proc/net/dev ↔ WMI ↔ psutil）不会影响上层统计逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class NetSnapshot:
    """某一时刻所有受监控网卡的累计字节数快照。

    bytes_recv / bytes_sent 为系统启动以来的单调递增累计值，
    上层通过两次快照做差值得到周期内的真实流量。
    timestamp 使用 time.monotonic() 的秒数，避免系统时间调整影响速率。
    """

    timestamp: float
    # {iface_name: (recv_bytes, sent_bytes)}
    interfaces: dict[str, tuple[int, int]]


class NetProviderError(RuntimeError):
    """Provider 读取失败时抛出，上层应回退到备用数据源。"""


class NetProvider(ABC):
    """网络流量数据源统一接口。"""

    #: 数据源名称，用于日志与诊断
    name: str = "abstract"

    @abstractmethod
    def snapshot(self) -> NetSnapshot:
        """读取当前各网卡累计字节数快照。

        返回的累计值必须是单调递增的（系统重启后会归零），
        上层会处理回绕与溢出。

        Raises:
            NetProviderError: 读取失败时抛出。
        """
        raise NotImplementedError
