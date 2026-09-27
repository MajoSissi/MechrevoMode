# 数据目录定位 —— 必须和程序自己的算法一致（见 config.go 的 dataDir/resolveDataDir）：
# 首选「程序同目录\data」，程序目录不可写时回退 %APPDATA%\MechrevoMode。
#
# 脚本里**别再写死 %APPDATA%**：程序改成绿色布局之后，写死的路径会一直读到旧文件，
# 表现出来是「日志不更新」「配置改了没反应」，看着像程序坏了，其实只是读错了文件。
import os

INSTALLED_EXE = r"D:\User\OneDrive\Programm\MechrevoMode\MechrevoMode.exe"
SRC_DIR = r"D:\User\Desktop\MechrevoMode"

# 旧版本的位置，也是回退目录。迁移测试（_mk_legacy_cfg.py）还要用它。
LEGACY = os.path.join(os.environ.get("APPDATA", ""), "MechrevoMode")


def data_dir():
	r"""按程序同目录是否存在 data\ 判断；不存在说明程序回退到了 APPDATA。"""
	d = os.path.join(os.path.dirname(INSTALLED_EXE), "data")
	return d if os.path.isdir(d) else LEGACY


DATA = data_dir()
LOG = os.path.join(DATA, "log.txt")
CONFIG = os.path.join(DATA, "config.json")
