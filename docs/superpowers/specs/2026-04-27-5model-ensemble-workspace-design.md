# 5模型融合工作空间设计文档

## Overview

将 LGBM_ALSTM_Workspace 从当前的2模型（LGBM+ALSTM，共用39因子）升级为5模型融合，基于 `data_preparation_refactor` spec 定义的44个bin字段和46个特征表达式，**新增11个短线专用bin字段（零API成本）→ 总计55个bin字段**，引入两个正交的特征空间（截面增强~79因子 + 时序展开390维），实现模型架构多样性和特征空间多样性的双重融合。

**核心设计决策：**
- 不使用 Qlib 标准 Alpha158/360 handler，改用 DataHandlerLP + QlibDataLoader 全自定义表达式
- **11个零成本bin字段**：从已拉取的raw数据中新增映射（资金流4+融资2+筹码3+股本1+涨跌幅1），聚焦短线信号
- 截面模型（LGBM/XGBoost/Linear）使用~79个截面因子（原66 + 短线增强13）
- 时序模型（ALSTM/GRU）使用13个核心字段 × 30天 = 390维时序展开（短线优化选股）
- 融合策略：ICIR加权（默认）、动态权重、分组融合共4个combo

---

## Architecture

```
特征空间1: 截面增强79因子                    特征空间2: 时序展开390维
(46 Spec + 20 Alpha158 + 13短线增强)    (13字段短线优化 × 30天历史窗口)
    │                                       │
    ├── LGBModel (num_leaves=63)            ├── ALSTM (hidden=128, attention)
    ├── XGBModel (max_depth=5)             └── GRU   (hidden=128, no attention)
    └── LinearModel (Ridge, alpha=1.0)
    │                                       │
    └──────────── Ensemble Fusion ──────────┘
         │              │            │
    all5_icir     all5_dynamic   tree3_icir / seq2_equal
```

**数据流：**

```
Qlib bin文件 (features/{stock}/*.day.bin, 55字段)
    → DataHandlerLP + QlibDataLoader (实时计算特征表达式)
    → 截面模型: QlibDataLoader 79表达式 + ProcessInf/Fillna
    → 时序模型: QlibDataLoader 390时序表达式 + RobustZScoreNorm/Fillna
    → 各模型训练 → SignalRecord → 预测输出
    → Ensemble Fusion (Z-Score归一化 + 权重计算 + 信号融合)
    → 回测 TopK-DropN
```

---

## Feature Spaces

### 新增11个bin字段（零成本，聚焦短线）

以下字段已在 data_sync fetcher 中拉取并存储，仅需新增 `field_mapping.yaml` 映射即可进入bin：

| bin字段 | 来源接口 | 来源列 | 短线逻辑 |
|---------|---------|--------|---------|
| `buy_lg_amount` | moneyflow | buy_lg_amount | 特大单买入——机构/主力进场信号 |
| `sell_lg_amount` | moneyflow | sell_lg_amount | 特大单卖出——主力离场信号 |
| `buy_md_amount` | moneyflow | buy_md_amount | 中单买入——散户参与度指标 |
| `sell_md_amount` | moneyflow | sell_md_amount | 中单卖出 |
| `rzmre` | margin_detail | rzmre | 当日融资买入额——短线多头最敏感的杠杆情绪 |
| `rzche` | margin_detail | rzche | 当日融资偿还额——配合买入额判断净方向 |
| `cost_15pct` | cyq_perf | cost_15pct | 廉价筹码边界——筹码集中度下沿 |
| `cost_50pct` | cyq_perf | cost_50pct | 市场平均持仓成本——短线博弈核心锚点 |
| `cost_85pct` | cyq_perf | cost_85pct | 套牢盘边界——上方抛压强度 |
| `free_share` | stk_factor_pro | free_share | 实际可交易股本（剔除大股东锁定） |
| `pct_chg` | stk_factor_pro | pct_chg | 当日涨跌幅——涨停板博弈基础 |

**bin字段总计：44（原spec） + 11（本次新增） = 55个**

### 空间1：截面增强因子 ~79维（树模型 + 线性模型）

基于 data_preparation_refactor spec 第6.7节的46个表达式，叠加 Alpha158 补充20个，再叠加本次11个bin字段带来的短线增强13个表达式。

#### A-M组（46个，Spec定义）

| 组 | 内容 | 数量 |
|----|------|------|
| A 价格动量 | `$close/Ref($close,N)-1`, N∈{1,5,10,20,60} | 5 |
| B 均线偏离 | `Mean($close,N)/$close-1`, N∈{5,10,20,60} | 4 |
| C 波动率 | `Std($close/Ref($close,1)-1,N)`, N∈{5,10,20} | 3 |
| D 成交量 | `$volume/Mean($volume,N)-1` 等 | 3 |
| E 换手率 | `$turnover_rate`, 换手率偏离, `$turnover_rate_f`, `$volume_ratio` | 4 |
| F 资金流 | 大单净流入/市值, 大单净买卖强度等 | 5 |
| G 估值 | `1/($pe_ttm+1e-8)`, `$pb`, `$ps_ttm`, `$dv_ttm` | 4 |
| H 市值 | `Log($circ_mv)`, `$circ_mv/$total_mv` | 2 |
| I 融资融券 | `$rzye/($circ_mv*10000)`, `$rqye/($circ_mv*10000)` | 2 |
| J 筹码分布 | `$winner_rate`, 筹码价差, 价格偏离平均成本 | 3 |
| K 涨跌停 | 距涨停距离, 距跌停距离 | 2 |
| L 连涨连跌 | `$updays/5`, `$downdays/5`, `1/($topdays+1)`, `$lowdays/20` | 4 |
| M 事件标志 | `$holder_change_flag`, `$float_flag`, `$forecast_flag`, `$express_flag` | 4 |

#### N组（20个，Alpha158补充）

- BETA(5/20/60天趋势斜率) ×3：`Slope($close,N)/$close`
- RSQR(5/20/60天趋势拟合度) ×3：`Rsquare($close,N)`
- RESI(5/20/60天线性偏离) ×3：`Resi($close,N)/$close`
- RSV(5/20/60天相对位置) ×3：`($close-Min($low,N))/(Max($high,N)-Min($low,N)+1e-12)`
- RANK(20/60天价格分位) ×2：`Rank($close,N)`
- MAX/MIN(20天极值比) ×2：`Max($high,20)/$close`, `Min($low,20)/$close`
- QTLU(20天80%分位) ×1：`Quantile($close,20,0.8)/$close`
- K线形态(KMID/KUP/KSFT) ×3

#### O组（13个，短线增强 — 基于11个新bin字段）

资金流增强：
- `($buy_lg_amount-$sell_lg_amount)/($circ_mv+1e-8)` — 特大单净强度
- `($buy_lg_amount+$buy_elg_amount)/($buy_lg_amount+$buy_elg_amount+$sell_lg_amount+$sell_elg_amount+1e-8)` — 大资金买入占比
- `$buy_md_amount/($buy_lg_amount+$buy_md_amount+1e-8)` — 散户主导程度(中单占比高=散户市)
- `($buy_lg_amount-$sell_lg_amount)/($buy_elg_amount+$sell_elg_amount+1e-8)` — 特大单vs大单方向背离(主力与机构分歧)

融资情绪增强：
- `($rzmre-$rzche)/($rzmre+$rzche+1e-8)` — 融资净买入方向
- `$rzmre/($circ_mv*10000+1e-8)` — 融资买入强度
- `$rzche/($rzye+1e-8)` — 融资偿还率(高位=获利了结)

筹码增强：
- `($close/$factor)/$cost_50pct-1` — 价格vs平均成本(替代weight_avg版本，cost_50pct更精确)
- `$cost_15pct/$cost_85pct` — 筹码集中度(→1=极度集中，筹码在窄区间)
- `($cost_50pct-$cost_15pct)/($cost_85pct-$cost_15pct+1e-8)` — 筹码分布偏度(<0.5=筹码偏下方=支撑强)

股本增强：
- `$circ_mv/($free_share+1e-8)` — 实际流通市价(元/股，区别于理论流通价)

涨跌幅增强：
- `$pct_chg` — 当日涨跌幅(涨停板博弈、极端值检测)

**总计：46(Spec) + 20(Alpha158) + 13(短线增强) = 79个截面因子**

### 空间2：时序展开 ~390维（ALSTM + GRU）— 短线优化版

从55个bin字段中选取13个短线最核心的，各展开30天历史窗口：

| 类别 | 字段 | 归一化方式 | 短线选股理由 |
|------|------|-----------|------------|
| 价格 | close | `Ref($close, d)/$close` | 价格序列基础 |
| 成交量 | volume | `Ref($volume, d)/($close+1e-8)` | 量价关系 |
| 换手率 | turnover_rate | `Ref($turnover_rate, d)` | 流动性——短线爆发前提 |
| 资金流 | net_mf_amount | `Ref($net_mf_amount, d)/($circ_mv+1e-8)` | 整体资金方向 |
| 资金流 | buy_lg_amount | `Ref($buy_lg_amount, d)/($circ_mv+1e-8)` | **特大单买入**——机构进场(替换原buy_elg) |
| 资金流 | sell_lg_amount | `Ref($sell_lg_amount, d)/($circ_mv+1e-8)` | **特大单卖出**——主力离场(替换原sell_elg) |
| 融资 | rzye | `Ref($rzye, d)/($circ_mv*10000+1e-8)` | 融资余额——杠杆存量 |
| 融资 | rzmre | `Ref($rzmre, d)/($circ_mv*10000+1e-8)` | **融资买入流量**——每日杠杆情绪(替换原rqye) |
| 筹码 | winner_rate | `Ref($winner_rate, d)` | 获利盘比例——抛压判断 |
| 筹码 | cost_50pct | `Ref($cost_50pct, d)` | **市场平均成本**——博弈锚点(替换原pb) |
| 估值 | pe_ttm | `Ref(1/($pe_ttm+1e-8), d)` | 估值锚 |
| 市值 | circ_mv | `Ref($circ_mv, d)/($circ_mv+1e-8)` | 盘子大小 |
| 连涨 | updays | `Ref($updays, d)` | 短期趋势强度 |

**短线优化：buy_elg→buy_lg, sell_elg→sell_lg, rqye→rzmre, pb→cost_50pct** — 4个交换把时序空间从"通用A股"调整为"短线博弈"。

**13字段 × 30天 = 390维**

---

## Model Matrix

| # | 模型名 | 算法 | 特征空间 | 维度 | 预计训练 |
|---|--------|------|----------|------|---------|
| 1 | lgbm_79factor | LightGBM | 截面增强79 | 79 | ~25min |
| 2 | xgboost_79factor | XGBoost | 截面增强79 | 79 | ~30min |
| 3 | linear_79factor | Ridge | 截面增强79 | 79 | ~5min |
| 4 | alstm_ts390 | ALSTM | 时序展开390 | 390 | ~60min |
| 5 | gru_ts390 | GRU | 时序展开390 | 390 | ~50min |
| **总计** | | | | | **~2.8h** |

### 超参数对比

| 参数 | lgbm_79factor | xgboost_79factor | linear_79factor | alstm_ts390 | gru_ts390 |
|------|-------------|-----------------|-----------------|-------------|----------|
| 关键结构 | num_leaves=63 | max_depth=5 | alpha=1.0 | hidden=128, attention | hidden=128, no attention |
| learning_rate | 0.05 | 0.05 | 0.001 | 0.001 | 0.001 |
| n_estimators/epochs | 250 | 200 | 500 | 200 | 200 |
| 正则化 | L1=0.1, L2=0.5 | L1=0.1, L2=1.0 | L2=1.0 | dropout=0.2 | dropout=0.2 |
| early_stop | - | - | 20 | 20 | 20 |

### 模型差异化设计

- **lgbm vs xgboost**：LGBM叶子多(63)、L2轻(0.5) → 拟合能力更强；XGBoost深度浅(5)、L2重(1.0) → 更稳健保守
- **alstm vs gru**：ALSTM含注意力机制 → 选择性关注关键时间步；GRU纯循环 → 平等对待所有时间步，不同的归纳偏置
- **linear**：纯线性基线 → 用于检测非线性模型是否有超额收益

---

## Ensemble Combos

| Combo | 模型范围 | 方法 | 默认 | 用途 |
|-------|---------|------|------|------|
| all5_icir | lgbm_79factor + xgboost_79factor + linear_79factor + alstm_ts390 + gru_ts390 | icir_weighted | ✅ | 日常交易主combo |
| all5_dynamic | 同上 | dynamic | | 市场风格切换时自适应 |
| tree3_icir | lgbm_79factor + xgboost_79factor + linear_79factor | icir_weighted | | 诊断：排除时序模型后对比 |
| seq2_equal | alstm_ts390 + gru_ts390 | equal | | 诊断：时序模型独立贡献评估 |

`min_model_ic: 0.01` — IC低于此阈值的模型在ICIR加权中权重归零。

---

## Data Processing Pipeline

### 截面模型

```yaml
infer_processors:
    - class: ProcessInf    # ±∞ → NaN
    - class: Fillna        # NaN → 0
learn_processors:
    - class: DropnaLabel
    - class: CSRankNorm
      kwargs:
          fields_group: label
```

截面因子已是比值（去量纲），仅需处理异常值。

### 时序模型

```yaml
infer_processors:
    - class: RobustZScoreNorm
      kwargs:
          fields_group: feature
          clip_outlier: true
    - class: Fillna
learn_processors:
    - class: DropnaLabel
    - class: CSRankNorm
      kwargs:
          fields_group: label
```

时序原始值量纲差异大（元/万元/%混用），需RobustZScoreNorm强归一化。

### Filter Pipe（共用）

- 排除未复权价 < 80元的股票（过滤ST、低价股）
- 排除停牌股票（suspend < 1）

### Label（共用）

`Ref($close, -6) / Ref($close, -1) - 1` — 5日收益率，`ann_scaler: 52`

---

## File Changes

### 新增文件

| 文件 | 说明 |
|------|------|
| `config/workflow_config_lgbm_79factor_weekly.yaml` | LGBM + 79截面因子 |
| `config/workflow_config_xgboost_79factor_weekly.yaml` | XGBoost + 79截面因子 |
| `config/workflow_config_linear_79factor_weekly.yaml` | Ridge + 79截面因子 |
| `config/workflow_config_alstm_ts390_weekly.yaml` | ALSTM + 390时序展开 |
| `config/workflow_config_gru_ts390_weekly.yaml` | GRU + 390时序展开 |

### 更新文件

| 文件 | 变更 |
|------|------|
| `config/field_mapping.yaml` | 新增11个bin字段映射（资金流4+融资2+筹码3+股本1+涨跌幅1） |
| `config/model_registry.yaml` | 5模型注册（79截面×3 + 390时序×2） |
| `config/ensemble_config.json` | 4个融合combo |
| `config/model_config.json` | experiment_name_prefix → "5model_weekly" |

### 删除文件

- `config/workflow_config_lgbm_alpha158_weekly.yaml`（被 lgbm_79factor 替代）
- `config/workflow_config_alstm_alpha360_weekly.yaml`（被 alstm_ts390 + gru_ts390 替代）

---

## Key Design Decisions

1. **不用 Alpha158/360 handler**：Spec的46表达式覆盖了A股特有因子，通过 DataHandlerLP + QlibDataLoader 全自定义，统一管理所有表达式。

2. **短线导向的因子和选股**：11个新增bin字段全部聚焦短线信号——主力资金结构（buy_lg/sell_lg）、日内融资情绪（rzmre/rzche）、筹码分布（cost_50pct/15pct/85pct）、股本弹性（free_share）、涨跌停博弈（pct_chg）。时序模型选股也交换了4个核心字段（buy_elg→buy_lg, rqye→rzmre, pb→cost_50pct等），让RNN关注更敏感的短线信号。

3. **截面79 vs 时序390正交**：截面模型从工程特征中学习因子→收益的映射，时序模型从原始序列中自主学习短线模式。两者对市场的"观察方式"根本不同，是融合收益的核心来源。

4. **保留线性模型**：作为诊断工具——如果LGBM/XGBoost的IC不超过线性模型，说明79因子中没有可利用的非线性关系，需要重新审视因子质量。

5. **GRU与ALSTM互补**：ALSTM的注意力机制可能过度聚焦少数时间步，GRU的无注意力架构提供不同的预测模式。

---

## Correctness Properties

### Property 1: 周频标签注入

*For any* 有效的 workflow YAML（无论初始label内容），以 `freq: week` 调用 `inject_config` 时，label 必须为 `["Ref($close, -6) / Ref($close, -1) - 1]`，ann_scaler 必须为 `52`。

### Property 2: 截面模型 infer_processors 完整性

*For any* 截面模型 YAML，`infer_processors` 必须包含 `ProcessInf` 和 `Fillna`（精确顺序），不得包含 `RobustZScoreNorm`。

### Property 3: 时序模型 d_feat 一致性

*For any* 时序模型 YAML，`model.kwargs.d_feat` 必须等于 `390`（13字段 × 30天）。

### Property 4: Ensemble Z-Score 归一化

*For any* 多模型预测分值 DataFrame（datetime × instrument），逐日 Z-Score 归一化后每个交易日均值近似为0（< 1e-10），标准差近似为1（< 1e-6）。

---

## Error Handling

| 场景 | 处理 |
|------|------|
| bin字段缺失（如dv_ttm不在bin中） | QlibDataLoader 表达式返回NaN → Fillna填充为0 |
| 单个模型训练失败 | static_train.py 记录错误到 run_state.json，继续训练其他模型 |
| 融合时某模型预测缺失 | ensemble_fusion.py 跳过缺失模型并输出警告 |
| GPU不可用 | Qlib PyTorch模型自动回退CPU |
| workflow YAML 语法错误 | Qlib表达式引擎在训练启动时报错，指出具体表达式 |

---

## Testing Strategy

### 单元测试

- 5个 workflow YAML 的结构验证（model.class、d_feat、infer_processors、label、ann_scaler）
- model_registry.yaml 的5模型注册完整性
- ensemble_config.json 的4个combo结构验证
- 79个截面因子表达式数量验证
- 390个时序因子表达式数量验证（13 × 30 = 390）
- field_mapping.yaml 的55个bin字段完整性验证（44原有 + 11新增）

### 属性测试（Property-based）

- `inject_config` 周频标签注入（Property 1）
- 截面模型 infer_processors 完整性（Property 2）
- 时序模型 d_feat 一致性（Property 3）
- Z-Score 归一化逐日均值（Property 4）

### 集成测试（需完整 Qlib 环境）

- `static_train.py --models lgbm_79factor` 单模型训练成功
- `static_train.py --full` 5模型依次训练成功
- `ensemble_fusion.py --from-config-all` 全部4个combo融合成功
- 5模型融合回测Sharpe > 单模型最佳Sharpe
