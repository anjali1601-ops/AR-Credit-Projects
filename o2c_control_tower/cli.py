"""Print the director summary, or serve it."""

from __future__ import annotations

import sys

from o2c_control_tower.app import HOST, PORT, app
from o2c_control_tower.render import render_text
from o2c_control_tower.summary import build_control_tower


def serve() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["serve"]:
        serve()
        return
    if args:
        raise SystemExit("usage: python -m o2c_control_tower [serve]")
    sys.stdout.write(render_text(build_control_tower()))


if __name__ == "__main__":
    main()
