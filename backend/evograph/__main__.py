import argparse
import os
from pathlib import Path

from .application.api import Application
from .frontend_build import is_stale, rebuild


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
        "--build",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Rebuild the frontend before starting; default rebuilds only when sources changed",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    dist = root / "dist"
    if args.build is not False and (args.build or is_stale(root, dist)):
        ok, message = rebuild(root)
        print(message, flush=True)
        if not ok and not (dist / "index.html").exists():
            raise SystemExit("前端未构建，且自动构建不可用。请手动运行：npm install && npm run build")

    app = Application(args.data_dir)
    if args.browser:
        import uvicorn

        from .transport.http import create_app

        # Agent updates use HTTP streaming, so no optional WebSocket backend is needed.
        uvicorn.run(create_app(app, dist), host="127.0.0.1", port=args.port, ws="none")
    else:
        from .transport.desktop import launch

        launch(app, dist)


if __name__ == "__main__":
    main()
