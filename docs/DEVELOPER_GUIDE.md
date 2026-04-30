# QuantPits 开发指南

> 本文档面向 QuantPits 系统的开发者和使用者，涵盖项目架构、编码规范、工作区配置和扩展方法。

---

## 目录

1. [项目架构](#1-项目架构)
2. [编码规范](#2-编码规范)
3. [工作区配置](#3-工作区配置)
4. [训练流水线](#4-训练流水线)
5. [扩展指南](#5-扩展指南)
6. [常见问题](#6-常见问题)

---

## 1. 项目架构

### 1.1 核心设计：Engine 与 Workspace 分离

QuantPits 采用 **Engine + Workspace** 双层架构，实现多市场/多策略并行而互不干扰：

```
QuantPits/
├── quantpits/                  # Engine：全局共享的代码（不依赖具体 Workspace）
│   ├── scripts/               # 可执行入口脚本
│   ├── utils/                 # 共享工具模块
│   └── docs/                  # 系统文档
└── workspaces/                 # Workspace：每个实例独立的配置和数据
    └── <YourWorkspace>/
        ├── config/            # 模型注册、策略、日期配置
        ├── data/              # 持仓/交易日志/运行状态
        ├── output/            # 预测/融合/回测结果
        ├── mlruns/            # MLflow 训练追踪
        └── archive/           # 历史备份
```

**核心原则**：
- `quantpits/` 目录下所有代码**不包含**任何 Workspace 路径硬编码
- Workspace 路径通过 `env.py` 的 `QLIB_WORKSPACE_DIR` 环境变量动态寻址
- 所有脚本的输入输出以 `env.ROOT_DIR`（= `QLIB_WORKSPACE_DIR`）为根目录计算

### 1.2 无 `__init__.py` 的 Namespace Package

`quantpits/utils/` 目录下**没有** `__init__.py`，使用 Python namespace package 直接导入：

```python
from quantpits.utils import env              # ✅ 正确
from quantpits.utils.env import init_qlib    # ✅ 正确
from quantpits.utils.train_utils import train_single_model  # ✅ 正确
# from quantpits.utils import *              # ❌ 禁止使用
```

### 1.3 env.py 路径解析优先级

```python
# 优先级 1: 命令行 --workspace 参数
if _workspace_arg:
    ROOT_DIR = os.path.abspath(_workspace_arg)
# 优先级 2: 环境变量 QLIB_WORKSPACE_DIR
elif "QLIB_WORKSPACE_DIR" in os.environ:
    ROOT_DIR = os.path.abspath(os.environ["QLIB_WORKSPACE_DIR"])
# 优先级 3: 报错提示
else:
    raise RuntimeError("Please source a workspace run_env.sh first!")
```

### 1.4 共享工具模块

| 模块 | 职责 | 服务对象 |
|------|------|----------|
| `train_utils.py` | 日期计算、YAML 注入、模型注册、训练记录合并 | 训练、预测 |
| `predict_utils.py` | 预测数据加载/保存、Recorder 管理 | 预测、融合、穷举 |
| `config_loader.py` | Workspace 级配置加载 | 全局 |
| `strategy.py` | 策略配置/回测策略构建 | 穷举、融合、分析 |
| `backtest_utils.py` | Qlib 回测执行与评估 | 穷举、融合、分析 |
| `fusion_engine.py` | 权重计算、信号融合 | 融合 |
| `ensemble_utils.py` | Ensemble 配置解析、combo 管理 | 融合、信号排名、订单生成 |
| `search_utils.py` | 组合搜索共享逻辑 | 组合搜索、分析 |
| `run_context.py` | 运行输出路径管理 | 组合搜索、分析 |

---

## 2. 编码规范

### 2.1 导入规范（最重要）

**所有脚本的顶层必须首先导入 env**：

```python
#!/usr/bin/env python
"""
<脚本名> — <一句话描述>
"""

import os
import sys
import json
import argparse
from datetime import datetime

# === env 必须最早导入 ===
from quantpits.utils import env

# 后续导入
from quantpits.utils.train_utils import (
    load_model_registry, train_single_model, merge_train_records
)
```

**qlib 相关导入必须延迟到函数内部**（允许 `--list`、`--show-state` 等命令在无 qlib 环境下运行）：

```python
def calculate_dates():
    from qlib.data import D                        # ✅ 延迟导入
    from quantpits.utils.config_loader import load_workspace_config
    # ...

def train_single_model(...):
    from qlib.utils import init_instance_by_config  # ✅ 延迟导入
    from qlib.workflow import R
    # ...
```

**导入顺序**：标准库 → 第三方库 → quantpits 内部

### 2.2 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 公共函数 | `snake_case` | `train_single_model`, `merge_train_records` |
| 内部函数 | `_snake_case`（下划线前缀） | `_get_inner_model`, `_signal_handler` |
| 类 | `CamelCase` | `RollingState`, `OrderGenerator` |
| 模块级常量 | `UPPER_SNAKE_CASE` | `TRADING_DAYS_PER_YEAR`, `MODE_SEPARATOR` |
| 模块级变量 | `snake_case` | `SCRIPT_DIR`, `ROOT_DIR` |
| 配置路径常量 | 全大写 | `REGISTRY_FILE`, `MODEL_CONFIG_FILE` |
| YAML 文件 | `workflow_config_<model>.yaml` | `workflow_config_gru.yaml` |
| 模型注册名 | `<algo>_<dataset>` | `gru_Alpha158`, `lightgbm_Alpha158` |
| 复合 key | `model_name@mode` | `gru_Alpha158@static` |

### 2.3 Docstring 规范

**公共函数使用 Google 风格**：

```python
def train_single_model(model_name, yaml_file, params, experiment_name, no_pretrain=False):
    """
    训练单个模型。

    Args:
        model_name (str): 模型名称
        yaml_file (str): workflow YAML 文件路径
        params (dict): 训练参数
        experiment_name (str): MLflow 实验名
        no_pretrain (bool): 是否跳过预训练，默认 False

    Returns:
        dict: 包含 record_id 和 experiment_name

    Raises:
        RuntimeError: 训练失败时
    """
```

**模块级 docstring**（每个工具模块文件顶部）：

```python
"""
融合引擎 — 权重计算 & 信号融合

从 ensemble_fusion.py Stage 3 / Stage 4 抽取，供多处复用。

主要功能：
- calculate_weights() — 四种权重模式
- generate_ensemble_signal() — 融合信号生成
"""
```

### 2.4 类定义位置

类集中在以下文件定义，不要散落各处：

| 文件 | 类 |
|------|-----|
| `utils/strategy.py` | `OrderGenerator`, `TopkDropoutOrderGenerator` |
| `utils/run_context.py` | `RunContext`, `_LegacyRunContext` |
| `scripts/brokers/base.py` | `BaseBrokerAdapter` |
| `scripts/deep_analysis/base_agent.py` | `BaseAgent`, `AgentFindings`, `AnalysisContext` |
| `scripts/analysis/portfolio_analyzer.py` | `PortfolioAnalyzer` |

### 2.5 入口脚本模板

```python
#!/usr/bin/env python
"""
<脚本名> — <一句话描述>

使用方式：
  python quantpits/scripts/<script_name>.py --arg value

示例：
  python quantpits/scripts/<script_name>.py --models gru_Alpha158
"""

import os
import sys
import json
import argparse
from datetime import datetime

from quantpits.utils import env

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    parser = argparse.ArgumentParser(description="<描述>")
    parser.add_argument("--models", type=str, help="逗号分隔的模型名")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不执行")
    # ... 添加参数
    return parser.parse_args()


def main():
    args = parse_args()
    # ... 业务逻辑


if __name__ == "__main__":
    main()
```

### 2.6 断点恢复与状态管理

所有长时间运行的脚本必须实现状态管理：

```python
RUN_STATE_FILE = os.path.join(ROOT_DIR, "data", "run_state.json")

def save_run_state(state, state_file=None):
    """保存运行状态到 JSON 文件。"""
    if state_file is None:
        state_file = RUN_STATE_FILE
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)

def load_run_state(state_file=None):
    """加载运行状态，不存在则返回空字典。"""
    if state_file is None:
        state_file = RUN_STATE_FILE
    if os.path.exists(state_file):
        with open(state_file) as f:
            return json.load(f)
    return {}
```

### 2.7 历史备份模式

修改重要文件前必须备份：

```python
def backup_file_with_date(file_path, history_dir=None, prefix=None):
    """备份文件到 data/history/ 目录，带日期时间戳。"""
    if history_dir is None:
        history_dir = os.path.join(ROOT_DIR, "data", "history")
    os.makedirs(history_dir, exist_ok=True)

    basename = os.path.basename(file_path)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_name = f"{prefix}_{basename}_{timestamp}" if prefix else f"{basename}_{timestamp}"
    shutil.copy(file_path, os.path.join(history_dir, backup_name))
```

### 2.8 信号处理（可选）

长时间运行的脚本可注册信号处理器实现安全中断：

```python
import signal

def _signal_handler(signum, frame):
    print("\n⚠️ 已安全中断！")
    raise KeyboardInterrupt()

def _install_signal_handlers():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
```

---

## 3. 工作区配置

### 3.1 配置文件一览

```
config/
├── model_registry.yaml       # 模型注册（算法、数据集、YAML 路径、启用状态）
├── model_config.json         # 日期/市场参数（训练窗口、滑动模式、频次）
├── strategy_config.yaml      # 策略参数（TopK/DropN、回测账户、费率）
├── prod_config.json          # 生产状态（持仓、现金，**由 prod_post_trade.py 自动管理**）
├── ensemble_config.json      # 融合组合（combo 定义、权重方法）
├── rolling_config.yaml       # 滚动训练配置（窗口长度、步长）
├── cashflow.json             # 出入金记录
├── combo_groups.yaml         # 组合搜索分组配置（可选）
└── workflow_config_<model>.yaml  # 每个模型的 Qlib 工作流配置
```

### 3.2 model_registry.yaml

```yaml
models:
  gru_Alpha158:                    # 命名格式：algo_dataset
    algorithm: gru                  # 算法名（用于 --algorithm 筛选）
    dataset: Alpha158              # 数据集（Alpha158 / Alpha360）
    market: csi300                 # 市场标签（用于 --market 筛选）
    yaml_file: config/workflow_config_gru.yaml  # 相对路径，**必须加 config/ 前缀**
    enabled: true                  # true = --all-enabled 会训练，false = 跳过
    tags: [ts, basemodel]         # 标签数组，用于 --tag 筛选
    # pretrain_source: lstm_Alpha158  # 可选，声明预训练依赖
```

### 3.3 model_config.json

```json
{
    "train_date_mode": "last_trade_date",   // last_trade_date=使用Qlib最新交易日; fixed_date=固定日期
    "data_slice_mode": "slide",             // slide=滑动窗口; fixed=固定日期
    "train_set_windows": 8,                // 训练集长度（年）
    "valid_set_window": 2,                 // 验证集长度（年）
    "test_set_window": 3,                  // 测试集长度（年）
    "market": "csi300",
    "benchmark": "SH000300",
    "freq": "week",                         // week 或 day（**影响 label 公式**）
    "experiment_name_prefix": "my_strategy"
}
```

**slide 模式日期计算**：

```
anchor_date = 最新交易日 (last_trade_date 模式)

fit_start      = anchor - (train + valid + test) 年
fit_end        = anchor - (valid + test) 年
valid_start    = anchor - (valid + test) 年
valid_end      = anchor - test 年
test_start     = anchor - test 年
test_end       = anchor
```

### 3.4 workflow_config_*.yaml

**命名**：`workflow_config_<model_name>.yaml`

**关键模板**：

```yaml
qlib_init:
    provider_uri: ~/.qlib/qlib_data/cn_data
    region: cn

market: &market csi300
benchmark: &benchmark SH000300

data_handler_config: &data_handler_config
    start_time: "2008-01-01"
    end_time: <TBD>              # 运行时由 inject_config 注入
    fit_start_time: <TBD>
    fit_end_time: <TBD>
    instruments: *market
    infer_processors: []
    learn_processors:
        - class: DropnaLabel
        - class: CSRankNorm
          kwargs:
              fields_group: label
    label: ["Ref($close, -6) / Ref($close, -1) - 1"]  # 默认周频，运行时被 freq 覆盖

task:
    model:
        class: GRUModel
        module_path: qlib.contrib.model.pytorch_model
        kwargs:
            d_feat: 158           # **必须匹配**：Alpha158=158, Alpha360=360
            hidden_size: 64
            num_layers: 2
            dropout: 0.0
    dataset:
        class: DatasetH
        module_path: qlib.data.dataset
        kwargs:
            handler:
                class: Alpha158
                module_path: qlib.contrib.data.handler
                kwargs:
                    <<: *data_handler_config  # YAML 合并继承
            segments:
                train: [<TBD>, <TBD>]
                valid: [<TBD>, <TBD>]
                test:  [<TBD>, <TBD>]
    record:
        - class: SignalRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              model: <MODEL>
              dataset: <DATASET>
        - class: SigAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              ana_long_short: True
              ann_scaler: 52      # **必须匹配**：周频=52，日频=242

port_analysis_config: {}
```

### 3.5 rolling_config.yaml

```yaml
rolling_start: "2020-01-01"   # T: 起始日期
train_years: 3                  # X: 训练区间（**必须为整数年**）
valid_years: 1                 # Y: 验证区间（**必须为整数年**）
test_step: "3M"                # Z: 测试步长 (nM 或 nY，**不支持小数**)
```

### 3.6 run_env.sh

```bash
# Linux/Mac
export QLIB_WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# export QLIB_DATA_DIR="~/.qlib/qlib_data/cn_data"  # 可选：自定义数据路径
# export QLIB_REGION="cn"                            # 可选：cn 或 us
```

> **不要硬编码绝对路径**。使用 `BASH_SOURCE[0]` 动态推导，确保工作区可迁移。

### 3.7 初始化新工作区

```bash
# 使用脚手架工具（推荐）
python -m quantpits.tools.init_workspace \
    --source workspaces/Demo_Workspace \
    --target workspaces/My_Workspace

# 初始化后必须完成的配置
# 1. 修改 run_env.sh 中的 QLIB_DATA_DIR
# 2. 修改 model_config.json 中的 market
# 3. 验证：source workspaces/My_Workspace/run_env.sh
#          python quantpits/scripts/static_train.py --list
```

---

## 4. 训练流水线

### 4.1 静态训练流水线

```
① 全量训练 ──────────────────────────────────────────
   python quantpits/scripts/static_train.py --full

② 仅预测（不重训）────────────────────────────────
   python quantpits/scripts/static_train.py --predict-only --all-enabled

③ 组合搜索（偶尔）────────────────────────────────
   # 快速筛选（秒级，~5000倍速）
   python quantpits/scripts/brute_force_fast.py --max-combo-size 3
   python quantpits/scripts/brute_force_fast.py --exclude-last-years 1

   # 精确回测（分钟级）
   python quantpits/scripts/brute_force_ensemble.py --max-combo-size 3

   # 独立 OOS 验证
   python quantpits/scripts/analyze_ensembles.py \
     --metadata output/ensemble_runs/brute_force_fast_<date>/run_metadata.json

④ 融合预测（每次）────────────────────────────────
   python quantpits/scripts/ensemble_fusion.py --from-config-all

⑤ Post-Trade（每次）──────────────────────────────
   python quantpits/scripts/prod_post_trade.py

⑥ 订单生成（每次）────────────────────────────────
   python quantpits/scripts/order_gen.py
```

### 4.2 滚动训练流水线

滚动训练与静态训练**完全独立**，共存于同一 Workspace：

```
① 冷启动（首次必须）──────────────────────────────
   python quantpits/scripts/rolling_train.py --cold-start --all-enabled

② 日常滚动（自动检测新 window）─────────────────
   python quantpits/scripts/rolling_train.py --all-enabled

③ 仅预测───────────────────────────────────────
   python quantpits/scripts/rolling_train.py --predict-only --all-enabled

④ 断点恢复─────────────────────────────────────
   python quantpits/scripts/rolling_train.py --resume
```

滚动训练结果通过 `--training-mode rolling` 接入下游：

```bash
python quantpits/scripts/brute_force_fast.py --training-mode rolling
python quantpits/scripts/ensemble_fusion.py --from-config --training-mode rolling
```

### 4.3 模型选择参数

| 参数 | 说明 |
|------|------|
| `--models m1,m2` | 按名称指定 |
| `--algorithm alg` | 按算法筛选 |
| `--dataset ds` | 按数据集筛选 |
| `--tag tag` | 按标签筛选 |
| `--all-enabled` | 所有 enabled=true 的模型 |
| `--skip m1,m2` | 排除指定模型 |
| `--dry-run` | 仅预览不执行 |

### 4.4 训练中断恢复

```bash
# 查看状态
python quantpits/scripts/static_train.py --show-state

# 继续（跳过已完成的）
python quantpits/scripts/static_train.py --models gru,mlp --resume

# 清除状态重新开始
python quantpits/scripts/static_train.py --clear-state
```

---

## 5. 扩展指南

### 5.1 添加新模型

1. 创建 `config/workflow_config_<model>.yaml`（参考 Qlib benchmarks 模板）
2. 在 `model_registry.yaml` 中注册（**首次设 `enabled: false`**）
3. 单独训练验证：

   ```bash
   python quantpits/scripts/static_train.py --models <name> --dry-run
   python quantpits/scripts/static_train.py --models <name>
   ```

4. 确认无误后改为 `enabled: true`

**关键注意事项**：

| 错误 | 后果 |
|------|------|
| `d_feat` 与数据集不匹配 | PyTorch tensor shape 不匹配 |
| `yaml_file` 路径缺少 `config/` 前缀 | 模型训练时找不到 YAML |
| `ann_scaler` 与 `freq` 不匹配 | IC/ICIR 计算错误 |
| `label` 公式用错天数 | 收益率目标错误 |

### 5.2 添加新融合方法

在 `fusion_engine.py` 的 `calculate_weights()` 中扩展：

```python
def calculate_weights(norm_df, model_metrics, method, ...):
    if method == 'your_new_method':
        # 你的新权重计算逻辑
        your_weights = your_compute_weights(norm_df, model_metrics)
        return your_weights, None, True  # (dynamic_weights, static_weights, is_dynamic)
```

### 5.3 添加新券商适配器

1. 在 `scripts/brokers/` 下实现：

   ```python
   from .base import BaseBrokerAdapter, REQUIRED_COLUMNS

   class MyBrokerAdapter(BaseBrokerAdapter):
       @property
       def name(self) -> str:
           return "my_broker"

       def read_settlement(self, file_path: str) -> pd.DataFrame:
           df = pd.read_excel(file_path)
           # 列名映射...
           return df

       def validate(self, df: pd.DataFrame) -> pd.DataFrame:
           missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
           if missing:
               raise ValueError(f"Missing columns: {missing}")
           return df
   ```

2. 在 `scripts/brokers/__init__.py` 中注册

### 5.4 添加新数据源

1. 将数据转换为 Qlib 二进制格式：

   ```bash
   python scripts/dump_qlib_bin.py \
       --source data/daily/*.parquet \
       --target ~/.qlib/qlib_data/my_data \
       --freq day
   ```

2. 在 `run_env.sh` 中指定路径：

   ```bash
   export QLIB_DATA_DIR="/path/to/your/qlib_data"
   export QLIB_REGION="cn"  # 或 "us"
   ```

---

## 6. 常见问题

| 问题 | 解决 |
|------|------|
| 报错 "Please source a workspace run_env.sh first!" | 先 `source workspaces/My_Workspace/run_env.sh` |
| `--list` 报错 qlib 相关错误 | qlib 导入放在了函数外部，需延迟到函数内部 |
| 训练时模型找不到 YAML | `yaml_file` 路径应加 `config/` 前缀 |
| `d_feat` 不匹配报错 | Alpha158=158, Alpha360=360，检查 YAML 中的 `d_feat` 和 `ann_scaler` |
| 滚动训练 `test_step` 报错 | `train_years`/`valid_years` 必须为整数，`test_step` 只支持 `nM` 或 `nY` |
| `prod_config.json` 内容被覆盖 | 正常现象，由 `prod_post_trade.py` 自动管理 |
| 跨日期增量训练日期混乱 | 建议在同 anchor_date 窗口内增量，跨日期用 `--full` |
| `run_env.sh` 路径失效 | 不要硬编码绝对路径，使用 `BASH_SOURCE[0]` 动态推导 |

---

## 附录：核心文件速查

| 文件 | 关键函数/类 | 说明 |
|------|-------------|------|
| `utils/train_utils.py:646` | `train_single_model()` | 单模型训练入口 |
| `utils/train_utils.py:1108` | `predict_single_model()` | 单模型预测入口 |
| `utils/train_utils.py:463` | `inject_config()` | YAML 参数注入 |
| `utils/train_utils.py:564` | `load_model_registry()` | 加载模型注册表 |
| `utils/fusion_engine.py:11` | `calculate_weights()` | 权重计算（四种模式） |
| `utils/config_loader.py:7` | `load_workspace_config()` | Workspace 配置加载 |
| `utils/env.py:44` | `init_qlib()` | Qlib 环境初始化 |
| `scripts/static_train.py` | `--full`, `--predict-only` | 静态训练入口 |
| `scripts/rolling_train.py` | `--cold-start`, `--resume` | 滚动训练入口 |
| `scripts/ensemble_fusion.py` | `--from-config` | 融合预测入口 |
