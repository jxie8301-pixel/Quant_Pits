"""
sync-all子命令 — 一键执行完整数据准备流程。

自动按正确顺序串联5个步骤：
  ① sync-static      — 静态表
  ② sync-daily (全部tier) — 日频数据
  ③ sync-event       — 事件数据
  ④ dump-pit         — 生成PIT文件（forward_fill型事件字段）
  ⑤ dump-bin         — 生成Qlib bin（日频+indicator+event_day_only型字段）

支持 --mode daily（增量，默认）和 --mode full（全量重建）。
可通过 --skip 跳过已完成的步骤。
Ctrl+C 可优雅中断，中断后可用 --skip 跳过已完成步骤续跑。
"""

from quantpits.utils import env

import logging
import time


def run_sync_all(
    workspace_dir: str,
    mode: str,
    skip: list[str] | None,
    start_date: str | None,
    end_date: str | None,
    field_mapping_path: str | None,
    project_root: str | None,
    log_level: str,
    repair: bool = False,
    target_stocks: list[str] | None = None,
) -> None:
    """
    一键执行完整数据准备流程。

    Args:
        workspace_dir: Workspace根目录
        mode: "full"全量重建或"daily"增量
        skip: 跳过的步骤列表（如 ["sync-static", "dump-pit"]）
        start_date: 起始日期YYYYMMDD（仅影响dump-bin）
        end_date: 结束日期YYYYMMDD（仅影响dump-bin）
        field_mapping_path: 字段映射YAML路径
        project_root: 项目根目录
        log_level: 日志级别
        repair: True时日频同步补齐历史缺口但不覆盖已有raw
        target_stocks: 指定股票ts_code列表，dump-pit和dump-bin仅生成指定股票
    """
    from quantpits.scripts.data_sync.rate_limiter import SHUTDOWN_EVENT
    from quantpits.scripts.data_sync.commands.sync_static_cmd import run_sync_static
    from quantpits.scripts.data_sync.commands.sync_daily_cmd import run_sync_daily
    from quantpits.scripts.data_sync.commands.sync_event_cmd import run_sync_event
    from quantpits.scripts.data_sync.commands.dump_pit_cmd import run_dump_pit
    from quantpits.scripts.data_sync.commands.dump_bin_cmd import run_dump_bin

    logger = logging.getLogger(__name__)

    skip_set = set(skip) if skip else set()

    steps = [
        ("sync-static", "同步静态表"),
        ("sync-daily", "同步日频数据（全部tier）"),
        ("sync-event", "同步事件数据"),
        ("dump-pit", "生成PIT文件（forward_fill型事件字段）"),
        ("dump-bin", "生成Qlib bin文件"),
    ]

    logger.info(f"=== 一键数据准备 (mode={mode}, repair={repair}) ===")
    logger.info(f"步骤: {' → '.join(s[0] for s in steps)}")
    if skip_set:
        logger.info(f"跳过: {skip_set}")

    t0 = time.time()
    completed_steps: list[str] = []

    for step_name, step_desc in steps:
        if SHUTDOWN_EVENT.is_set():
            logger.warning("收到中断信号，跳过后续步骤")
            break

        if step_name in skip_set:
            logger.info(f"⏭ 跳过: {step_name} ({step_desc})")
            continue

        logger.info(f"▶ 步骤: {step_name} — {step_desc}")
        step_t0 = time.time()

        try:
            if step_name == "sync-static":
                run_sync_static(project_root, workspace_dir, log_level)

            elif step_name == "sync-daily":
                run_sync_daily(
                    workspace_dir, mode, None, None,
                    project_root, log_level, repair=repair,
                )

            elif step_name == "sync-event":
                run_sync_event(
                    workspace_dir, mode, None, None,
                    project_root, log_level,
                )

            elif step_name == "dump-pit":
                run_dump_pit(
                    workspace_dir, project_root, log_level,
                    overwrite=(mode == "full"),
                    target_stocks=target_stocks,
                )

            elif step_name == "dump-bin":
                run_dump_bin(
                    workspace_dir, mode, start_date, end_date,
                    field_mapping_path, project_root, log_level,
                    target_stocks=target_stocks,
                )

            elapsed = time.time() - step_t0
            logger.info(f"✓ {step_name} 完成 ({elapsed:.1f}s)")
            completed_steps.append(step_name)

        except KeyboardInterrupt:
            SHUTDOWN_EVENT.set()
            logger.warning("收到 Ctrl+C，正在优雅停止...")
            break

        except Exception as e:
            elapsed = time.time() - step_t0
            logger.error(f"✗ {step_name} 失败 ({elapsed:.1f}s): {e}")
            logger.error("后续步骤中止，请修复后重试（可用 --skip 跳过已完成步骤）")
            return

    total = time.time() - t0

    if SHUTDOWN_EVENT.is_set():
        skipped = [s[0] for s in steps if s[0] not in completed_steps and s[0] not in skip_set]
        logger.warning(f"=== 一键数据准备中断 (已完成: {completed_steps}) ===")
        if skipped:
            logger.info(f"续跑命令: python -m quantpits.scripts.data_sync sync-all --skip {' '.join(completed_steps)}")
    else:
        logger.info(f"=== 一键数据准备完成 (总耗时 {total:.1f}s) ===")
