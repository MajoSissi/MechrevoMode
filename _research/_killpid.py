# 结束指定 pid 的进程（提权用）。用法: python _killpid.py <pid> [<pid> ...]
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pub_test

if len(sys.argv) < 2:
    sys.exit("用法: python _killpid.py <pid> [...]")

ok, msg = _pub_test.enable_debug_privilege()
print("SeDebugPrivilege:", msg)
for a in sys.argv[1:]:
    pid = int(a)
    name = ""
    for p, n in _pub_test._gcu_state.processes():
        if p == pid:
            name = n
    if not name:
        print("  pid=%d 已不存在" % pid)
        continue
    _pub_test.kill(pid, name)
