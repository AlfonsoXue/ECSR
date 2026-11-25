"""程式進入點：雙擊執行開啟視窗；加上 xlsx 路徑等參數則走命令列模式（見 ecsr_tool/cli.py）。"""
import sys

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] != "--selftest":
        from ecsr_tool.cli import main
    else:
        from ecsr_tool.gui import main
    sys.exit(main(args))
