# data_sync 日常操作指南

完整原理见 [data_sync_usage.md](data_sync_usage.md)。

## 最常用命令

```bash
# 每日增量（默认 --mode daily）
python -m quantpits.scripts.data_sync sync-all

# 首次全量
python -m quantpits.scripts.data_sync sync-all --mode full

# 修复日频历史缺口
python -m quantpits.scripts.data_sync sync-all --repair

# 查看状态
python -m quantpits.scripts.data_sync status
```

## 每日收盘后流程

一键：

```bash
python -m quantpits.scripts.data_sync sync-all --mode daily
```

分层调度（适合 crontab）：

```bash
# 15:35 盘后即时数据
python -m quantpits.scripts.data_sync sync-daily --tier post_market

# 19:35 资金与筹码数据
python -m quantpits.scripts.data_sync sync-daily --tier capital_flow

# 20:35 晚间数据
python -m quantpits.scripts.data_sync sync-daily --tier evening

# 次日 08:00 事件数据
python -m quantpits.scripts.data_sync sync-event --mode daily

# 次日 08:30 生成 PIT 和 bin
python -m quantpits.scripts.data_sync dump-pit
python -m quantpits.scripts.data_sync dump-bin --mode daily --start-date YYYYMMDD --end-date YYYYMMDD
```

## 全量、增量、修复

| 模式 | 日频 raw | 事件 raw | bin |
| --- | --- | --- | --- |
| 全量 `--mode full` | 全量重拉覆盖 | 尾部回溯拉取 | 删除旧 features 后重建 |
| 增量 `--mode daily` | 只拉新交易日 | 回溯 3 天拉取 | 追加或合并写入 |
| 修复 `--repair` | 只补缺失文件 | 常规尾部回溯 | 合并写入补到的日期 |

## 只生成 PIT 和 bin

raw 已准备好时：

```bash
python -m quantpits.scripts.data_sync dump-pit
python -m quantpits.scripts.data_sync dump-bin --mode full
```

只更新某日期范围：

```bash
python -m quantpits.scripts.data_sync dump-pit
python -m quantpits.scripts.data_sync dump-bin --mode daily --start-date 20260420 --end-date 20260427
```

增量 bin 使用完整历史交易日历计算索引，`--start-date/--end-date` 只限制写入窗口，不截断 `day.txt`。

## 历史缺口修复

发现日频文件缺失：

```bash
python -m quantpits.scripts.data_sync sync-daily --repair
python -m quantpits.scripts.data_sync dump-pit
python -m quantpits.scripts.data_sync dump-bin --mode daily --start-date 20240115 --end-date 20240115
```

大范围缺口一键修复：

```bash
python -m quantpits.scripts.data_sync sync-all --repair
```

## crontab 示例

```cron
# 日频分层同步
35 15 * * * cd /path/to/Quant_Pits && python -m quantpits.scripts.data_sync sync-daily --tier post_market
35 19 * * * cd /path/to/Quant_Pits && python -m quantpits.scripts.data_sync sync-daily --tier capital_flow
35 20 * * * cd /path/to/Quant_Pits && python -m quantpits.scripts.data_sync sync-daily --tier evening

# 事件、PIT、bin
00 08 * * * cd /path/to/Quant_Pits && python -m quantpits.scripts.data_sync sync-event --mode daily
30 08 * * * cd /path/to/Quant_Pits && python -m quantpits.scripts.data_sync dump-pit
40 08 * * * cd /path/to/Quant_Pits && python -m quantpits.scripts.data_sync dump-bin --mode daily --start-date YYYYMMDD --end-date YYYYMMDD
```

## 指定股票生成

仅生成指定股票的 PIT 和 bin（常用于调试或快速验证）：

```bash
python -m quantpits.scripts.data_sync dump-pit --stocks 600519.SH 000001.SZ
python -m quantpits.scripts.data_sync dump-bin --mode full --stocks 600519.SH 000001.SZ
```

一键模式也支持：

```bash
python -m quantpits.scripts.data_sync sync-all --mode full --stocks 600519.SH 000001.SZ 000333.SZ
```

## 环境变量

```bash
export TUSHARE_TOKEN=your_token
export QLIB_WORKSPACE_DIR=/path/to/workspaces/LGBM_ALSTM_Workspace
# 可选：自定义项目级 raw 目录
export QUANTPITS_RAW_DIR=/path/to/data/raw
```

## 检查点

运行后确认：

- `stk_factor_pro` 已到最新交易日。
- 各 tier 最新日期符合预期。
- `financial/` 下有 PIT 文件。
- `features/` 下有对应股票和字段 bin。

## 速查表

| 场景 | 命令 |
| --- | --- |
| 查看状态 | `status` |
| 每日增量 | `sync-all` |
| 全量重建 | `sync-all --mode full` |
| 修复日频缺口 | `sync-all --repair` |
| 只同步日频 | `sync-daily --mode daily` |
| 只修复日频 | `sync-daily --repair` |
| 只同步事件 | `sync-event --mode daily` |
| 生成 PIT | `dump-pit` |
| 生成指定股票 PIT | `dump-pit --stocks 600519.SH` |
| 全量生成 bin | `dump-bin --mode full` |
| 生成指定股票 bin | `dump-bin --mode full --stocks 600519.SH` |
| 增量生成 bin | `dump-bin --mode daily --start-date YYYYMMDD --end-date YYYYMMDD` |

> 命令均需加 `python -m quantpits.scripts.data_sync` 前缀。
