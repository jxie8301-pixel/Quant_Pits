# data_sync 使用文档

> QuantPits A 股数据同步与 Qlib bin/PIT 生成工具。

## 数据流

```text
Tushare API
  → data/raw/*.parquet          （项目级，多 Workspace 共享）
  → dump-pit → financial/       （PIT 文件，forward_fill 型事件字段）
  → dump-bin → qlib_data/       （日频 bin，含 indicator / event_day_only 型事件字段）
```

## 子命令

| 子命令 | 作用 |
| --- | --- |
| `sync-static` | 同步静态表（`stock_basic`、`trade_cal`） |
| `sync-daily` | 同步日频接口 raw |
| `sync-event` | 同步事件型接口 raw |
| `dump-pit` | 从 raw 事件数据生成 qlib PIT 文件（forward_fill 型字段） |
| `dump-bin` | 从 raw 直接按股票生成 Qlib bin（日频 + indicator + event_day_only 型字段） |
| `sync-all` | 按顺序一键执行完整流程 |
| `status` | 查看同步状态 |

## 环境准备

```bash
# 数据拉取只需 Tushare Token
export TUSHARE_TOKEN=your_token

# dump-pit / dump-bin / sync-all 需要 Workspace
export QLIB_WORKSPACE_DIR=/path/to/workspaces/LGBM_ALSTM_Workspace
```

也可显式传入：

```bash
python -m quantpits.scripts.data_sync --workspace workspaces/LGBM_ALSTM_Workspace status
```

raw 数据默认写到项目级 `data/raw/`，多 Workspace 共享。可用 `QUANTPITS_RAW_DIR` 指定自定义 raw 目录。

## 一键流程

### 首次全量构建

```bash
python -m quantpits.scripts.data_sync sync-all --mode full
```

执行顺序：

```text
sync-static → sync-daily → sync-event → dump-pit → dump-bin
```

- `sync-static`：全量覆盖静态表。
- `sync-daily --mode full`：从接口起始日到今天全量重拉日频 raw。
- `sync-event --mode full`：尾部回溯 + 区间合并去重拉取事件 raw。
- `dump-pit`：生成 PIT 文件（forward_fill 型事件字段）。
- `dump-bin --mode full`：清理旧 `features/` 后全量生成 Qlib bin。

### 每日增量更新

```bash
python -m quantpits.scripts.data_sync sync-all
```

等价于 `--mode daily`：

- 日频接口只拉最后日期之后的新交易日。
- 事件接口从最后公告日回溯 3 天拉取。
- `dump-pit` 增量更新 PIT 文件。
- `dump-bin` 增量追加写入 bin。

### 修复历史缺口

```bash
python -m quantpits.scripts.data_sync sync-all --repair
```

- 日频：扫描交易日历，只补缺失 raw，不覆盖已有文件。
- 事件：仍使用常规尾部回溯逻辑。
- 后续执行 `dump-pit` 和 `dump-bin`。

### 中后续跑

```bash
python -m quantpits.scripts.data_sync sync-all --skip sync-static sync-daily
```

可跳过步骤名：`sync-static` `sync-daily` `sync-event` `dump-pit` `dump-bin`

### 指定股票

`--stocks` 同时过滤 `dump-pit` 和 `dump-bin`，仅生成指定股票的 PIT、bin 和 instruments：

```bash
python -m quantpits.scripts.data_sync sync-all --mode full --stocks 600519.SH 000001.SZ 000333.SZ
```

## 分步同步

### 1. 静态表

```bash
python -m quantpits.scripts.data_sync sync-static
```

每次全量覆盖 `stock_basic.parquet` 和 `trade_cal.parquet`。

### 2. 日频数据

```bash
python -m quantpits.scripts.data_sync sync-daily --mode full     # 全量
python -m quantpits.scripts.data_sync sync-daily --mode daily    # 增量
python -m quantpits.scripts.data_sync sync-daily --repair        # 补缺口
python -m quantpits.scripts.data_sync sync-daily --tier post_market  # 按tier
python -m quantpits.scripts.data_sync sync-daily --interfaces stk_factor_pro moneyflow  # 指定接口
```

日频接口：

| 接口 | tier | 说明 |
| --- | --- | --- |
| `stk_factor_pro` | post_market | 行情因子（15:00 后可用） |
| `suspend_d` | post_market | 停牌 |
| `stk_limit` | post_market | 涨跌停 |
| `moneyflow` | capital_flow | 资金流（16:00 后） |
| `margin_detail` | capital_flow | 融资融券 |
| `cyq_perf` | capital_flow | 筹码分布 |
| `top_list` | evening | 龙虎榜（19:00 后） |

### 3. 事件数据

```bash
python -m quantpits.scripts.data_sync sync-event --mode daily
```

事件接口按公告日 `ann_date` 存储，增量从最后公告日回溯 3 天拉取，按主键合并去重。

| 接口 | 主键 |
| --- | --- |
| `stk_holdertrade` | ts_code, ann_date, holder_name, begin_date |
| `share_float` | ts_code, ann_date, float_date, holder_name |
| `forecast_vip` | ts_code, ann_date, end_date, type |
| `express_vip` | ts_code, ann_date, end_date |

### 4. 生成 PIT 文件

```bash
python -m quantpits.scripts.data_sync dump-pit
```

指定股票：

```bash
python -m quantpits.scripts.data_sync dump-pit --stocks 600519.SH 000001.SZ
```

从 raw 事件数据生成 qlib PIT 格式文件，用于训练时 `P($$field_q)` 表达式读取 forward_fill 型事件字段。

输出：

```text
workspaces/<Workspace>/data/qlib_data/financial/<symbol>/<field>_q.data
workspaces/<Workspace>/data/qlib_data/financial/<symbol>/<field>_q.index
```

### 5. 生成 Qlib bin

#### 全量

```bash
python -m quantpits.scripts.data_sync dump-bin --mode full
```

删除旧 `features/` 后全量重建。

#### 增量

```bash
python -m quantpits.scripts.data_sync dump-bin --mode daily --start-date 20260427 --end-date 20260427
```

- 使用完整交易日历作为索引基准。
- 新值优先；新值为 NaN 时保留旧值。
- `--start-date/--end-date` 只限制写入窗口，不截断 `day.txt`。

未指定日期时，根据 `stk_factor_pro` 已同步 raw 自动推断范围。

#### 指定股票

`--stocks` 仅生成指定股票的 bin 和 instruments，calendars 仍基于完整交易日历：

```bash
python -m quantpits.scripts.data_sync dump-bin --mode full --stocks 600519.SH 000001.SZ
```

#### 自定义字段映射

```bash
python -m quantpits.scripts.data_sync dump-bin --mode full --field-mapping config/field_mapping.yaml
```

## 事件字段分类

| 字段类型 | 存储 | 训练表达式 | 示例 |
| --- | --- | --- | --- |
| forward_fill | PIT `.data`+`.index` | `P($$field_q)` | `holder_change_ratio_q`, `float_ratio_q`, `forecast_net_ratio_q`, `express_roe_q`, `express_yoy_net_q` |
| indicator | 日频 bin | `$field` | `holder_change_flag`, `float_flag`, `forecast_flag`, `express_flag` |
| event_day_only | 日频 bin | `$field` | `holder_change`, `float_vol` |

- **forward_fill**：公告日后持续填充到下一事件。由 `dump-pit` 生成 PIT 文件。
- **indicator**：公告日=1.0，其余=0.0。由 `dump-bin` 直接生成日频 bin。
- **event_day_only**：仅公告日有值，其余=NaN。由 `dump-bin` 直接生成日频 bin。

## 调度建议

| 时间 | 命令 | 说明 |
| --- | --- | --- |
| 15:35 | `sync-daily --tier post_market` | 行情因子、停牌、涨跌停 |
| 19:35 | `sync-daily --tier capital_flow` | 资金流、融资融券、筹码 |
| 20:35 | `sync-daily --tier evening` | 龙虎榜、业绩预告/快报 |
| 次日 08:00 | `sync-event --mode daily` | 事件接口 |
| 次日 08:30 | `dump-pit && dump-bin --mode daily` | 生成 PIT 和 Qlib bin |

也可用一键命令代替：

```bash
python -m quantpits.scripts.data_sync sync-all --mode daily
```

## 目录结构

```text
Quant_Pits/
├── data/
│   └── raw/                         # 项目级 raw，多 Workspace 共享
│       ├── stock_basic.parquet
│       ├── trade_cal.parquet
│       ├── stk_factor_pro/
│       │   ├── 20180102.parquet
│       │   └── ...
│       ├── stk_holdertrade/
│       └── ...
└── workspaces/
    └── LGBM_ALSTM_Workspace/
        ├── config/
        │   └── field_mapping.yaml
        └── data/
            └── qlib_data/
                ├── calendars/
                │   ├── day.txt
                │   └── day_future.txt
                ├── instruments/
                │   └── all.txt
                ├── financial/       # PIT 文件（dump-pit 生成）
                │   └── sh600519/
                │       ├── holder_change_ratio_q.data
                │       └── holder_change_ratio_q.index
                └── features/        # 日频 bin（dump-bin 生成）
                    ├── sh600519/
                    │   ├── close.day.bin
                    │   └── ...
                    └── sz000001/
```

## 状态检查

```bash
python -m quantpits.scripts.data_sync status
```

重点确认：

- `stk_factor_pro` 已到最新交易日。
- 各 tier 最新日期符合数据发布时间。
- `financial/` 下有 PIT 文件。
- `features/` 下有对应股票和字段 bin。

## 常见问题

### Q: `full`、`daily`、`repair` 怎么选？

- 首次初始化或怀疑 raw 大面积污染：`sync-all --mode full`。
- 日常收盘后更新：`sync-all --mode daily`。
- 发现日频历史缺口但不覆盖已有文件：`sync-all --repair`。

### Q: 只补了历史某几天，bin 怎么更新？

```bash
python -m quantpits.scripts.data_sync dump-pit
python -m quantpits.scripts.data_sync dump-bin --mode daily --start-date START --end-date END
```

增量 bin 基于完整 calendar 合并写入，不会重置历史索引。

### Q: 北交所股票如何处理？

`.BJ` 股票在 Tushare 转 Qlib 代码时过滤，不生成 Qlib bin。

### Q: 训练配置中事件字段怎么写？

- forward_fill 型用 `P($$field_q)`，如 `P($$holder_change_ratio_q)`。
- indicator / event_day_only 型用 `$field`，如 `$holder_change_flag`、`$float_vol`。
