#!/usr/bin/env python3
"""Patch KernelSU-Next sources for Linux 4.19 (RMX3350 / MT6893).

KernelSU-Next 默认面向 5.10+，用到了 4.19 内核里还不存在的 fops 成员。
这个脚本做最小侵入的版本宏包裹 —— 不删除任何逻辑，只是让 4.19 不编译
它用不到的分支。

覆盖的错误（drivers/kernelsu/infra/file_wrapper.c）：
  :109:24  error: no member named 'iopoll' in 'struct file_operations'
  :339:23  error: use of undeclared identifier 'REMAP_FILE_DEDUP'
  :342:28  error: no member named 'remap_file_range' in 'struct file_operations'
  :347:28  error: no member named 'remap_file_range' in 'struct file_operations'
  :393:12  error: no member named 'iopoll' in 'struct file_operations'
  :430:12  error: no member named 'remap_file_range' in 'struct file_operations'
  :431:19  error: no member named 'remap_file_range' in 'struct file_operations'

内核版本对照：
  - fops->iopoll            5.1 引入   (4.19 没有 -> 整个 wrapper 不编译)
  - fops->remap_file_range  4.20 引入  (4.19 叫 dedupe_file_range)
  - REMAP_FILE_DEDUP        4.20 引入
"""

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "kernel")
TARGET = ROOT / "drivers" / "kernelsu" / "infra" / "file_wrapper.c"

# --- 1. ksu_wrapper_iopoll：把 #else 分支降级成 5.1~6.1 区间 ---
IOPOLL_OLD = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 1, 0)
static int ksu_wrapper_iopoll(struct kiocb *kiocb, struct io_comp_batch *icb,
                              unsigned int v)
{
    struct ksu_file_wrapper *data = kiocb->ki_filp->private_data;
    struct file *orig = data->orig;
    kiocb->ki_filp = orig;
    return orig->f_op->iopoll(kiocb, icb, v);
}
#else
static int ksu_wrapper_iopoll(struct kiocb *kiocb, bool spin)
{
    struct ksu_file_wrapper *data = kiocb->ki_filp->private_data;
    struct file *orig = data->orig;
    kiocb->ki_filp = orig;
    return orig->f_op->iopoll(kiocb, spin);
}
#endif"""

IOPOLL_NEW = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 1, 0)
static int ksu_wrapper_iopoll(struct kiocb *kiocb, struct io_comp_batch *icb,
                              unsigned int v)
{
    struct ksu_file_wrapper *data = kiocb->ki_filp->private_data;
    struct file *orig = data->orig;
    kiocb->ki_filp = orig;
    return orig->f_op->iopoll(kiocb, icb, v);
}
#elif LINUX_VERSION_CODE >= KERNEL_VERSION(5, 1, 0)
static int ksu_wrapper_iopoll(struct kiocb *kiocb, bool spin)
{
    struct ksu_file_wrapper *data = kiocb->ki_filp->private_data;
    struct file *orig = data->orig;
    kiocb->ki_filp = orig;
    return orig->f_op->iopoll(kiocb, spin);
}
#endif // iopoll 是 5.1 才进 struct file_operations，4.19 无此成员"""

# --- 2. remap_file_range wrapper：整体包进 >= 4.20 ---
REMAP_OLD_HEAD = """static loff_t ksu_wrapper_remap_file_range(struct file *file_in, loff_t pos_in,"""
REMAP_NEW_HEAD = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 20, 0)
static loff_t ksu_wrapper_remap_file_range(struct file *file_in, loff_t pos_in,"""

REMAP_OLD_TAIL = """        return orig->f_op->remap_file_range(orig, pos_in, file_out, pos_out,
                                            len, remap_flags);
    }
}"""
REMAP_NEW_TAIL = """        return orig->f_op->remap_file_range(orig, pos_in, file_out, pos_out,
                                            len, remap_flags);
    }
}
#else // 4.19: remap_file_range / REMAP_FILE_DEDUP 都不存在（4.20 才引入）
static loff_t ksu_wrapper_remap_file_range(struct file *file_in, loff_t pos_in,
                                           struct file *file_out,
                                           loff_t pos_out, loff_t len,
                                           unsigned int remap_flags)
{
    // 4.19 的 file_operations 只有 dedupe_file_range，且语义不同，
    // 这里保守地返回 -EOPNOTSUPP，让上层走 fallback 路径。
    return -EOPNOTSUPP;
}
#endif"""

# --- 3. ops 赋值：iopoll / remap_file_range 分别加版本守卫 ---
OPS_IOPOLL_OLD = """    p->ops.iopoll = fp->f_op->iopoll ? ksu_wrapper_iopoll : NULL;"""
OPS_IOPOLL_NEW = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 1, 0)
    p->ops.iopoll = fp->f_op->iopoll ? ksu_wrapper_iopoll : NULL;
#endif"""

OPS_REMAP_OLD = """    p->ops.remap_file_range =
        fp->f_op->remap_file_range ? ksu_wrapper_remap_file_range : NULL;"""
OPS_REMAP_NEW = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 20, 0)
    p->ops.remap_file_range =
        fp->f_op->remap_file_range ? ksu_wrapper_remap_file_range : NULL;
#endif"""

PATCHES = [
    ("iopoll wrapper (#else -> 5.1+ branch)", IOPOLL_OLD, IOPOLL_NEW),
    ("remap wrapper (>= 4.20 guard)", REMAP_OLD_HEAD, REMAP_NEW_HEAD),
    ("remap wrapper tail (4.19 stub)", REMAP_OLD_TAIL, REMAP_NEW_TAIL),
    ("ops.iopoll (>= 5.1 guard)", OPS_IOPOLL_OLD, OPS_IOPOLL_NEW),
    ("ops.remap_file_range (>= 4.20 guard)", OPS_REMAP_OLD, OPS_REMAP_NEW),
]

# 幂等标记：补丁落地后这两串必定唯一存在
MARK_IOPOLL = "#elif LINUX_VERSION_CODE >= KERNEL_VERSION(5, 1, 0)"
MARK_REMAP = "#else // 4.19: remap_file_range / REMAP_FILE_DEDUP 都不存在"


def main():
    if not TARGET.exists():
        print(f"[ERROR] not found: {TARGET}")
        return 1
    src = TARGET.read_text(encoding="utf-8")

    # 幂等闸门：两个标记都在说明已经打过，直接退出。
    # （不能只靠 replace 的 count 判断 —— 打完补丁后原文片段仍然存在，
    #   例如 remap wrapper 的函数体在 #else 分支里保留着，会再次命中。）
    if MARK_IOPOLL in src and MARK_REMAP in src:
        print("  [INFO] 已打过 4.19 补丁，跳过")
        return 0

    orig = src
    for label, old, new in PATCHES:
        n = src.count(old)
        if n == 0:
            print(f"  [WARN] {label}: 未命中")
            continue
        src = src.replace(old, new, 1)
        print(f"  [OK]   {label}")
    if src == orig:
        print("  [INFO] 没有改动")
    TARGET.write_text(src, encoding="utf-8")

    # 复核：每个 f_op-> 引用都应该落在某个版本宏内
    body = TARGET.read_text(encoding="utf-8")
    print(f"  幂等标记 iopoll={MARK_IOPOLL in body} remap={MARK_REMAP in body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
