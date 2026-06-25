# astrbot_plugin_net_monitor

AstrBot 系统级网络流量监控插件，支持实时速率与当月累计流量统计。

## 特性

- **系统级数据源**：Linux 优先读取 `/proc/net/dev`，Windows / macOS / BSD 使用 `psutil.net_io_counters(pernic=True)`。
  不依赖业务埋点，不受 AstrBot 内部逻辑影响。
- **统一统计口径**：默认统计非虚拟、非环回网卡的收发总量，避免 loopback、Docker、网桥、VPN、虚拟化接口污染结果。
- **后台轻量采集循环**：周期性采样并计算滑动窗口平均速率，`/net` 指令直接读缓存，数值稳、不抖动。
- **当月累计持久化**：每轮成功采样的上下行增量会写入 AstrBot 插件数据目录，并按自然月自动归零。
- **自动回退**：启动时逐个数据源试读；运行期 active provider 连续失败 3 次后尝试切换备用 provider。

> 当月累计从插件开始运行并成功采样后计算；插件未运行期间已经产生的系统流量无法反推。

## 安装

将本目录放入 AstrBot 插件目录，依赖会自动安装：

```text
psutil>=5.9.0
```

> Linux 上 `/proc/net/dev` 是内核导出，无需 psutil；但建议仍保留 psutil，便于原生数据源不可用时回退。

## 配置

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `sample_interval` | `2` | 采集周期（秒），范围 1~300 |
| `window_seconds` | `5` | 平均速率窗口（秒），范围 1~60；小于采集周期时自动收敛 |
| `include_virtual_interfaces` | `false` | 是否统计默认虚拟/隧道/环回接口 |
| `include_interfaces` | `[]` | 只统计匹配这些 glob 的接口；非空时绕过默认虚拟接口黑名单 |
| `exclude_interfaces` | `[]` | 最终排除匹配这些 glob 的接口，优先级最高 |

过滤优先级：

1. `include_interfaces` 非空时，只保留匹配项。
2. 否则，当 `include_virtual_interfaces=false` 时应用默认虚拟接口黑名单。
3. 最后应用 `exclude_interfaces`。

## 指令

| 指令 | 说明 |
|------|------|
| `/net` | 实时速率（滑动窗口平均+瞬时）+ 当月累计 + 本次启动累计 + 运行时长 + 数据源/网卡数 |

## 架构

```text
provider/              数据源层（抽象 + 过滤 + 平台实现 + 工厂回退）
  base.py              NetProvider 抽象 + NetSnapshot
  filtering.py         统一网卡过滤规则
  linux.py             /proc/net/dev 数据源
  psutil_provider.py   通用 psutil 数据源
  windows.py           Windows psutil 薄包装
  factory.py           ManagedNetProvider + select_provider()
monitor.py             差值累计 + 滑动窗口平均 + provider 切换基线重置
monthly_usage.py       当月累计 JSON 持久化 + 自然月轮转
formatter.py           字节/速率/时长格式化
main.py                Star 入口 + 采集循环 + /net 指令
```

## 设计说明

- **为什么用滑动窗口平均**：瞬时速率来自两次采样的差值，单次抖动会产生尖刺；窗口平均有明确的「近 N 秒」语义。
- **为什么后台循环**：拉取式（命令触发才采）的速率受调用间隔影响，间隔越久越不准；后台固定周期采样，`/net` 拿到的是最近一次稳定值。
- **为什么切换 provider 要重置基线**：不同 provider 或接口名集合不能直接做差；切换当轮只记录新基线，避免产生假流量尖刺。
- **当前获取的系统总量是什么**：底层 `/proc/net/dev` / `psutil.net_io_counters(pernic=True)` 返回的是系统启动以来的网卡累计计数。插件不会把这个值直接当作当月流量，而是只把相邻两次采样的正向差值累加到当月统计里。
