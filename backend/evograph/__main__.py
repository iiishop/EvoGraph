import argparse
import os
from pathlib import Path

from .application.api import Application


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
    args = parser.parse_args()
    app = Application(args.data_dir)
    dist = Path(__file__).resolve().parents[2] / "dist"
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
