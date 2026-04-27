# 5模型融合工作空间设计文档

## Overview

将 LGBM_ALSTM_Workspace 从当前的2模型（LGBM+ALSTM，共用39因子）升级为5模型融合，基于 `data_preparation_refactor` spec 定义的44个bin字段和46个特征表达式，引入两个正交的特征空间（截面增强66因子 + 时序展开390维），实现模型架构多样性和特征空间多样性的双重融合。

**核心设计决策：**
- 不使用 Qlib 标准 Alpha158/Alpha360 handler，改用 DataHandlerLP + QlibDataLoader 全自定义表达式
- 截面模型（LGBM/XGBoost/Linear）使用66个截面因子
- 时序模型（ALSTM/GRU）使用13个核心字段 × 30天 = 390维时序展开
- 融合策略：ICIR加权（默认）、动态权重、分组融合共4个combo

---

## Architecture

```
特征空间1: 截面增强66因子              特征空间2: 时序展开390维
(spec 46表达式 + Alpha158补充20个)     (13字段 × 30天历史窗口)
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
Qlib bin文件 (features/{stock}/*.day.bin, 44字段)
    → DataHandlerLP + QlibDataLoader (实时计算特征表达式)
    → 截面模型: QlibDataLoader 66表达式 + ProcessInf/Fillna
    → 时序模型: QlibDataLoader 390时序表达式 + RobustZScoreNorm/Fillna
    → 各模型训练 → SignalRecord → 预测输出
    → Ensemble Fusion (Z-Score归一化 + 权重计算 + 信号融合)
    → 回测 TopK-DropN
```

---

## Feature Spaces

### 空间1：截面增强因子 ~66维（树模型 + 线性模型）

基于 data_preparation_refactor spec 第6.7节的46个表达式，叠加 Alpha158 中有价值但 spec 未覆盖的20个算子。

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

**总计：46 + 20 = 66个截面因子**

### 空间2：时序展开 ~390维（ALSTM + GRU）

从44个bin字段中选取13个最核心的，各展开30天历史窗口：

| 类别 | 字段 | 归一化方式 |
|------|------|-----------|
| 价格 | close | `Ref($close, d)/$close` |
| 成交量 | volume | `Ref($volume, d)/($close+1e-8)` |
| 换手率 | turnover_rate | `Ref($turnover_rate, d)` |
| 资金流 | net_mf_amount | `Ref($net_mf_amount, d)/($circ_mv+1e-8)` |
| 资金流 | buy_elg_amount | `Ref($buy_elg_amount, d)/($circ_mv+1e-8)` |
| 资金流 | sell_elg_amount | `Ref($sell_elg_amount, d)/($circ_mv+1e-8)` |
| 融资 | rzye | `Ref($rzye, d)/($circ_mv*10000+1e-8)` |
| 融资 | rqye | `Ref($rqye, d)/($circ_mv*10000+1e-8)` |
| 筹码 | winner_rate | `Ref($winner_rate, d)` |
| 估值 | pe_ttm | `Ref(1/($pe_ttm+1e-8), d)` |
| 估值 | pb | `Ref($pb, d)` |
| 市值 | circ_mv | `Ref($circ_mv, d)/($circ_mv+1e-8)` |
| 连涨 | updays | `Ref($updays, d)` |

**13字段 × 30天 = 390维**

---

## Model Matrix

| # | 模型名 | 算法 | 特征空间 | 维度 | 预计训练 |
|---|--------|------|----------|------|---------|
| 1 | lgbm_66factor | LightGBM | 截面增强66 | 66 | ~25min |
| 2 | xgboost_66factor | XGBoost | 截面增强66 | 66 | ~30min |
| 3 | linear_66factor | Ridge | 截面增强66 | 66 | ~5min |
| 4 | alstm_ts390 | ALSTM | 时序展开390 | 390 | ~60min |
| 5 | gru_ts390 | GRU | 时序展开390 | 390 | ~50min |
| **总计** | | | | | **~2.8h** |

### 超参数对比

| 参数 | lgbm_66factor | xgboost_66factor | linear_66factor | alstm_ts390 | gru_ts390 |
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
| all5_icir | 全部5个 | icir_weighted | ✅ | 日常交易主combo |
| all5_dynamic | 全部5个 | dynamic | | 市场风格切换时自适应 |
| tree3_icir | lgbm+xgb+linear | icir_weighted | | 诊断：排除时序模型后对比 |
| seq2_equal | alstm+gru | equal | | 诊断：时序模型独立贡献评估 |

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
| `config/workflow_config_lgbm_66factor_weekly.yaml` | LGBM + 66截面因子 |
| `config/workflow_config_xgboost_66factor_weekly.yaml` | XGBoost + 66截面因子 |
| `config/workflow_config_linear_66factor_weekly.yaml` | Ridge + 66截面因子 |
| `config/workflow_config_alstm_ts390_weekly.yaml` | ALSTM + 390时序展开 |
| `config/workflow_config_gru_ts390_weekly.yaml` | GRU + 390时序展开 |

### 更新文件

| 文件 | 变更 |
|------|------|
| `config/model_registry.yaml` | 5模型注册 |
| `config/ensemble_config.json` | 4个融合combo |
| `config/model_config.json` | experiment_name_prefix → "5model_weekly" |

### 删除文件

- `config/workflow_config_lgbm_alpha158_weekly.yaml`（被 lgbm_66factor 替代）
- `config/workflow_config_alstm_alpha360_weekly.yaml`（被 alstm_ts390 + gru_ts390 替代）

---

## Key Design Decisions

1. **不用 Alpha158/360 handler**：Spec的46表达式覆盖了A股特有因子（资金流、融资融券、筹码、事件），这些Alpha158 handler不提供。通过 DataHandlerLP + QlibDataLoader 全自定义，统一管理所有表达式。

2. **截面66 vs 时序390正交**：截面模型从工程特征中学习因子→收益的映射，时序模型从原始序列中自主学习模式。两者对市场的"观察方式"根本不同，是融合收益的核心来源。

3. **保留线性模型**：作为诊断工具——如果LGBM/XGBoost的IC不超过线性模型，说明66因子中没有可利用的非线性关系，需要重新审视因子质量。

4. **GRU与ALSTM互补**：ALSTM的注意力机制可能过度聚焦少数时间步，GRU的无注意力架构提供不同的预测模式。

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
- 66个截面因子表达式数量验证
- 390个时序因子表达式数量验证（13 × 30 = 390）

### 属性测试（Property-based）

- `inject_config` 周频标签注入（Property 1）
- 截面模型 infer_processors 完整性（Property 2）
- 时序模型 d_feat 一致性（Property 3）
- Z-Score 归一化逐日均值（Property 4）

### 集成测试（需完整 Qlib 环境）

- `static_train.py --models lgbm_66factor` 单模型训练成功
- `static_train.py --full` 5模型依次训练成功
- `ensemble_fusion.py --from-config-all` 全部4个combo融合成功
- 5模型融合回测Sharpe > 单模型最佳Sharpe
