from __future__ import annotations

import sys
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_net_monitor.provider import NetProvider, NetProviderError, NetSnapshot
from astrbot_plugin_net_monitor.provider.factory import (
    ManagedNetProvider,
    ProviderCandidate,
)


def snap(timestamp: float, value: int) -> NetSnapshot:
    return NetSnapshot(timestamp=timestamp, interfaces={"eth0": (value, value)})


class StaticProvider(NetProvider):
    def __init__(
        self,
        name: str,
        items: list[NetSnapshot | NetProviderError],
    ) -> None:
        self.name = name
        self._items = deque(items)

    def snapshot(self) -> NetSnapshot:
        if not self._items:
            raise NetProviderError(f"{self.name} exhausted")
        item = self._items.popleft()
        if isinstance(item, NetProviderError):
            raise item
        return item


class ManagedNetProviderTest(unittest.TestCase):
    def test_startup_probe_skips_failed_candidate(self) -> None:
        bad = StaticProvider("bad", [NetProviderError("bad boot")])
        good = StaticProvider("good", [snap(1.0, 1), snap(2.0, 2)])

        provider = ManagedNetProvider(
            [
                ProviderCandidate("bad", lambda: bad),
                ProviderCandidate("good", lambda: good),
            ]
        )

        self.assertEqual(provider.name, "good")
        self.assertEqual(provider.snapshot().timestamp, 2.0)

    def test_runtime_switch_after_three_failures(self) -> None:
        primary = StaticProvider(
            "primary",
            [
                snap(0.0, 0),
                snap(1.0, 1),
                NetProviderError("fail1"),
                NetProviderError("fail2"),
                NetProviderError("fail3"),
            ],
        )
        backup = StaticProvider("backup", [snap(10.0, 10), snap(11.0, 11)])
        provider = ManagedNetProvider(
            [
                ProviderCandidate("primary", lambda: primary),
                ProviderCandidate("backup", lambda: backup),
            ],
            failure_threshold=3,
        )

        self.assertEqual(provider.snapshot().timestamp, 1.0)
        with self.assertRaises(NetProviderError):
            provider.snapshot()
        with self.assertRaises(NetProviderError):
            provider.snapshot()
        switched = provider.snapshot()

        self.assertEqual(switched.timestamp, 10.0)
        self.assertEqual(provider.name, "backup")
        self.assertEqual(provider.generation, 1)

    def test_all_candidates_failed_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(NetProviderError, "无可用网络数据源"):
            ManagedNetProvider(
                [
                    ProviderCandidate(
                        "bad1", lambda: StaticProvider("bad1", [NetProviderError("x")])
                    ),
                    ProviderCandidate(
                        "bad2", lambda: StaticProvider("bad2", [NetProviderError("y")])
                    ),
                ]
            )


if __name__ == "__main__":
    unittest.main()
