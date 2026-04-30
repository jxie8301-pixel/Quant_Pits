"""
全局raw/Workspace路径解析器 — raw数据全局共享，bin per-workspace。

读取优先级：项目级全局raw → Workspace级raw回退。
写入始终走项目级全局raw（不存在时自动创建）。
"""

from quantpits.utils import env

import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DataPaths:
    """
    数据路径集合，封装raw/feature/qlib_data的路径解析结果。

    Attributes:
        raw_read_dir: raw数据读取目录（全局优先，Workspace回退）
        raw_write_dir: raw数据写入目录（始终项目级全局）
        feature_dir: 宽表Parquet目录（per-workspace）
        qlib_dir: Qlib bin数据目录（per-workspace）
        using_workspace_raw: 是否使用了Workspace级raw回退
    """
    raw_read_dir: str
    raw_write_dir: str
    feature_dir: str
    qlib_dir: str
    using_workspace_raw: bool


def resolve_project_root(project_root: str | None = None) -> str:
    """
    解析项目根目录。

    优先级：
    1. --project-root 命令行参数
    2. QUANTPITS_RAW_DIR 环境变量（指向raw目录，推断project_root）
    3. 向上查找包含quantpits/目录的父目录
    4. 当前工作目录

    Args:
        project_root: 命令行指定的项目根目录

    Returns:
        项目根目录的绝对路径
    """
    if project_root and os.path.isdir(project_root):
        return os.path.abspath(project_root)

    env_raw = os.environ.get("QUANTPITS_RAW_DIR")
    if env_raw:
        candidate = os.path.dirname(os.path.dirname(env_raw))
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)

    current = os.getcwd()
    for _ in range(5):
        if os.path.isdir(os.path.join(current, "quantpits")):
            return os.path.abspath(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return os.getcwd()


def resolve_data_paths(
    workspace_dir: str,
    project_root: str | None = None,
) -> DataPaths:
    """
    解析所有数据路径，实现raw全局优先+Workspace回退策略。

    Args:
        workspace_dir: Workspace根目录（通过env.ROOT_DIR或--workspace获取）
        project_root: 项目根目录（可选，自动推断）

    Returns:
        DataPaths 包含所有解析后的路径

    Note:
        - raw写入始终使用项目级目录（不存在时自动创建）
        - raw读取优先项目级，不存在时回退到Workspace级
        - 回退时记录INFO日志提示
    """
    root = resolve_project_root(project_root)
    global_raw = os.path.join(root, "data", "raw")
    ws_raw = os.path.join(workspace_dir, "data", "raw")
    feature_dir = os.path.join(workspace_dir, "data", "feature")
    qlib_dir = os.path.join(workspace_dir, "data", "qlib_data")

    os.makedirs(global_raw, exist_ok=True)

    using_workspace_raw = False
    raw_read_dir = global_raw
    if not _has_parquet_files(global_raw):
        if os.path.exists(ws_raw) and _has_parquet_files(ws_raw):
            raw_read_dir = ws_raw
            using_workspace_raw = True
            logger.info(
                f"使用Workspace级raw数据（{ws_raw}），"
                f"新数据将写入项目级raw（{global_raw}）"
            )

    return DataPaths(
        raw_read_dir=raw_read_dir,
        raw_write_dir=global_raw,
        feature_dir=feature_dir,
        qlib_dir=qlib_dir,
        using_workspace_raw=using_workspace_raw,
    )


def resolve_raw_dir(
    workspace_dir: str,
    project_root: str | None = None,
) -> str:
    """
    解析raw数据读取目录（便捷函数）。

    Args:
        workspace_dir: Workspace根目录
        project_root: 项目根目录（可选，自动推断）

    Returns:
        raw数据读取目录的绝对路径
    """
    paths = resolve_data_paths(workspace_dir, project_root)
    return paths.raw_read_dir


def _has_parquet_files(directory: str) -> bool:
    """
    检查目录下是否有Parquet文件（含子目录）。

    Args:
        directory: 目录路径

    Returns:
        True表示存在Parquet文件
    """
    if not os.path.exists(directory):
        return False
    for root, dirs, files in os.walk(directory):
        if any(f.endswith(".parquet") for f in files):
            return True
    return False
