"""命令列入口。

    payradar probe <provider> [--details N]   單一服務擷取體檢

完整一輪（run / emit / score / usage）在 Phase 2 起加入。
退出碼：0 成功；2 參數錯誤。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="payradar")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("provider")
    probe.add_argument("--details", type=int, default=2)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "probe":
        from .sources import probe as run_probe

        report = run_probe(args.provider, details=args.details)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
