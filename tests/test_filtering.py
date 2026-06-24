from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_net_monitor.provider.filtering import InterfaceFilter


class InterfaceFilterTest(unittest.TestCase):
    def test_default_excludes_virtual_and_loopback_interfaces(self) -> None:
        filt = InterfaceFilter()

        self.assertFalse(filt.allow("lo"))
        self.assertFalse(filt.allow("docker0"))
        self.assertFalse(filt.allow("br-123"))
        self.assertFalse(filt.allow("vethabcd"))
        self.assertFalse(filt.allow("utun2"))
        self.assertFalse(filt.allow("Karing TUN Network Adapter"))
        self.assertFalse(filt.allow("vEthernet (WSL)"))
        self.assertFalse(filt.allow("Local Area Connection* 9"))
        self.assertFalse(filt.allow("本地连接* 10"))
        self.assertFalse(filt.allow("Tailscale"))
        self.assertTrue(filt.allow("eth0"))
        self.assertTrue(filt.allow("WLAN"))

    def test_include_virtual_interfaces_disables_default_blacklist(self) -> None:
        filt = InterfaceFilter(include_virtual_interfaces=True)

        self.assertTrue(filt.allow("lo"))
        self.assertTrue(filt.allow("docker0"))

    def test_include_interfaces_bypasses_default_blacklist(self) -> None:
        filt = InterfaceFilter(include_interfaces=["docker*"])

        self.assertTrue(filt.allow("docker0"))
        self.assertFalse(filt.allow("eth0"))

    def test_exclude_interfaces_has_final_priority(self) -> None:
        filt = InterfaceFilter(
            include_virtual_interfaces=True,
            include_interfaces=["eth*", "docker*"],
            exclude_interfaces=["docker*"],
        )

        self.assertTrue(filt.allow("eth0"))
        self.assertFalse(filt.allow("docker0"))

    def test_apply_filters_interface_mapping(self) -> None:
        filt = InterfaceFilter(exclude_interfaces=["wlan*"])
        interfaces = {
            "eth0": (100, 200),
            "wlan0": (300, 400),
            "lo": (500, 600),
        }

        self.assertEqual(filt.apply(interfaces), {"eth0": (100, 200)})


if __name__ == "__main__":
    unittest.main()
