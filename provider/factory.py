"""数据源工厂和运行期回退管理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .base import NetProvider, NetProviderError, NetSnapshot
from .filtering import InterfaceFilter
from .psutil_provider import PsutilNetProvider

ProviderFactory = Callable[[], NetProvider]


@dataclass(frozen=True)
class ProviderCandidate:
    """一个可探测的数据源候选。"""

    label: str
    factory: ProviderFactory


class ManagedNetProvider(NetProvider):
    """带启动探测和运行期回退的 provider 外壳。"""

    def __init__(
        self,
        candidates: list[ProviderCandidate],
        *,
        failure_threshold: int = 3,
    ) -> None:
        if not candidates:
            raise NetProviderError("没有可用的数据源候选")
        self._candidates = candidates
        self._failure_threshold = max(1, int(failure_threshold))
        self._active_index = -1
        self._active: NetProvider | None = None
        self._failures = 0
        self._generation = 0
        self._activate_first_available()

    @property
    def name(self) -> str:
        if self._active is None:
            return "unavailable"
        return getattr(self._active, "name", self._candidates[self._active_index].label)

    @property
    def generation(self) -> int:
        """每次切换 active provider 后递增，供上层重置基线。"""
        return self._generation

    def _probe(self, index: int) -> tuple[NetProvider, NetSnapshot]:
        candidate = self._candidates[index]
        provider = candidate.factory()
        snap = provider.snapshot()
        return provider, snap

    def _set_active(self, index: int, provider: NetProvider) -> None:
        self._active_index = index
        self._active = provider
        self._failures = 0

    def _activate_first_available(self) -> None:
        errors: list[str] = []
        for index, candidate in enumerate(self._candidates):
            try:
                provider, _ = self._probe(index)
            except NetProviderError as e:
                errors.append(f"{candidate.label}: {e}")
            except Exception as e:
                errors.append(f"{candidate.label}: {e!r}")
            else:
                self._set_active(index, provider)
                return
        raise NetProviderError("无可用网络数据源，已尝试: " + "; ".join(errors))

    def _switch_to_backup(self, cause: NetProviderError) -> NetSnapshot:
        errors = [f"{self.name}: {cause}"]
        for index, candidate in enumerate(self._candidates):
            if index == self._active_index:
                continue
            try:
                provider, snap = self._probe(index)
            except NetProviderError as e:
                errors.append(f"{candidate.label}: {e}")
            except Exception as e:
                errors.append(f"{candidate.label}: {e!r}")
            else:
                self._set_active(index, provider)
                self._generation += 1
                return snap
        raise NetProviderError("数据源连续失败且无可用备用: " + "; ".join(errors))

    def snapshot(self) -> NetSnapshot:
        if self._active is None:
            raise NetProviderError("当前没有 active 数据源")
        try:
            snap = self._active.snapshot()
        except NetProviderError as e:
            self._failures += 1
            if self._failures < self._failure_threshold:
                raise NetProviderError(
                    f"{self.name} 读取失败 "
                    f"({self._failures}/{self._failure_threshold}): {e}"
                ) from e
            return self._switch_to_backup(e)

        self._failures = 0
        return snap


def _build_candidates(interface_filter: InterfaceFilter) -> list[ProviderCandidate]:
    candidates: list[ProviderCandidate] = []

    try:
        from . import linux

        if linux.is_supported():
            candidates.append(
                ProviderCandidate(
                    "linux_proc",
                    lambda: linux.LinuxProcNetProvider(
                        interface_filter=interface_filter
                    ),
                )
            )
    except Exception:
        # 模块级异常留给 psutil 候选兜底；真正错误会在探测结果中体现。
        pass

    candidates.append(
        ProviderCandidate(
            "psutil",
            lambda: PsutilNetProvider(interface_filter=interface_filter),
        )
    )
    return candidates


def select_provider(
    *,
    include_virtual_interfaces: bool = False,
    include_interfaces: list[str] | tuple[str, ...] | str | None = None,
    exclude_interfaces: list[str] | tuple[str, ...] | str | None = None,
    failure_threshold: int = 3,
) -> NetProvider:
    """选择当前平台的网络数据源，并返回带运行期回退的 provider。"""
    interface_filter = InterfaceFilter(
        include_virtual_interfaces=include_virtual_interfaces,
        include_interfaces=include_interfaces,
        exclude_interfaces=exclude_interfaces,
    )
    return ManagedNetProvider(
        _build_candidates(interface_filter),
        failure_threshold=failure_threshold,
    )
