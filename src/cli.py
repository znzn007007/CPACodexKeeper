from .maintainer import CPACodexKeeper
from .notifier import FeishuNotifier
from .quota_job import run_test_notification
from .settings import SettingsError, load_settings


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description="CPACodexKeeper")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不实际修改 / Dry run")
    parser.add_argument("--daemon", action="store_true", default=True, help="守护模式，默认开启 / Run forever")
    parser.add_argument("--once", dest="daemon", action="store_false", help="仅执行一轮后退出 / Run once")
    parser.add_argument(
        "--quota-test",
        choices=("summary", "broadcast", "alert", "recovery", "deleted", "disabled"),
        help="发送受控 CPA quota 测试消息，不进入 maintainer 主流程 / Send controlled quota test notification",
    )
    parser.add_argument(
        "--quota-test-state-file",
        help="受控 quota 测试使用的隔离状态文件路径，仅用于记录部署验证命令 / Isolated test state path",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        settings = load_settings()
    except SettingsError as exc:
        parser.exit(status=2, message=f"Configuration error: {exc}\n")

    if args.quota_test_state_file:
        settings.quota_state_file = args.quota_test_state_file

    if args.quota_test:
        notifier = FeishuNotifier(settings=settings, dry_run=args.dry_run)
        return 0 if run_test_notification(settings, notifier, args.quota_test) else 1

    maintainer = CPACodexKeeper(settings=settings, dry_run=args.dry_run)
    try:
        if args.daemon:
            maintainer.run_forever(interval_seconds=settings.interval_seconds)
            return 0
        maintainer.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        maintainer.notifier.notify_process_exit(exc)
        raise
