# 标准时序类型

## 必须遵守的建模契约

`timeseries.type` 是 Core 的业务键，不是前端显示名称，也不是任意文本。新建或修改曲线前必须先查询当前 case 的服务端配置：

```bash
teap timeseries types "$CASE_PATH" --sheet wind
teap timeseries types "$CASE_PATH" --sheet load
```

只使用返回的 `sheets[].types[].value`。命令会同时为每个类型返回 `recommended_value_type`。新模型使用 `<设备表>.<类型键>` 限定形式，例如：

- 风电可用出力曲线：`wind.p_rate`；
- 光伏可用出力曲线：`solar.p_rate`；
- 负荷曲线：`load.p_rate`；
- 常规机组指定出力率：`gen.p_rate`。

绝对不要把前端的 `label`（例如 `指定出力率`、`风电可用标幺出力`、`负荷标幺功率`）写进 `type`。这些文本只用于展示，Core 仿真按类型键匹配，写入展示名会导致曲线无法识别。

旧 `.tc` 可能保存裸键 `p_rate`。Core `develop` 在读取 case 时会检查曲线绑定，并按 `tc_structure` 顺序找到的第一个引用设备补充 `ref_element`，所以单一设备类别绑定的旧曲线仍可运行；同一裸曲线跨多个设备类别复用时，这条兼容规则可能选到错误语义。CLI 仅在 `--bind-sheet` 或已有 `ref_element` 能唯一确定设备表时接受并规范化它。新建模不使用裸键。没有绑定目标时必须直接写限定类型，不能猜测 `p_rate` 的含义。

## 五个不同字段

| 字段 | 合法值/用途 | 禁止混用 |
| --- | --- | --- |
| `type` | 本文的 Core 标准类型，如 `wind.p_rate` | 不能写 `fixed`、`repeat` 或中文 label |
| `value_type` | `replace` 或 `multiply`；Core 的真实字段名 | 不能写模板模式或业务类型 |
| `data_type` | CLI 输入兼容别名，会规范化为 `value_type` | 不会原样写入 Core；不能与 `value_type` 冲突 |
| `period` | `year`、`day`、`month`、`quarter` | 只控制本地紧凑数据展开 |
| `template` | `fixed` 或 `repeat` | 只控制本地展开方式 |

`replace` 表示曲线值替换设备静态字段，`multiply` 表示曲线值乘以静态字段。所有 `*.p_rate` 推荐 `multiply`，其他标准类型推荐 `replace`；专业建模仍应按业务含义显式填写。Core 只接受这两个 `value_type`。

推荐值不匹配不会阻止写入，因为部分专业模型可能有意使用另一种合并语义；普通 JSON 成功输出会包含结构化警告：

```json
{
  "warnings": [
    {
      "code": "timeseries_value_type_not_recommended",
      "type": "load.p_rate",
      "value_type": "replace",
      "recommended_value_type": "multiply"
    }
  ]
}
```

看到该警告后不要原样重复写入；先检查静态基值、曲线数值语义和目标算法，再决定保留现值或改为推荐值。

## 当前 develop 完整类型快照

下表来自 TEAP Core `origin/develop` 的 `teap/config/tc_structure.yml`，commit `7025ae0fb761ef05ba6c51fe852c091fd62bf1f6`。它合并两类来源：设备属性的 `change_with_timeseries: true` 与表级 `extra_ts_type`。运行服务可能更新，因此命令返回值始终优先于此快照。

| 设备表 | 标准 `type` |
| --- | --- |
| `zone` | `zone.reserve_up_coe`, `zone.reserve_emer_coe`, `zone.load_relax_priority`, `zone.renewable_accommodation_priority`, `zone.renewable_penetration_upper_limit`, `zone.min_on_cap` |
| `trafo` | `trafo.maintenance`, `trafo.in_service`, `trafo.sn_mva`, `trafo.max_loading_rate`, `trafo.tap_side`, `trafo.tap_pos`, `trafo.tap_neutral`, `trafo.tap_max`, `trafo.tap_min`, `trafo.shift_degree` |
| `ac_line` | `ac_line.maintenance`, `ac_line.in_service`, `ac_line.max_i_ka`, `ac_line.max_loading_rate`, `ac_line.transmission_cost` |
| `dc_line` | `dc_line.maintenance`, `dc_line.in_service`, `dc_line.transmission_cost`, `dc_line.max_p_mw`, `dc_line.max_f2t_loading_rate`, `dc_line.max_t2f_loading_rate`, `dc_line.min_f2t_loading_rate`, `dc_line.min_t2f_loading_rate`, `dc_line.power_loss_rate` |
| `interface_add` | `interface_add.max_p_mw`, `interface_add.min_p_mw`, `interface_add.in_service`, `interface_add.itf_relax_cost` |
| `feedin` | `feedin.p_rate`, `feedin.in_service`, `feedin.max_p_mw`, `feedin.relax`, `feedin.incoming_upper_limit_after_relax`, `feedin.incoming_lower_limit_after_relax`, `feedin.outgoing_upper_limit_after_relax`, `feedin.outgoing_lower_limit_after_relax`, `feedin.relax_cost_cny_per_mwh` |
| `shunt` | `shunt.q_mvar`, `shunt.max_step`, `shunt.step`, `shunt.in_service` |
| `gen` | `gen.gen_on_off`, `gen.gen_p_daily_accum`, `gen.p_rate`, `gen.maintenance`, `gen.num_min_online`, `gen.in_service`, `gen.max_p_mw`, `gen.max_p_rate`, `gen.min_p_rate`, `gen.power_consumption_rate`, `gen.ramp_rate_per_hour`, `gen.on_off_cost_cny`, `gen.p0_cost_cny_per_mwh`, `gen.p1_cost_cny_per_mwh`, `gen.p1_pg_start_rate`, `gen.p1_pg_end_rate`, `gen.p2_cost_cny_per_mwh`, `gen.p2_pg_end_rate`, `gen.p3_cost_cny_per_mwh`, `gen.p3_pg_end_rate`, `gen.p4_cost_cny_per_mwh`, `gen.p4_pg_end_rate`, `gen.p5_cost_cny_per_mwh`, `gen.p5_pg_end_rate` |
| `hydro_acc` | `hydro_acc.gen_on_off`, `hydro_acc.gen_p_daily_accum`, `hydro_acc.p_rate`, `hydro_acc.maintenance`, `hydro_acc.num_min_online`, `hydro_acc.in_service`, `hydro_acc.max_p_mw`, `hydro_acc.max_p_rate`, `hydro_acc.min_p_rate`, `hydro_acc.power_consumption_rate`, `hydro_acc.ramp_rate_per_hour`, `hydro_acc.on_off_cost_cny`, `hydro_acc.p0_cost_cny_per_mwh`, `hydro_acc.p1_cost_cny_per_mwh`, `hydro_acc.p1_pg_start_rate`, `hydro_acc.p1_pg_end_rate`, `hydro_acc.p2_cost_cny_per_mwh`, `hydro_acc.p2_pg_end_rate`, `hydro_acc.p3_cost_cny_per_mwh`, `hydro_acc.p3_pg_end_rate`, `hydro_acc.p4_cost_cny_per_mwh`, `hydro_acc.p4_pg_end_rate`, `hydro_acc.p5_cost_cny_per_mwh`, `hydro_acc.p5_pg_end_rate` |
| `hydropower` | `hydropower.maintenance`, `hydropower.in_service`, `hydropower.max_p_mw`, `hydropower.max_p_rate`, `hydropower.min_p_rate`, `hydropower.water_cost` |
| `reservoir` | `reservoir.max_volume`, `reservoir.min_volume`, `reservoir.max_charge_flow`, `reservoir.max_discharge_flow`, `reservoir.min_discharge_flow` |
| `wind` | `wind.p_rate`, `wind.maintenance`, `wind.max_p_mw`, `wind.in_service`, `wind.generation_cost` |
| `solar` | `solar.p_rate`, `solar.maintenance`, `solar.max_p_mw`, `solar.in_service`, `solar.generation_cost` |
| `load` | `load.p_rate`, `load.in_service`, `load.max_p_mw` |
| `dsm` | `dsm.in_service`, `dsm.allow_direct_demand_response`, `dsm.max_p_mw_direct_inc_dr`, `dsm.max_p_mw_direct_dec_dr`, `dsm.allow_load_shift`, `dsm.max_p_mw_shift_inc_dr`, `dsm.max_p_mw_shift_dec_dr`, `dsm.direct_increase_cost_mwh`, `dsm.direct_shred_cost_mwh`, `dsm.shift_dr_cost_mwh` |
| `dsm_plan` | `dsm_plan.in_service`, `dsm_plan.allow_direct_demand_response`, `dsm_plan.max_p_mw_direct_inc_dr`, `dsm_plan.max_p_mw_direct_dec_dr`, `dsm_plan.allow_load_shift`, `dsm_plan.max_p_mw_shift_inc_dr`, `dsm_plan.max_p_mw_shift_dec_dr`, `dsm_plan.direct_increase_cost_mwh`, `dsm_plan.direct_shred_cost_mwh`, `dsm_plan.shift_dr_cost_mwh` |
| `stogen` | `stogen.discharge_charge_state`, `stogen.discharge_power_pu`, `stogen.charge_power_pu`, `stogen.maintenance`, `stogen.max_p_discharge_mw`, `stogen.min_p_discharge_mw`, `stogen.max_p_charge_mw`, `stogen.min_p_charge_mw`, `stogen.in_service`, `stogen.charge_cost_cny_per_mwh`, `stogen.discharge_cost_cny_per_mwh` |
| `storage` | `storage.max_e_mwh`, `storage.standing_loss_rate`, `storage.min_e_mwh` |
| `hydrogen_tank` | `hydrogen_tank.discharge_charge_state`, `hydrogen_tank.discharge_power_pu`, `hydrogen_tank.charge_power_pu`, `hydrogen_tank.maintenance`, `hydrogen_tank.external_hydrogen_load_kg`, `hydrogen_tank.max_p_discharge_mw`, `hydrogen_tank.min_p_discharge_mw`, `hydrogen_tank.max_p_charge_mw`, `hydrogen_tank.min_p_charge_mw`, `hydrogen_tank.in_service`, `hydrogen_tank.charge_cost_cny_per_mwh`, `hydrogen_tank.discharge_cost_cny_per_mwh`, `hydrogen_tank.max_e_mwh`, `hydrogen_tank.standing_loss_rate`, `hydrogen_tank.min_e_mwh` |
| `csp` | `csp.dni_pu`, `csp.p_rate`, `csp.maintenance`, `csp.gen_on_off`, `csp.max_p_mw`, `csp.max_p_rate`, `csp.min_p_rate`, `csp.in_service`, `csp.on_off_cost_cny`, `csp.charge_cost_cny_per_mwh`, `csp.discharge_cost_cny_per_mwh`, `csp.charge_efficiency`, `csp.discharge_efficiency`, `csp.max_e_mwh`, `csp.min_e_mwh`, `csp.standing_loss_rate`, `csp.max_p_charge_resistive_mw`, `csp.resistive_charge_cost_cny_per_mwh`, `csp.charge_resistive_efficiency`, `csp.max_p_charge_gas_mw`, `csp.gas_charge_cost_cny_per_mwh` |
| `gen_plan` | `gen_plan.gen_on_off`, `gen_plan.gen_p_daily_accum`, `gen_plan.p_rate`, `gen_plan.maintenance`, `gen_plan.num_min_online`, `gen_plan.in_service`, `gen_plan.max_p_mw`, `gen_plan.max_p_rate`, `gen_plan.min_p_rate`, `gen_plan.power_consumption_rate`, `gen_plan.ramp_rate_per_hour`, `gen_plan.on_off_cost_cny`, `gen_plan.p0_cost_cny_per_mwh`, `gen_plan.p1_cost_cny_per_mwh`, `gen_plan.p1_pg_start_rate`, `gen_plan.p1_pg_end_rate`, `gen_plan.p2_cost_cny_per_mwh`, `gen_plan.p2_pg_end_rate`, `gen_plan.p3_cost_cny_per_mwh`, `gen_plan.p3_pg_end_rate`, `gen_plan.p4_cost_cny_per_mwh`, `gen_plan.p4_pg_end_rate`, `gen_plan.p5_cost_cny_per_mwh`, `gen_plan.p5_pg_end_rate` |
| `hydro_acc_plan` | `hydro_acc_plan.gen_on_off`, `hydro_acc_plan.gen_p_daily_accum`, `hydro_acc_plan.p_rate`, `hydro_acc_plan.maintenance`, `hydro_acc_plan.num_min_online`, `hydro_acc_plan.in_service`, `hydro_acc_plan.max_p_mw`, `hydro_acc_plan.max_p_rate`, `hydro_acc_plan.min_p_rate`, `hydro_acc_plan.power_consumption_rate`, `hydro_acc_plan.ramp_rate_per_hour`, `hydro_acc_plan.on_off_cost_cny`, `hydro_acc_plan.p0_cost_cny_per_mwh`, `hydro_acc_plan.p1_cost_cny_per_mwh`, `hydro_acc_plan.p1_pg_start_rate`, `hydro_acc_plan.p1_pg_end_rate`, `hydro_acc_plan.p2_cost_cny_per_mwh`, `hydro_acc_plan.p2_pg_end_rate`, `hydro_acc_plan.p3_cost_cny_per_mwh`, `hydro_acc_plan.p3_pg_end_rate`, `hydro_acc_plan.p4_cost_cny_per_mwh`, `hydro_acc_plan.p4_pg_end_rate`, `hydro_acc_plan.p5_cost_cny_per_mwh`, `hydro_acc_plan.p5_pg_end_rate` |
| `wind_plan` | `wind_plan.p_rate`, `wind_plan.maintenance`, `wind_plan.max_p_mw`, `wind_plan.in_service`, `wind_plan.generation_cost` |
| `solar_plan` | `solar_plan.p_rate`, `solar_plan.maintenance`, `solar_plan.max_p_mw`, `solar_plan.in_service`, `solar_plan.generation_cost` |
| `stogen_plan` | `stogen_plan.discharge_charge_state`, `stogen_plan.discharge_power_pu`, `stogen_plan.charge_power_pu`, `stogen_plan.maintenance`, `stogen_plan.max_p_discharge_mw`, `stogen_plan.min_p_discharge_mw`, `stogen_plan.max_p_charge_mw`, `stogen_plan.min_p_charge_mw`, `stogen_plan.in_service`, `stogen_plan.charge_cost_cny_per_mwh`, `stogen_plan.discharge_cost_cny_per_mwh` |
| `storage_plan` | `storage_plan.max_e_mwh`, `storage_plan.standing_loss_rate`, `storage_plan.min_e_mwh` |
| `hydrogen_tank_plan` | `hydrogen_tank_plan.discharge_charge_state`, `hydrogen_tank_plan.discharge_power_pu`, `hydrogen_tank_plan.charge_power_pu`, `hydrogen_tank_plan.maintenance`, `hydrogen_tank_plan.external_hydrogen_load_kg`, `hydrogen_tank_plan.max_p_discharge_mw`, `hydrogen_tank_plan.min_p_discharge_mw`, `hydrogen_tank_plan.max_p_charge_mw`, `hydrogen_tank_plan.min_p_charge_mw`, `hydrogen_tank_plan.in_service`, `hydrogen_tank_plan.charge_cost_cny_per_mwh`, `hydrogen_tank_plan.discharge_cost_cny_per_mwh`, `hydrogen_tank_plan.max_e_mwh`, `hydrogen_tank_plan.standing_loss_rate`, `hydrogen_tank_plan.min_e_mwh` |
| `ac_line_plan` | `ac_line_plan.maintenance`, `ac_line_plan.in_service`, `ac_line_plan.max_i_ka`, `ac_line_plan.max_loading_rate`, `ac_line_plan.transmission_cost` |
| `dc_line_plan` | `dc_line_plan.maintenance`, `dc_line_plan.in_service`, `dc_line_plan.transmission_cost`, `dc_line_plan.max_p_mw`, `dc_line_plan.max_f2t_loading_rate`, `dc_line_plan.max_t2f_loading_rate`, `dc_line_plan.min_f2t_loading_rate`, `dc_line_plan.min_t2f_loading_rate`, `dc_line_plan.power_loss_rate` |
| `integrated` | `integrated.p_rate`, `integrated.maintenance`, `integrated.in_service`, `integrated.max_p_mw_wind`, `integrated.max_p_mw_solar`, `integrated.max_p_discharge_mw`, `integrated.min_p_discharge_mw`, `integrated.max_p_charge_mw`, `integrated.min_p_charge_mw`, `integrated.charge_cost_cny_per_mwh`, `integrated.discharge_cost_cny_per_mwh`, `integrated.max_e_mwh`, `integrated.min_e_mwh`, `integrated.standing_loss_rate` |

## 写入与停止条件

标准流程：

1. 运行 `timeseries types` 查询目标 sheet；
2. 从 `types[].value` 选择精确限定类型，并读取同一项的 `recommended_value_type`；
3. 明确 `value_type` 和非空 `scenario`；若使用 `data_type` 别名，不能再给出不同的 `value_type`；
4. 创建曲线并绑定设备；
5. 回读 `timeseries` 与设备行，确认 type、场景和曲线 ID；
6. 设置同名 `case_info.scenario_selected`，再校验和启动。

遇到 `timeseries_type_required`、`timeseries_type_unqualified`、`invalid_timeseries_type`、`timeseries_type_sheet_mismatch`、`timeseries_type_sheet_unsupported`、`invalid_timeseries_value_type` 或 `conflicting_timeseries_value_type` 时，不要原样重试。按 hint 查询服务端清单并纠正输入；如果服务端清单没有业务所需类型，停止建模并报告 Core/服务版本与缺失类型，不要改用中文 label 或相似键。
