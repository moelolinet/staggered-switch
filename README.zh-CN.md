<div align="center">

<img src="custom_components/staggered_switch/brand/icon@2x.png" width="150" alt="Staggered Switch">

# Staggered Switch

Home Assistant 自定义集成：按「多日轮换周期」定时开关实体

[English](README.md) | **简体中文**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/moelolinet/staggered-switch)](https://github.com/moelolinet/staggered-switch/releases)

</div>

---

一个 Home Assistant 自定义集成，按照重复的**多日周期**打开或关闭实体。你不必为每个班次
写一条自动化，只需把排班表描述一次 —— 「周期第 3 天 06:30 → 打开」—— 周期就会永远循环下去。

它面向与「周」不对齐的排班：8 天轮换、12 小时班次、交替值班表，或任何「每 N 天」的计划。

> **说明** —— 这是社区集成，不是 Home Assistant 官方集成。Home Assistant 会显示
> 常见的「未经 Home Assistant 测试」提示。

## 功能

- **基于周期的排程** —— 周期长度可设为 1 到 366 天并指定第一天；每个动作都以
  *周期第 N 天* 加一个时刻来描述。
- **动作数量不受限** —— 一天可以有多个动作，多个动作也可以落在同一分钟。
- **打开 / 关闭 / 自定义** —— 使用开关式快捷动作，或携带自定义数据调用任意
  Home Assistant 服务（例如发送通知）。
- **重启后依然有效** —— 设备断电重启后，最近一个动作会在启动时补执行一次，
  使实体重新符合排班表。安装集成本身不会切换任何东西。
- **夏令时安全** —— 时区偏移变化时，动作仍保持本地墙上时间。当天不存在的时刻
  （例如时钟从 02:00 跳到 03:00 时的 02:30）会跳过而不是顺延到下一个小时；
  重复出现的小时使用第一次。
- **可随时编辑** —— 重新打开集成选项即可新增、修改、删除、禁用动作或更改周期长度；
  不再符合周期的动作会被自动清理。
- **状态传感器** —— 提供当前周期位置、下次运行、上次运行以及最后一次服务调用的错误。

## 安装

### HACS（自定义存储库）

1. 在 HACS 中打开菜单，选择 **自定义存储库**。
2. 填入本仓库地址，类别选择 **Integration**。
3. 安装 **Staggered Switch**。
4. 重启 Home Assistant。

### 手动安装

把 `custom_components/staggered_switch` 文件夹复制到 Home Assistant 的
`config/custom_components` 目录，然后重启 Home Assistant。

## 配置

1. 进入 **设置 → 设备与服务 → 添加集成**。
2. 搜索 **Staggered Switch**。
3. 填写：

   | 字段 | 含义 |
   | --- | --- |
   | 名称 | 该周期的显示名称。 |
   | 周期第一天 | 周期的第 1 天。 |
   | 周期长度（天） | 一个完整周期持续多少天。 |

4. 从菜单中添加动作。一个动作的所有信息都在**同一页**上收集：

   | 字段 | 含义 |
   | --- | --- |
   | 周期第几天 | 1 … 周期长度 |
   | 时刻 | 本地墙上时间 |
   | 动作 | 打开、关闭，或调用其它服务 |
   | 实体 | 要操作的实体（打开 / 关闭时必填） |

   选择 *调用其它服务* 可以调用任意服务；此时实体字段可选，填写后会一并传递。

5. 按需重复 **添加动作**，然后 **保存并完成**。

动作选择器提供 **打开**、**关闭** 和 **调用其它服务**。实际服务根据所选实体推导，
因为不同 domain 的命名并不统一：`cover` 使用 `open_cover` / `close_cover`，
`valve` 使用 `open_valve` / `close_valve`，而 switch 与 light 使用
`turn_on` / `turn_off`。

支持的 domain：`switch`、`light`、`fan`、`input_boolean`、`automation`、
`humidifier`、`media_player`、`remote`、`siren`、`cover`、`valve`。button 只支持
`press`，需要时请使用 **调用其它服务**。

之后都可以通过集成的 **配置** 按钮修改。

### 示例：8 天轮换

周期长度 `8`，第一天 `2024-01-01`（周期第 1 天）：

| 天 | 时刻 | 动作 |
| --- | --- | --- |
| 1 | 06:00 | 打开 `switch.water_heater` |
| 2 | 06:00 | 打开 `switch.water_heater` |
| 3 | 06:00 | 打开 `switch.water_heater` |
| 4 | 06:00 | 打开 `switch.water_heater` |
| 8 | 23:00 | 关闭 `switch.water_heater` |

第 9 天重新成为下一周期的第 1 天，如此循环。

## 实体

每个周期生成一个传感器，名称为 **Next run**，状态是下一个动作的时间戳。其属性包括：

| 属性 | 含义 |
| --- | --- |
| `cycle_index` | 当前周期序号（0 = 第一个周期）。 |
| `cycle_day` | 周期内的当前天（从 1 开始）。 |
| `cycle_days` | 配置的周期长度。 |
| `cycle_start` / `cycle_end` | 当前周期的首日与末日。 |
| `next_run` / `next_action` | 下一个执行时刻以及要执行的动作。 |
| `upcoming` | 接下来的三个动作。 |
| `last_run` / `last_action` | 最近一次成功执行。 |
| `last_error` | 最近一次调用的错误（若失败）。 |
| `run_count` | 自添加集成以来已执行的动作数。 |
| `actions` | 按存储顺序排列的已配置动作（前 50 条）。 |
| `actions_total` | 已配置动作总数。 |

## 服务

### `staggered_switch.run_action`

立即执行一个已配置的动作，忽略其计划时刻。便于测试排班表。

| 字段 | 含义 |
| --- | --- |
| `entry_id` | 该动作所属的周期。 |
| `rule_index` | 动作的从零开始的位置，与传感器 `actions` 属性中的顺序一致。 |

```yaml
service: staggered_switch.run_action
data:
  entry_id: 01J8Z3Q9VK4M2P7T1H6B5N0CXD
  rule_index: 0
```

## 周期如何计算

- 周期的第 1 天是配置的第一天。因此长度为 *N* 天的周期覆盖第 1 … N 天，然后重复。
- 每一天都是完整自然日；00:30 的动作会生成新的时刻，不继承前一天的任何内容。
- **下次运行** 始终严格位于未来，因此已经执行过的动作不会被再次报告。
- 动作按时间顺序执行；若两个动作在同一分钟，两者都会执行。
- 若动作失败（例如实体不可用），错误会显示在传感器中，排程继续进行。

## 常见问题

- **什么都不切换。** 检查传感器：`next_run` 是否在未来？把 `cycle_day` 与配置的天数对照。
  记住第 1 天是你配置的第一天，而不是「今天」。
- **重启后设备立刻切换了一次。** 这是启动收敛：最近一个动作会被补执行，使实体符合排班表。
  每个动作最多发生一次。
- **一个动作失败但其它动作仍执行。** 失败按动作隔离；错误显示在 `last_error` 中，排程继续工作。
- **`last_error` 有内容。** 动作触发时目标实体或服务不可用。检查实体 id 以及提供它的集成是否正常。
- **切换日当天什么都没执行。** 如果动作落在夏令时被跳过的小时内，当天不会执行。
  请选择跳过小时之外的时间，或在安全时刻再加一个动作。

## 许可

MIT
