"""网卡过滤规则。

Provider 只负责读取系统计数器；这里集中处理不同平台的接口命名差异，
保证 Linux / Windows / macOS / BSD 使用同一套统计口径。
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Iterable

DEFAULT_EXCLUDE_PATTERNS = (
    "lo*",
    "loopback*",
    "docker*",
    "br-*",
    "veth*",
    "virbr*",
    "tun*",
    "*tun*",
    "tap*",
    "utun*",
    "bridge*",
    "awdl*",
    "vethernet*",
    "*wi-fi direct*",
    "*local area connection[*]*",
    "*本地连接[*]*",
    "*virtualbox*",
    "*vmware*",
    "*hyper-v*",
    "*npcap*",
    "*tap*",
    "*wintun*",
    "*zerotier*",
    "*tailscale*",
)


def _normalize_patterns(raw: Iterable[str] | str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    else:
        parts = raw
    return tuple(str(item).strip().lower() for item in parts if str(item).strip())


class InterfaceFilter:
    """按统一规则决定某个网卡是否纳入统计。"""

    def __init__(
        self,
        *,
        include_virtual_interfaces: bool = False,
        include_interfaces: Iterable[str] | str | None = None,
        exclude_interfaces: Iterable[str] | str | None = None,
    ) -> None:
        self.include_virtual_interfaces = bool(include_virtual_interfaces)
        self.include_patterns = _normalize_patterns(include_interfaces)
        self.exclude_patterns = _normalize_patterns(exclude_interfaces)

    @staticmethod
    def _matches(name: str, patterns: tuple[str, ...]) -> bool:
        lowered = name.lower()
        return any(fnmatchcase(lowered, pattern) for pattern in patterns)

    def allow(self, iface: str) -> bool:
        """返回 iface 是否应纳入系统流量统计。"""
        if self.include_patterns:
            if not self._matches(iface, self.include_patterns):
                return False
        elif not self.include_virtual_interfaces:
            if self._matches(iface, DEFAULT_EXCLUDE_PATTERNS):
                return False

        if self.exclude_patterns and self._matches(iface, self.exclude_patterns):
            return False
        return True

    def apply(
        self, interfaces: dict[str, tuple[int, int]]
    ) -> dict[str, tuple[int, int]]:
        return {name: value for name, value in interfaces.items() if self.allow(name)}
