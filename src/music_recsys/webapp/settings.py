"""music-recsysの環境設定。旧配布物の設定名は読込み時だけ受け付ける。"""

import os


def setting(name, default=""):
    return os.environ.get("MUSIC_RECSYS_" + name, os.environ.get("OTOLAB_" + name, default))
