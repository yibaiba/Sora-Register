"""
协议版入口：在本目录（protocol/）内直接启动时运行此脚本。
将上级目录加入 sys.path，使 config、email_service 等可被导入，然后执行批量注册。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from protocol.main_protocol import main

if __name__ == "__main__":
    main()
