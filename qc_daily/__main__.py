"""支持 `python -m qc_daily` 直接运行 CLI。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
