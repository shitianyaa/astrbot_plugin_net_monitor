"""流量核心监控逻辑（纯实时 demo 版）。

职责：周期性读取数据源快照 → 算差值 → 维护本次启动累计 → 算近 N 秒滑动窗口平均速率。
不做持久化、不做历史：插件重载/系统重启即清零，只为演示实时速率。

设计要点：
- 速率展示用「滑动窗口平均」：保留最近 window_seconds 秒的采样点，用
  (当前累计 - 窗口起点累计) / 时间跨度 算平均，比单次瞬时值稳，语义也更直观
  （有明确的「最近 5 秒」语义）。
- 系统重启后网卡计数器归零 → 用 max(delta, 0) 抑制负数（session 累计保持单调）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .provider import NetProvider, NetProviderError, NetSnapshot

#: 默认滑动窗口长度（秒）。
DEFAULT_WINDOW_SECONDS = 5.0


@dataclass
class TrafficStats:
    """单次采集得到的统计结果。"""

    # 本周期瞬时速率（字节/秒）
    up_speed_bps: float = 0.0
    down_speed_bps: float = 0.0
    # 本周期新增流量（字节），供外层持久化月累计使用
    up_delta_bytes: int = 0
    down_delta_bytes: int = 0
    # 滑动窗口平均速率（字节/秒），展示用主值
    up_speed_avg_bps: float = 0.0
    down_speed_avg_bps: float = 0.0
    # 滑动窗口实际覆盖时长（秒），便于展示「近 X 秒平均」
    window_span_seconds: float = 0.0
    # 本进程运行期内的累计字节（仅内存，重启清零）
    session_up_bytes: int = 0
    session_down_bytes: int = 0
    # 系统网卡计数器绝对值（开机以来累计，与插件是否运行无关）
    system_up_bytes: int = 0
    system_down_bytes: int = 0
    # 受监控的网卡数量
    iface_count: int = 0
    # 数据源名称
    provider: str = "unknown"
    # 本次采集是否出错（出错时 stats 保留上次值，仅带 error 标记）
    error: str | None = None


class NetMonitor:
    """网络流量监控核心。

    线程模型：非线程安全，应在单条 asyncio 事件循环线程中调用 sample()。
    """

    def __init__(
        self,
        provider: NetProvider,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_samples: int = 512,
    ) -> None:
        self._provider = provider
        # 滑动窗口长度（秒），至少 1 秒，否则退化为瞬时值
        self._window_seconds = max(1.0, float(window_seconds))

        self._last: NetSnapshot | None = None
        self._provider_generation = self._current_provider_generation()
        self._session_up = 0
        self._session_down = 0
        # 上一次成功采样的「开机以来」总量，出错时复用，避免 error 分支重新算偏
        self._last_system_up = 0
        self._last_system_down = 0
        # 滑动窗口：(monotonic_ts, session_up_bytes, session_down_bytes)
        # 用 monotonic 时间算跨度，免受系统时钟跳变影响。
        self._window: deque[tuple[float, int, int]] = deque(maxlen=max_samples)

    @property
    def provider(self) -> NetProvider:
        """底层数据源（展示与诊断用）。"""
        return self._provider

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", "unknown")

    def _current_provider_generation(self) -> int:
        return int(getattr(self._provider, "generation", 0))

    def _push_window(self, ts: float, up_bytes: int, down_bytes: int) -> None:
        """记录一个窗口采样点并按时间裁剪到 window_seconds。

        保留策略：丢掉所有早于「现在 - 窗口长度」的点，但留一个最近的落在窗口外
        的点作为锚点，使窗口跨度尽量接近 window_seconds；若所有点都在窗口内
        （刚启动样本不足），则用最老的点作锚点，跨度自然小于窗口长度。
        """
        self._window.append((ts, up_bytes, down_bytes))
        cutoff = ts - self._window_seconds
        # 只要「第二个点」仍 <= cutoff，就可以把「第一个点」作为可丢弃的旧锚点丢掉，
        # 让第二个点升级为新锚点。停在「第二个点 > cutoff」处，此时第一个点就是锚点。
        while len(self._window) > 1 and self._window[1][0] <= cutoff:
            self._window.popleft()

    def _avg_from_window(self) -> tuple[float, float, float]:
        """返回 (up_avg_bps, down_avg_bps, span_seconds)。

        用窗口最老点（锚点）到最新点的 session 累计差 / 时间跨度。
        session 累计单调非递减，故差值非负。
        """
        if len(self._window) < 2:
            return 0.0, 0.0, 0.0
        t0, u0, d0 = self._window[0]
        t1, u1, d1 = self._window[-1]
        span = t1 - t0
        if span <= 0:
            return 0.0, 0.0, 0.0
        up_avg = (u1 - u0) / span
        down_avg = (d1 - d0) / span
        return up_avg, down_avg, span

    def _reset_baseline(self, snap: NetSnapshot) -> TrafficStats:
        """记录新基线，不与上一 provider 或首次采样前的数据混算。"""
        self._last = snap
        self._provider_generation = self._current_provider_generation()
        self._push_window(snap.timestamp, self._session_up, self._session_down)
        sys_up, sys_down = self._system_total(snap)
        self._last_system_up = sys_up
        self._last_system_down = sys_down
        up_avg, down_avg, span = self._avg_from_window()
        return TrafficStats(
            iface_count=len(snap.interfaces),
            provider=self.provider_name,
            up_speed_avg_bps=up_avg,
            down_speed_avg_bps=down_avg,
            window_span_seconds=span,
            session_up_bytes=self._session_up,
            session_down_bytes=self._session_down,
            system_up_bytes=sys_up,
            system_down_bytes=sys_down,
        )

    def _first_sample(self, snap: NetSnapshot) -> TrafficStats:
        """第一次采样只能记录基线，无法算差值。"""
        return self._reset_baseline(snap)

    @staticmethod
    def _system_total(snap: NetSnapshot) -> tuple[int, int]:
        """把当前快照所有网卡的累计字节数求和，得到「开机以来」总上下行。

        计数器是系统启动以来的单调累计值，故和即为开机至今网卡收发字节总量，
        与插件何时启动无关。
        """
        up = sum(s for _, s in snap.interfaces.values())
        down = sum(r for r, _ in snap.interfaces.values())
        return up, down

    def sample(self) -> TrafficStats:
        """采集一次。失败时返回带 error 的 stats，保留上次速率总量。"""
        old_generation = self._provider_generation
        try:
            snap = self._provider.snapshot()
        except NetProviderError as e:
            up_avg, down_avg, span = self._avg_from_window()
            return TrafficStats(
                up_speed_avg_bps=up_avg,
                down_speed_avg_bps=down_avg,
                window_span_seconds=span,
                session_up_bytes=self._session_up,
                session_down_bytes=self._session_down,
                system_up_bytes=self._last_system_up,
                system_down_bytes=self._last_system_down,
                iface_count=len(self._last.interfaces) if self._last else 0,
                provider=self.provider_name,
                error=str(e),
            )

        if self._last is None:
            return self._first_sample(snap)
        if self._current_provider_generation() != old_generation:
            return self._reset_baseline(snap)

        dt = snap.timestamp - self._last.timestamp
        # timestamp 来自 monotonic；仍兜底处理异常 provider 返回的非递增时间。
        if dt <= 0:
            dt = 1e-6

        up_delta = 0
        down_delta = 0
        # 仅对两次快照都存在的网卡做差值，避免网卡增删导致总量跳变
        for iface, (recv, sent) in snap.interfaces.items():
            prev = self._last.interfaces.get(iface)
            if prev is None:
                continue
            prev_recv, prev_sent = prev
            # max 抑制系统重启计数器归零导致的负值
            up_delta += max(sent - prev_sent, 0)
            down_delta += max(recv - prev_recv, 0)

        up_speed = up_delta / dt
        down_speed = down_delta / dt

        self._session_up += up_delta
        self._session_down += down_delta
        self._last = snap

        self._push_window(snap.timestamp, self._session_up, self._session_down)
        up_avg, down_avg, span = self._avg_from_window()
        sys_up, sys_down = self._system_total(snap)
        self._last_system_up = sys_up
        self._last_system_down = sys_down

        return TrafficStats(
            up_speed_bps=up_speed,
            down_speed_bps=down_speed,
            up_delta_bytes=up_delta,
            down_delta_bytes=down_delta,
            up_speed_avg_bps=up_avg,
            down_speed_avg_bps=down_avg,
            window_span_seconds=span,
            session_up_bytes=self._session_up,
            session_down_bytes=self._session_down,
            system_up_bytes=sys_up,
            system_down_bytes=sys_down,
            iface_count=len(snap.interfaces),
            provider=self.provider_name,
        )

    def snapshot_now(self) -> NetSnapshot | None:
        """返回最近一次基线快照，供需要读网卡列表的地方使用。"""
        return self._last
