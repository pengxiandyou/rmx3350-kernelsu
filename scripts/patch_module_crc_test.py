#!/usr/bin/env python3
"""实验性补丁：让内核忽略 vendor .ko 的 CRC 不匹配，只告警不拒绝。

背景
----
设备 F.28 固件（2024-08 编译）的 vendor 模块 `__versions` 里，
`module_layout` 的 CRC 是 0x3d2f9bfd，而公开源码树（最后同步 2023-03-20）
编出来的是 0x13931b80。520 个符号里 179 个对不上，包含
wake_up_process / kthread_stop / skb_copy / of_property_read_string 等，
说明 struct task_struct / sk_buff / device_node 的布局与固件不同。

本补丁把 kernel/module.c 的 check_version() 在版本不符时从
`return 0`（拒绝加载）改成 `return 1`（放行），仅打印告警。
目的是做一次实测：如果 OPPO 只是在结构体尾部追加字段，现用偏移不变，
模块可能仍然能跑；如果中间插了字段，强加载会踩坏内存、系统会挂。

这是实验性改动，不是最终方案。
"""

import sys
from pathlib import Path

MARK = "KSU_TEST_IGNORE_CRC_419"

OLD = (
    '\tpr_warn("%s: disagrees about version of symbol %s\\n",\n'
    '\t       info->name, symname);\n'
    '\treturn 0;'
)

NEW = (
    '\t/* ' + MARK + ': 实验性忽略 CRC 不匹配，仅告警 */\n'
    '\tpr_warn("%s: IGNORING version mismatch for symbol %s\\n",\n'
    '\t       info->name, symname);\n'
    '\treturn 1;'
)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("kernel")
    target = root / "kernel" / "module.c"
    if not target.exists():
        print("[SKIP] not found: %s" % target)
        return 0

    text = target.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        print("[INFO] %s: already patched" % target)
        return 0

    if OLD not in text:
        print("[ERROR] anchor not found in %s" % target)
        return 1

    text = text.replace(OLD, NEW, 1)
    target.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("[OK] %s: CRC mismatch now warns instead of failing" % target)

    # 复展确认
    i = text.find(MARK)
    print("--- patched snippet ---")
    print(text[i - 60:i + 220])
    return 0


if __name__ == "__main__":
    sys.exit(main())
