# data_sync 模块 Qlib Bin 生成审计报告

日期：2026-04-30

## 结论摘要

- **合规性（Qlib Bin格式）整体基本合规**：`BinWriter` 采用小端 `float32` 序列，首值为 `start_index`，后接日频值，符合项目内定义的 Qlib bin 约定。 
- **存在若干边界风险**：主要集中在索引计算、文件长度校验与日历索引查找效率上。
- **性能方面存在明确可优化点**：尤其是日历索引 O(n) 查找、事件数据全量拼接、逐股票逐字段串行写入带来的 CPU/IO 开销。

## 合规性审计

### 1) Bin 文件结构与写入逻辑

- 文件格式定义清晰：`[start_index, v0, v1, ...]`，并采用 `float32` 小端写入。`write_new`/`append_fast`/`rewrite_with_merge` 三路径均围绕该格式实现。 
- 追加逻辑中要求 `old_end_index == new_start_index`，避免中间空洞；否则进入重写合并路径，策略是“新值优先、旧值兜底”。 
- 损坏文件检测：对小于最小长度（2个float）或 metadata 解析失败的文件，执行删除重建，具备一定容错。

**判断**：满足本项目对 Qlib 日频 bin 的工程实现约束。

### 2) 日历与索引一致性

- `generate_bins_per_stock` 会把索引日历裁剪到 `[data_start, data_end]`，并据此计算 `start_index`，避免把未同步历史写进索引区间。
- `write_calendar_files` 同时写 `day.txt`（历史）与 `day_future.txt`（含未来已知交易日），适配未来交易日查询场景。

**风险点**：`_find_start_index` 通过 `calendar.index(date)` 查找索引，复杂度 O(n)，在大规模逐字段写入时会产生重复线性扫描。

### 3) 字段映射与事件语义

- `FieldMapping` 区分 `daily/indicator/event_day_only/pit` 等 alignment；`pit` 字段被显式排除在 bin 日频写入之外，语义一致。
- indicator 字段在最终表里统一 `fillna(0)`，公告日/非公告日语义明确。

**潜在合规问题（轻微）**：`compute_bin_values` 对 expression 采用 `eval` 执行表达式，虽然禁用了 `__builtins__`，但仍属于动态求值路径，建议改为白名单算子解析器（如 numexpr/自定义 AST 解释）以提高可控性。

## 性能审计

### 1) 明确存在的性能问题

1. **日历索引查找重复 O(n)**
   - 每次写字段都会调用 `_find_start_index`，内部使用 `list.index`。
   - 在“股票数 × 字段数”规模下，重复线性查找成本可观。

2. **事件数据读取为“全量日期逐文件拼接”**
   - `_load_raw_events` 对接口所有同步日期逐文件读取并 concat。
   - 在长历史或高频增量运行中，内存峰值与 IO 时间上升明显。

3. **逐股票×逐字段串行写入，目录与文件频繁打开关闭**
   - 当前为单线程串行路径，且每个字段独立触发文件写逻辑。
   - 在 SSD 环境也会出现小文件高频写入放大问题。

4. **表达式字段逐股票重复 `eval`**
   - 若映射中 expression 多，解释执行开销明显。

### 2) 优化优先级建议

- **P0（应优先）**
  1. 预构建 `calendar_index_map = {date: idx}`，替换 `_find_start_index` 的线性查找。
  2. `_load_raw_events` 增加日期范围过滤参数（至少支持 `start_date/end_date`），减少无关文件扫描。

- **P1（建议）**
  1. 对 expression 预编译（`compile`）并复用，或迁移到 numexpr。
  2. 写入阶段按股票批处理+并行（进程池）并控制并发度，降低 wall time。

- **P2（可选）**
  1. 统一 parquet 读取列裁剪（仅读取必要列：`ts_code/trade_date` + mapping依赖列）。
  2. 建立增量 watermark，避免每次事件口径全历史重扫。

## 风险等级

- **合规风险**：低（格式及语义总体一致）。
- **性能风险**：中（数据规模增长后运行时间和内存占用将明显上升）。

## 建议落地顺序（两周内）

1. 首先落地日历索引映射与事件日期范围过滤（投入小、收益高）。
2. 其次处理 expression 执行与并行写入策略。
3. 最后补充基准测试（10年全市场、1年增量）并建立回归阈值。
