# 对照诊断：HKCU Run 里的其他项是否也启动失败？
# 如果它们都起来了、只有 MechrevoMode 没起，说明 Run 列表本身没问题，
# 问题出在这一条 entry 上；如果全都没起，说明整个 Run 列表被策略/设置关掉了。
import os
import subprocess
import winreg

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 读真实注册表内容，不手写路径（避免转义踩坑）
entries = []
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as k:
    for i in range(winreg.QueryInfoKey(k)[1]):
        name, value, typ = winreg.EnumValue(k, i)
        entries.append((name, value, typ))

r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                   capture_output=True, text=True, encoding="gbk", errors="replace")
running = [line.split(",")[0].strip('"').lower() for line in r.stdout.splitlines() if line.strip()]

print("=" * 84)
print("HKCU\\...\\Run 每一项目前是否有对应进程在运行")
print("=" * 84)
print("%-26s %-6s %s" % ("名称", "在跑", "目标"))
print("-" * 84)

# StartupApproved 用于标注「任务管理器里被禁用」
disabled = set()
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run") as k:
        for i in range(winreg.QueryInfoKey(k)[1]):
            nm, val, _t = winreg.EnumValue(k, i)
            if isinstance(val, (bytes, bytearray)) and len(val) > 0 and val[0] == 3:
                disabled.add(nm)
except FileNotFoundError:
    pass

launched, missing = [], []
for name, value, typ in entries:
    # 从注册表值里取可执行文件名（去掉引号、取第一段）
    v = value.strip()
    if v.startswith('"'):
        exe = v[1:].split('"')[0]
    else:
        exe = v.split(" ")[0]
    base = os.path.basename(exe).lower()
    alive = base in running
    tag = ""
    if name in disabled:
        tag = "  [任务管理器已禁用]"
    print("%-26s %-6s %s%s" % (name, "YES" if alive else "--", exe, tag))
    (launched if alive else missing).append(name)

print()
print("已启动 %d 项，未启动 %d 项" % (len(launched), len(missing)))
print()
print("=" * 84)
print("结论")
print("=" * 84)
if len(launched) >= 2:
    print("  Run 列表本身是正常处理的（有多项成功启动）。")
    print("  => 问题只出在 MechrevoMode 这一条 entry 上。")
    print("  => 需要针对这条 entry 找原因，而不是怀疑 Run 机制被关。")
else:
    print("  几乎没有 Run 项成功启动，怀疑整个 Run 列表未被处理。")
print()
print("未启动的项：", ", ".join(missing) if missing else "无")
