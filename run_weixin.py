"""
微信-Claude 桥接服务一键启动脚本。

用法:
    python run_weixin.py              # 正常启动（复用已有 token）
    python run_weixin.py --login      # 强制重新扫码登录
    python run_weixin.py --config xx  # 指定配置文件
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from weixin_lib.logger import setup_logger
from weixin_lib.bridge import WeixinClaudeBridge


def main():
    force_login = "--login" in sys.argv
    config_path = None
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    print("=" * 50)
    print("  微信-Claude Code 桥接服务")
    print("=" * 50)
    print()

    setup_logger()

    bridge = WeixinClaudeBridge(config_path=config_path)

    if not bridge.login(force=force_login):
        print("❌ 登录失败，退出")
        sys.exit(1)

    try:
        bridge.run()
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
