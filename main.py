"""AstrBot 网络流量监控插件入口（纯实时 demo 版）。

设计目标：
- 系统级网络流量监控（Linux /proc/net/dev、Windows/macOS/BSD via psutil）。
- 后台轻量采集循环算滑动窗口平均速率，/net 指令直接读缓存，数值稳。
- 纯实时，不落盘、不存历史：插件重载 / 系统重启即清零，只为演示。

数据流：
  provider.snapshot() → monitor.sample()（后台循环） → 缓存 stats
  /net 指令 → 读缓存 stats → 格式化输出
"""

from __future__ import annotations

import asyncio
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .formatter import human_bytes, human_duration, human_speed
from .monitor import NetMonitor
from .provider import NetProviderError, select_provider

_PLUGIN_TAG = "[NetMonitor]"

# 默认采集周期（秒）。太短会增加 CPU 开销；太长速率不准。
DEFAULT_INTERVAL = 2
INTERVAL_RANGE = (1, 300)

# 默认滑动窗口长度（秒），用于算「近 N 秒平均速率」。
DEFAULT_WINDOW = 5
WINDOW_RANGE = (1, 60)


class NetMonitorPlugin(Star):
    """系统网络流量实时监控插件（demo 版，无持久化）。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self._interval = self._clamp(
            config.get("sample_interval", DEFAULT_INTERVAL),
            DEFAULT_INTERVAL,
            INTERVAL_RANGE,
        )
        # 滑动窗口长度（秒），用于算平均速率。窗口需 >= 采集周期才有意义。
        window_seconds = self._clamp(
            config.get("window_seconds", DEFAULT_WINDOW),
            DEFAULT_WINDOW,
            WINDOW_RANGE,
        )
        if window_seconds < self._interval:
            window_seconds = self._interval
            logger.warning(
                f"{_PLUGIN_TAG} window_seconds < sample_interval，已收敛为 {window_seconds}s"
            )

        include_virtual = self._bool_config(
            config.get("include_virtual_interfaces", False),
            False,
        )
        include_interfaces = config.get("include_interfaces", [])
        exclude_interfaces = config.get("exclude_interfaces", [])

        # 选择数据源。失败则降级为「空监控」，保证插件仍能加载、指令仍可响应。
        self._monitor_window_seconds = window_seconds
        try:
            provider = select_provider(
                include_virtual_interfaces=include_virtual,
                include_interfaces=include_interfaces,
                exclude_interfaces=exclude_interfaces,
            )
        except NetProviderError as e:
            logger.error(f"{_PLUGIN_TAG} 无可用网络数据源：{e}")
            self._provider_failed = str(e)
            self._monitor = None
        else:
            self._provider_failed = None
            self._monitor = NetMonitor(provider, window_seconds=window_seconds)
            logger.info(
                f"{_PLUGIN_TAG} 数据源：{provider.name}，采集周期 {self._interval}s，"
                f"平均窗口 {window_seconds}s"
            )

        self._loop_task: asyncio.Task | None = None
        self._started_at = time.monotonic()
        self._last_stats = None  # 最近一次采集结果，指令查询时复用
        # 三层兜底：构造器 → initialize → on_astrbot_loaded
        self._ensure_loop_started("__init__")

    # ========== 生命周期兜底 ==========

    async def initialize(self):
        self._ensure_loop_started("initialize")

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        self._ensure_loop_started("on_astrbot_loaded")

    async def terminate(self):
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info(f"{_PLUGIN_TAG} 插件已停止")

    def _ensure_loop_started(self, reason: str) -> None:
        """幂等启动采集循环。已运行或数据源不可用则跳过。"""
        if self._monitor is None:
            return
        if self._loop_task is not None and not self._loop_task.done():
            return
        try:
            self._loop_task = asyncio.create_task(self._collect_loop())
            logger.debug(f"{_PLUGIN_TAG} 采集循环已启动（{reason}）")
        except RuntimeError:
            # 构造器阶段可能没有运行中的事件循环，留给下一层兜底
            logger.debug(f"{_PLUGIN_TAG} {reason} 阶段无事件循环，等待下一层兜底")

    # ========== 配置解析 ==========

    @staticmethod
    def _clamp(raw, default, value_range):
        lo, hi = value_range
        try:
            val = type(default)(raw)
        except (TypeError, ValueError):
            logger.warning(f"{_PLUGIN_TAG} 配置值 {raw!r} 非法，使用默认 {default}")
            return default
        if val < lo or val > hi:
            logger.warning(f"{_PLUGIN_TAG} 配置值 {val} 越界 [{lo},{hi}]，使用默认 {default}")
            return default
        return val

    @staticmethod
    def _bool_config(raw, default: bool) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        if raw is None:
            return default
        return bool(raw)

    # ========== 采集循环 ==========

    async def _collect_loop(self):
        """主采集循环：周期采样，更新缓存的 stats。

        整个循环体包在 try/except 内：任何异常都只记日志、跳过本轮、保留上次 stats，
        绝不让采集循环静默死亡。首轮采样在本循环内做（_last 为 None 时
        _first_sample 返回 0 增量，天然安全），不需要单独的基线采样。
        """
        while True:
            await asyncio.sleep(self._interval)
            try:
                stats = self._monitor.sample() if self._monitor else None
                if stats is None:
                    continue
                self._last_stats = stats
                if stats.error:
                    logger.warning(f"{_PLUGIN_TAG} 数据源错误：{stats.error}")
            except Exception as e:
                logger.error(f"{_PLUGIN_TAG} 采集循环异常（已跳过本轮）：{e}")

    # ========== 指令 ==========

    @filter.command("net")
    async def net(self, event: AstrMessageEvent):
        """实时网络流量与速率"""
        event.stop_event()
        if self._provider_failed:
            yield event.plain_result(f"❌ 无可用网络数据源：{self._provider_failed}")
            return
        stats = self._last_stats
        if stats is None or stats.error:
            yield event.plain_result("⏳ 正在采集数据，请稍候再用 /net 查看")
            return
        uptime = time.monotonic() - self._started_at
        # 窗口实际覆盖时长：启动不足窗口长度时按实际时长显示，避免「近 5s」其实只有 1s
        span = min(stats.window_span_seconds, float(self._monitor_window_seconds))
        span_text = f"{span:.1f}s" if span else "—"
        msg = (
            f"📡 网络流量监控\n"
            "─────────────\n"
            f"平均速率（近 {span_text}）\n"
            f"  ⬆ 上传 {human_speed(stats.up_speed_avg_bps)}\n"
            f"  ⬇ 下载 {human_speed(stats.down_speed_avg_bps)}\n"
            "瞬时速率\n"
            f"  ⬆ {human_speed(stats.up_speed_bps)}\n"
            f"  ⬇ {human_speed(stats.down_speed_bps)}\n"
            "─────────────\n"
            "开机以来累计\n"
            f"  ⬆ 上传 {human_bytes(stats.system_up_bytes)}\n"
            f"  ⬇ 下载 {human_bytes(stats.system_down_bytes)}\n"
            "本次启动累计\n"
            f"  ⬆ 上传 {human_bytes(stats.session_up_bytes)}\n"
            f"  ⬇ 下载 {human_bytes(stats.session_down_bytes)}\n"
            f"运行时长 {human_duration(uptime)}\n"
            f"数据源 {stats.provider} · 网卡 {stats.iface_count} 块"
        )
        yield event.plain_result(msg)
