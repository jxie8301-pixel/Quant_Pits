"""
data_sync CLI入口 — python -m quantpits.scripts.data_sync

用法：
    python -m quantpits.scripts.data_sync sync-static
    python -m quantpits.scripts.data_sync sync-daily --mode full --tier post_market
    python -m quantpits.scripts.data_sync sync-event --mode daily --tier event
    python -m quantpits.scripts.data_sync dump-pit
    python -m quantpits.scripts.data_sync dump-bin --mode full
    python -m quantpits.scripts.data_sync sync-all --mode daily
    python -m quantpits.scripts.data_sync status
"""

from quantpits.utils import env

import os
import sys
import signal
import logging
import argparse
from datetime import datetime

from quantpits.scripts.data_sync.rate_limiter import SHUTDOWN_EVENT


def build_parser() -> argparse.ArgumentParser:
    """
    构建CLI参数解析器。

    Returns:
        argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        description="QuantPits 数据同步工具 — Tushare→Parquet→Qlib bin/PIT"
    )

    parser.add_argument(
        '--workspace',
        default=None,
        help='Workspace根目录（也可通过QLIB_WORKSPACE_DIR环境变量设置）',
    )
    parser.add_argument(
        '--project-root',
        default=None,
        help='项目根目录（全局raw数据位置，默认自动推断）',
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='日志级别',
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "sync-static",
        help="同步静态表（stock_basic, trade_cal）",
    )

    sp_daily = subparsers.add_parser(
        "sync-daily",
        help="同步日频接口",
    )
    sp_daily.add_argument(
        '--mode',
        choices=['full', 'daily'],
        default='daily',
        dest='sub_mode',
        help='同步模式',
    )
    sp_daily.add_argument(
        '--tier',
        nargs='+',
        default=None,
        dest='tier_names',
        help='指定tier（如 post_market capital_flow）',
    )
    sp_daily.add_argument(
        '--interfaces',
        nargs='+',
        default=None,
        dest='sub_interfaces',
        help='指定接口',
    )
    sp_daily.add_argument(
        '--repair',
        action='store_true',
        default=False,
        help='扫描并补齐历史缺失日期，不覆盖已有raw文件',
    )

    sp_event = subparsers.add_parser(
        "sync-event",
        help="同步事件型接口",
    )
    sp_event.add_argument(
        '--mode',
        choices=['full', 'daily'],
        default='daily',
        dest='sub_mode',
        help='同步模式',
    )
    sp_event.add_argument(
        '--tier',
        nargs='+',
        default=None,
        dest='tier_names',
        help='指定tier（如 event）',
    )
    sp_event.add_argument(
        '--interfaces',
        nargs='+',
        default=None,
        dest='sub_interfaces',
        help='指定接口',
    )

    subparsers.add_parser(
        "status",
        help="查询同步状态",
    )

    sp_dump_bin = subparsers.add_parser(
        "dump-bin",
        help="按股票逐个生成Qlib bin（日频+indicator+event_day_only字段）",
    )
    sp_dump_bin.add_argument(
        '--mode',
        choices=['full', 'daily'],
        default='daily',
        dest='sub_mode',
        help='转换模式',
    )
    sp_dump_bin.add_argument(
        '--start-date',
        default=None,
        help='起始日期YYYYMMDD',
    )
    sp_dump_bin.add_argument(
        '--end-date',
        default=None,
        help='结束日期YYYYMMDD',
    )
    sp_dump_bin.add_argument(
        '--field-mapping',
        default=None,
        dest='sub_field_mapping',
        help='字段映射YAML配置路径',
    )
    sp_dump_bin.add_argument(
        '--stocks',
        nargs='+',
        default=None,
        dest='sub_stocks',
        help='指定股票ts_code列表（如 600519.SH 000001.SZ），仅生成指定股票',
    )

    sp_dump_pit = subparsers.add_parser(
        "dump-pit",
        help="生成PIT文件（forward_fill型事件字段，训练时用P($$field_q)读取）",
    )
    sp_dump_pit.add_argument(
        '--stocks',
        nargs='+',
        default=None,
        dest='sub_stocks',
        help='指定股票ts_code列表（如 600519.SH 000001.SZ），仅生成指定股票',
    )

    sp_sync_all = subparsers.add_parser(
        "sync-all",
        help="一键执行完整数据准备流程（sync→dump-pit→dump-bin）",
    )
    sp_sync_all.add_argument(
        '--mode',
        choices=['full', 'daily'],
        default='daily',
        dest='sub_mode',
        help='模式：daily=增量（默认），full=全量重建',
    )
    sp_sync_all.add_argument(
        '--skip',
        nargs='+',
        default=None,
        dest='skip_steps',
        help='跳过的步骤（如 sync-static dump-pit）',
    )
    sp_sync_all.add_argument(
        '--start-date',
        default=None,
        help='dump-bin起始日期YYYYMMDD',
    )
    sp_sync_all.add_argument(
        '--end-date',
        default=None,
        help='dump-bin结束日期YYYYMMDD',
    )
    sp_sync_all.add_argument(
        '--field-mapping',
        default=None,
        dest='sub_field_mapping',
        help='字段映射YAML配置路径',
    )
    sp_sync_all.add_argument(
        '--repair',
        action='store_true',
        default=False,
        help='日频同步阶段补齐历史缺失日期，不覆盖已有raw文件',
    )
    sp_sync_all.add_argument(
        '--stocks',
        nargs='+',
        default=None,
        dest='sub_stocks',
        help='指定股票ts_code列表（如 600519.SH 000001.SZ），dump-pit和dump-bin仅生成指定股票',
    )

    return parser


def _setup_logging(log_level: str, workspace_dir: str) -> None:
    """
    配置日志：控制台+按日期滚动文件。

    Args:
        log_level: 日志级别
        workspace_dir: Workspace根目录
    """
    log_dir = os.path.join(workspace_dir, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    log_file = os.path.join(log_dir, f"sync_{date_str}.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root_logger.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)


def main() -> None:
    """CLI入口主函数。"""
    parser = build_parser()
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        print(
            "错误：环境变量TUSHARE_TOKEN未设置，"
            "请通过 export TUSHARE_TOKEN=xxx 配置后重试"
        )
        sys.exit(1)

    masked = token[:6] + "*" * (len(token) - 10) + token[-4:] if len(token) > 10 else "***"

    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    workspace_dir = env.ROOT_DIR
    if args.workspace:
        workspace_dir = args.workspace

    cmd = args.command
    needs_workspace = cmd in ("dump-bin", "dump-pit", "sync-all")
    if needs_workspace and not workspace_dir:
        print(
            "错误：此命令需要Workspace，请设置 QLIB_WORKSPACE_DIR 环境变量或使用 --workspace 参数"
        )
        sys.exit(1)

    log_dir = workspace_dir if workspace_dir else os.getcwd()
    _setup_logging(args.log_level, log_dir)
    logger = logging.getLogger(__name__)

    logger.info(f"Tushare Token: {masked}")
    if workspace_dir:
        logger.info(f"Workspace: {workspace_dir}")
    else:
        logger.info("Workspace: 未设置（仅数据拉取模式）")

    if conda_env and conda_env != "qlib":
        logger.warning(
            f"当前conda环境为 '{conda_env}'，建议执行 conda activate qlib 后重试"
        )

    signal_count = 0

    def _signal_handler(signum, frame):
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            logger.warning("收到退出信号(Ctrl+C)，正在安全停止...（再按一次强制退出）")
            SHUTDOWN_EVENT.set()
            raise KeyboardInterrupt
        else:
            logger.warning("收到第二次退出信号，强制退出！")
            sys.exit(130)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    project_root = args.project_root

    if args.command:
        try:
            _dispatch_subcommand(args, workspace_dir, project_root, logger)
        except KeyboardInterrupt:
            logger.warning("收到 Ctrl+C，已停止")
            sys.exit(130)
    else:
        parser.print_help()
        sys.exit(1)


def _dispatch_subcommand(
    args: argparse.Namespace,
    workspace_dir: str,
    project_root: str | None,
    logger: logging.Logger,
) -> None:
    """
    分发子命令到对应cmd模块。

    Args:
        args: 解析后的命令行参数
        workspace_dir: Workspace根目录
        project_root: 项目根目录
        logger: Logger实例
    """
    cmd = args.command
    log_level = args.log_level

    if cmd == "sync-static":
        from quantpits.scripts.data_sync.commands.sync_static_cmd import run_sync_static
        logger.info("子命令: sync-static")
        run_sync_static(project_root, workspace_dir, log_level)

    elif cmd == "sync-daily":
        from quantpits.scripts.data_sync.commands.sync_daily_cmd import run_sync_daily
        mode = getattr(args, 'sub_mode', 'daily')
        tier_names = getattr(args, 'tier_names', None)
        interfaces = getattr(args, 'sub_interfaces', None)
        repair = getattr(args, 'repair', False)
        logger.info(f"子命令: sync-daily, mode={mode}")
        run_sync_daily(
            workspace_dir, mode, tier_names, interfaces,
            project_root, log_level, repair=repair,
        )

    elif cmd == "sync-event":
        from quantpits.scripts.data_sync.commands.sync_event_cmd import run_sync_event
        mode = getattr(args, 'sub_mode', 'daily')
        tier_names = getattr(args, 'tier_names', None)
        interfaces = getattr(args, 'sub_interfaces', None)
        logger.info(f"子命令: sync-event, mode={mode}")
        run_sync_event(
            workspace_dir, mode, tier_names, interfaces,
            project_root, log_level,
        )

    elif cmd == "dump-bin":
        from quantpits.scripts.data_sync.commands.dump_bin_cmd import run_dump_bin
        mode = getattr(args, 'sub_mode', 'daily')
        start_date = getattr(args, 'start_date', None)
        end_date = getattr(args, 'end_date', None)
        field_mapping = getattr(args, 'sub_field_mapping', None)
        target_stocks = getattr(args, 'sub_stocks', None)
        logger.info(f"子命令: dump-bin, mode={mode}")
        run_dump_bin(
            workspace_dir, mode, start_date, end_date,
            field_mapping, project_root, log_level,
            target_stocks=target_stocks,
        )

    elif cmd == "dump-pit":
        from quantpits.scripts.data_sync.commands.dump_pit_cmd import run_dump_pit
        target_stocks = getattr(args, 'sub_stocks', None)
        logger.info("子命令: dump-pit")
        run_dump_pit(
            workspace_dir, project_root, log_level,
            overwrite=True, target_stocks=target_stocks,
        )

    elif cmd == "sync-all":
        from quantpits.scripts.data_sync.commands.sync_all_cmd import run_sync_all
        mode = getattr(args, 'sub_mode', 'daily')
        skip = getattr(args, 'skip_steps', None)
        start_date = getattr(args, 'start_date', None)
        end_date = getattr(args, 'end_date', None)
        field_mapping = getattr(args, 'sub_field_mapping', None)
        repair = getattr(args, 'repair', False)
        target_stocks = getattr(args, 'sub_stocks', None)
        logger.info(f"子命令: sync-all, mode={mode}")
        run_sync_all(
            workspace_dir, mode, skip, start_date, end_date,
            field_mapping, project_root, log_level, repair=repair,
            target_stocks=target_stocks,
        )

    elif cmd == "status":
        from quantpits.scripts.data_sync.commands.status_cmd import run_status
        logger.info("子命令: status")
        run_status(workspace_dir, project_root, log_level)

    else:
        logger.error(f"未知子命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
