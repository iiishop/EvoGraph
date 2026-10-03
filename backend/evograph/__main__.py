import argparse
import os
from pathlib import Path

from .application.api import Application
from .frontend_build import bundle_status, rebuild


def main():
    parser = argparse.ArgumentParser(description="EvoGraph desktop evolution planner")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Serve the browser development API and built frontend",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("EVOGRAPH_DATA_DIR", Path.home() / ".evograph")),
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--gui",
        choices=("qt", "gtk"),
        help="Override the desktop backend (default: Qt on Linux, native on Windows/macOS)",
    )
    parser.add_argument(
        "--build",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Rebuild before starting; default verifies provenance and rebuilds stale bundles",
    )
    args = parser.parse_args()

    if args.browser and args.gui:
        parser.error("--gui cannot be combined with --browser")
    if not args.browser:
        from .transport.desktop import check_desktop_environment

        check_desktop_environment()

    root = Path(__file__).resolve().parents[2]
    dist = root / "dist"
    if args.build is False:
        print(
            "警告：已显式使用 --no-build，跳过前端校验和重建；现有 dist 可能与源码不一致。",
            flush=True,
        )
    else:
        valid, provenance = bundle_status(root, dist)
        if args.build or not valid:
            if not valid:
                print(f"{provenance}，正在重新构建…", flush=True)
            ok, message = rebuild(root)
            print(message, flush=True)
            if not ok:
                install = "npm ci" if (root / "package-lock.json").is_file() else "npm install"
                raise SystemExit(
                    f"前端构建失败，已停止启动以避免使用旧版或不完整界面。\n"
                    f"请在 {root} 依次运行：\n{install}\nnpm run build\n"
                    "如需有意使用现有预构建界面，可显式传入 --no-build（不保证与源码一致）。"
                )
        else:
            print(provenance, flush=True)

    try:
        app = Application(args.data_dir)
    except OSError as exc:
        parser.error(
            f"Cannot open data directory {args.data_dir}: {exc}. "
            "Choose a writable directory with --data-dir or EVOGRAPH_DATA_DIR."
        )
    if args.browser:
        import uvicorn

        from .transport.http import create_app

        # Agent updates use HTTP streaming, so no optional WebSocket backend is needed.
        uvicorn.run(create_app(app, dist), host="127.0.0.1", port=args.port, ws="none")
    else:
        from .transport.desktop import launch

        launch(app, dist, gui=args.gui)


if __name__ == "__main__":
    main()
