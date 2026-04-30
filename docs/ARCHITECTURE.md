# QuantPits 系统架构文档

## 1. 系统定位

QuantPits 是基于 Microsoft Qlib 构建的**生产级量化交易系统**，支持周频/日频的端到端量化投资流水线。核心特性包括高度模块化、多 Workspace 隔离运行、模型融合（Ensemble）、执行归因分析，以及交互式 Streamlit 可视化面板。

---

## 2. 核心设计原则：Engine + Workspace 分离

系统严格分离了**引擎代码**与**工作区数据/配置**，实现多实例并行隔离：

```
QuantPits/
├── quantpits/          # Engine: 全局共用代码（Scripts + Utils + Tools）
├── workspaces/         # Workspace: 各实例独立的配置、数据、输出
│   └── Demo_Workspace/  # 示例工作区
├── ui/                 # 可视化面板
└── docs/               # 文档
```

### 2.1 Workspace 目录结构

每个 Workspace 是完全隔离的"交易控制台"，包含：

| 目录/文件 | 说明 |
|---|---|
| `config/` | 模型注册表(model_registry.yaml)、策略配置(strategy_config.yaml)、回测配置 |
| `data/` | 持仓历史、交易日志、资金曲线 |
| `output/` | 预测结果、融合结果、回测报告 |
| `mlruns/` | MLflow 追踪日志 |
| `run_env.sh` | 环境变量脚本（设置 QLIB_WORKSPACE_DIR, QLIB_DATA_DIR 等) |

### 2.2 环境初始化流程

**所有脚本必须在开头执行 `from quantpits.utils import env`**：

1. `env.py` 读取 `--workspace` 参数或 `QLIB_WORKSPACE_DIR` 环境变量
2. 设置 `ROOT_DIR` → 所有路径基于此解析
3. 配置 `MLFLOW_TRACKING_URI` 指向 Workspace 内的 `mlruns/`
4. `env.init_qlib()` 根据 `QLIB_DATA_DIR` / `QLIB_REGION` 初始化 Qlib

---

## 3. 核心模块依赖图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        QuantPits 架构分层                                │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐                  │
│  │ UI 层        │   │ Scripts 层   │   │ Config 层   │                  │
│  │ dashboard.py│   │ static_train │   │ model_reg   │                  │
│  │ rolling_dash │   │ ensemble_fus │   │ strategy_   │                  │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘                  │
│         │                  │                  │                         │
│         ▼                  ▼                  ▼                         │
│  ┌─────────────────────────────────────────────────────────┐            │
│  │              Utils 层（共享库）                           │            │
│  │  env │ train_utils │ fusion_engine │ config_loader      │            │
│  │  backtest_utils │ strategy │ predict_utils │ ...         │            │
│  └────────────────────────────┬────────────────────────────┘            │
│                                │                                         │
│                                ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐            │
│  │              Qlib / 第三方依赖                           │            │
│  │  qlib │ pandas │ numpy │ yaml │ mlflow │ streamlit    │            │
│  └─────────────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 核心模块详解

### 4.1 quantpits/scripts/ — 流水线脚本矩阵

| 模块 | 文件 | 职责 |
|---|---|---|
| **训练** | `static_train.py` | 统一训练入口：全量训练、增量训练、仅预测 |
| **训练** | `rolling_train.py` | 滚动训练：冷启动、滚动预测、断点恢复 |
| **融合** | `ensemble_fusion.py` | 多模型融合：等权/ICIR加权/动态权重 |
| **融合** | `brute_force_ensemble.py` | GPU加速穷举组合优选 |
| **融合** | `minentropy_ensemble.py` | 最小熵融合 |
| **交易** | `prod_post_trade.py` | 实盘后处理：解析交割单、更新持仓/现金 |
| **交易** | `order_gen.py` | 订单生成：TopK/DropN 买卖建议 |
| **分析** | `run_analysis.py` | 单模型/融合分析 |
| **深度分析** | `run_deep_analysis.py` | 多 Agent 协同深度分析（MAS 系统） |
| **信号** | `signal_ranking.py` | 归一化分数排名输出 |

#### 主要流水线依赖顺序

```
① static_train --full          # 全量训练
   └──→ ① static_train --predict-only --all-enabled  # 增量预测
            └──→ ③ ensemble_fusion                   # 融合预测
                     └──→ ④ prod_post_trade          # Post-Trade
                              └──→ ⑤ order_gen       # 订单生成
```

### 4.2 quantpits/utils/ — 共享工具库

| 文件 | 职责 |
|---|---|
| `env.py` | **根路径解析、Workspace 检测、Qlib 初始化** |
| `train_utils.py` | **核心训练函数 `train_single_model()`**、日期计算、YAML注入、模型注册表管理 |
| `fusion_engine.py` | 融合权重计算（equal/ICIR/manual/dynamic） |
| `config_loader.py` | 统一配置加载器：合并 model_config.json / strategy_config.yaml / prod_config.json |
| `backtest_utils.py` | 回测通用工具 |
| `strategy.py` | 策略执行逻辑（TopK/DropN） |
| `predict_utils.py` | 预测结果处理 |
| `ensemble_utils.py` | Ensemble 融合工具 |
| `constants.py` | 全局常量（交易日/年、周数、无风险利率等） |

### 4.3 quantpits/scripts/brokers/ — 券商适配器

```
brokers/
├── base.py          # BaseBrokerAdapter 抽象类，定义统一 Schema
│                    # SELL_TYPES / BUY_TYPES / INTEREST_TYPES 标准交易类别
│                    # REQUIRED_COLUMNS 标准列名
└── gtja.py          # 国泰君安券商适配器实现
```

适配器职责：将不同券商各异的 XLSX/CSV 导出格式清洗为统一内部 DataFrame，实现与实盘交易模块的解耦。

### 4.4 quantpits/scripts/analysis/ — 分析模块

| 类 | 职责 |
|---|---|
| `PortfolioAnalyzer` | 资产组合表现分析：收益、夏普、最大回撤、Barra 归因 |
| `ExecutionAnalyzer` | 微观执行损耗分析：价差滑点、延时成本 |
| `EnsembleAnalyzer` | 融合模型分析 |
| `SingleModelAnalyzer` | 单模型分析 |
| `TradeClassifier` | 交易分类（买入/卖出/红利等） |

`utils.py` 提供 `init_qlib()` / `load_market_config()` 等初始化函数。

### 4.5 quantpits/scripts/deep_analysis/ — MAS 深度分析系统

```
deep_analysis/
├── coordinator.py        # 多 Agent 协调器，扫描工作区数据、调度 Agent
├── base_agent.py         # BaseAgent 抽象基类
├── llm_interface.py      # LLM 接口封装
├── report_generator.py   # 分析报告生成
├── synthesizer.py        # 多 Agent 结果综合
├── config_ledger.py      # 配置账本
└── agents/
    ├── ensemble_eval.py       # 融合评估 Agent
    ├── execution_quality.py   # 执行质量 Agent
    ├── market_regime.py       # 市场状态 Agent
    ├── model_health.py        # 模型健康 Agent
    ├── portfolio_risk.py      # 组合风险 Agent
    ├── prediction_audit.py    # 预测审计 Agent
    └── trade_pattern.py       # 交易模式 Agent
```

### 4.6 quantpits/tools/ — 独立工具

| 文件 | 职责 |
|---|---|
| `init_workspace.py` | 创建新工作区（从源克隆） |
| `check_workflow_yaml.py` | 检查 workflow YAML 配置 |
| `archive_dated_files.py` | 归档带日期的文件 |
| `plot_model_opinions.py` | 可视化模型观点分歧 |
| `classify_history.py` | 历史数据分类 |

### 4.7 ui/ — Streamlit 可视化面板

| 文件 | 职责 |
|---|---|
| `dashboard.py` | 宏观资产组合业绩评估面板 |
| `rolling_dashboard.py` | 时序策略执行健康监测面板 |

两个面板都依赖 `PortfolioAnalyzer` 和 `ExecutionAnalyzer`。

---

## 5. 配置系统

### 5.1 核心配置文件

| 文件 | 位置 | 说明 |
|---|---|---|
| `model_registry.yaml` | Workspace/config/ | 模型注册表：算法、数据集、市场、YAML路径、启用状态 |
| `model_config.json` | Workspace/config/ | 基础环境参数：市场、基准、TopK、训练日期范围 |
| `strategy_config.yaml` | Workspace/config/ | 策略单一数据源：TopK、DropN、买入建议因子 |
| `prod_config.json` | Workspace/config/ | 生产状态：当前日期、持仓、现金 |
| `ensemble_config.json` | Workspace/config/ | 融合配置：候选组合、默认组合 |
| `rolling_config.yaml` | Workspace/config/ | 滚动训练配置：滚动窗口、验证期 |
| `workflow_config_*.yaml` | Workspace/config/ | Qlib 工作流配置：训练/预测参数 |
| `cashflow.json` | Workspace/config/ | 资金流水记录 |

### 5.2 配置加载流程

`config_loader.load_workspace_config()` 统一合并三层配置：

```
model_config.json  →  基础环境参数（市场、基准、TopK）
       ↓
strategy_config.yaml  →  策略参数（优先级更高）
       ↓
prod_config.json  →  生产状态（当前持仓、现金、日期）
```

---

## 6. 数据流

### 6.1 训练 → 预测 → 融合数据流

```
Qlib Data (provider_uri)
        │
        ▼
┌───────────────────┐
│  static_train.py  │
│  (Qlib DatasetH)  │
└────────┬──────────┘
         │ predictions
         ▼
output/predictions/*.csv
         │
         ▼
┌─────────────────────┐
│ ensemble_fusion.py  │  ← ensemble_config.json
└────────┬────────────┘
         │ fusion predictions
         ▼
output/ensemble/*.csv
```

### 6.2 交易闭环数据流

```
融合预测
    │
    ▼
┌───────────────────┐     ┌────────────────────┐
│ prod_post_trade   │────▶│ prod_config.json   │
│ (解析交割单)       │     │ (更新持仓/现金)     │
└───────────────────┘     └─────────┬──────────┘
                                    │
                                    ▼
                           ┌────────────────────┐
                           │ order_gen.py        │
                           │ (TopK/DropN 订单)   │
                           └────────────────────┘
```

---

## 7. 关键依赖关系总结

```
env.py
  ├── ROOT_DIR (from --workspace arg or QLIB_WORKSPACE_DIR env)
  ├── QLIB_DATA_DIR / QLIB_REGION (from env vars, defaults: ~/.qlib/qlib_data/cn_data, cn)
  ├── MLFLOW_TRACKING_URI (→ Workspace/mlruns/)
  └── init_qlib() ──────────────────────────────────────────┐
                                                           │
static_train.py ───────────▶ train_utils.train_single_model()│
                                   │                        │
                                   ├── train_utils.py        │
                                   ├── config_loader.py ────┘
                                   │
ensemble_fusion.py ───▶ fusion_engine.calculate_weights()──┘
                            │
                            ├── train_utils.py
                            └── config_loader.py

prod_post_trade.py ────▶ brokers.get_adapter() ──▶ BaseBrokerAdapter
                               │
                               └── gtja.py (国泰君安实现)

order_gen.py ──────────▶ strategy.py (TopK/DropN 逻辑)

ui/dashboard.py ────────▶ PortfolioAnalyzer / ExecutionAnalyzer
                               │
                               └── analysis/utils.py ──▶ init_qlib()

run_deep_analysis.py ──▶ coordinator.py ──▶ agents/* (7个Agent)
```

---

## 8. 快速参考：流水线命令

```bash
# 激活工作区
source workspaces/Demo_Workspace/run_env.sh

# 流水线
python -m quantpits.scripts.static_train --full                  # ①全量训练
python -m quantpits.scripts.static_train --predict-only --all-enabled  # ②增量预测
python -m quantpits.scripts.ensemble_fusion --from-config-all    # ③融合
python -m quantpits.scripts.prod_post_trade                       # ④Post-Trade
python -m quantpits.scripts.order_gen                              # ⑤订单生成

# 可视化
streamlit run ui/dashboard.py
streamlit run ui/rolling_dashboard.py
```

---

*文档版本：基于 QuantPits 最新代码仓分析生成*
