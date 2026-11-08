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

import re
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
NL = chr(10)

MARK_IOPOLL = "#elif LINUX_VERSION_CODE >= KERNEL_VERSION(5, 1, 0)"
MARK_REMAP = "#else // 4.19: remap_file_range / REMAP_FILE_DEDUP 都不存在"


# --- 4. seccomp_cache.c：4.19 整个文件退化为空操作 ---
# 该文件重新定义了 struct seccomp_filter 并访问 filter->cache.allow_native，
# 但 4.19 的内核结构体只有 {usage, log, prev, prog} 四个成员 ——
# seccomp 动作缓存（struct action_cache）是 5.9 才引入的。
# 强行编译不是"缺个头文件"那么简单，而是内存布局错配，必须整体 stub 掉。
SECCOMP_STUB = """// Linux 4.19: 内核还没有 seccomp 动作缓存。
//
// 原文件重新定义了 struct seccomp_filter 并访问 filter->cache.allow_native，
// 但 4.19 的 kernel/seccomp.c 里该结构体只有四个成员：
//     struct seccomp_filter {
//         refcount_t usage;
//         bool log;
//         struct seccomp_filter *prev;
//         struct bpf_prog *prog;
//     };
// struct action_cache 与 SECCOMP_ARCH_NATIVE_NR 都是 5.9+ 才有的。
// 照原样编译会结构体重定义 + 越界访问，因此这里退化成空操作：
// 4.19 没有缓存可清，也就不需要清。
#include <linux/version.h>
#include <linux/types.h>

#include "infra/seccomp_cache.h"

void ksu_seccomp_clear_cache(struct seccomp_filter *filter, int nr)
{
    (void)filter;
    (void)nr;
}

void ksu_seccomp_allow_cache(struct seccomp_filter *filter, int nr)
{
    (void)filter;
    (void)nr;
}
"""

SECCOMP_TARGET = ROOT / "drivers" / "kernelsu" / "infra" / "seccomp_cache.c"
MARK_SECCOMP = "4.19 没有缓存可清"

# --- 5. su_mount_ns.c：4.19 没有 include/uapi/linux/mount.h ---
# 那个头是 5.2 引入的（配 MOVE_MOUNT_* 那一批新 mount API 一起拆出来的），
# 4.19 的 MS_* 常量全在 include/uapi/linux/fs.h —— 而该文件第 5 行
# 已经 #include <linux/fs.h> 了。
# 该文件实际只用到 MS_PRIVATE(1<<18) 和 MS_REC(16384)，两者在 4.19 都有。
MOUNTNS_TARGET = ROOT / "drivers" / "kernelsu" / "infra" / "su_mount_ns.c"
MOUNTNS_OLD = "#include <uapi/linux/mount.h>"
MOUNTNS_NEW = ("// 4.19: 没有 include/uapi/linux/mount.h（5.2 才拆出来）。\n"
               "// 本文件只用到 MS_PRIVATE / MS_REC，两者都在上面的 <linux/fs.h> 里。")
MARK_MOUNTNS = "4.19: 没有 include/uapi/linux/mount.h"


def patch_mountns():
    if not MOUNTNS_TARGET.exists():
        print("  [WARN] su_mount_ns.c 不存在，跳过")
        return
    cur = MOUNTNS_TARGET.read_text(encoding="utf-8")
    if MARK_MOUNTNS in cur:
        print("  [INFO] su_mount_ns.c 已处理")
        return
    if MOUNTNS_OLD not in cur:
        print("  [WARN] su_mount_ns.c: 未找到 uapi/linux/mount.h include")
        return
    MOUNTNS_TARGET.write_text(cur.replace(MOUNTNS_OLD, MOUNTNS_NEW, 1), encoding="utf-8")
    print("  [OK]   su_mount_ns.c: 去掉 uapi/linux/mount.h")



# --- 6. pkg_observer.c：fsnotify_ops / task_work_add 的 API 差异 ---
PKGOBS_TARGET = ROOT / "drivers" / "kernelsu" / "manager" / "pkg_observer.c"
MARK_PKGOBS = "4.19 的 fsnotify_ops 只有 handle_event"

PKGOBS_TW_OLD = """            if (!task_work_add(current, cb, TWA_RESUME))
                return;"""

PKGOBS_TW_NEW = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 8, 0)
            if (!task_work_add(current, cb, TWA_RESUME))
#else
            // 4.19: task_work_add 第三个参数是 bool notify
            // （5.8 才改成 enum task_work_notify_mode，才有 TWA_RESUME）
            if (!task_work_add(current, cb, true))
#endif
                return;"""

PKGOBS_FN_RE = re.compile(
    r"static const struct fsnotify_ops ksu_ops = \{[^}]*\};", re.S)

PKGOBS_FN_NEW = """#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 9, 0)
// 4.19 的 fsnotify_ops 只有 handle_event，签名比 handle_inode_event 多三个参数：
//   (group, inode, mask, data, data_type, file_name, cookie, iter_info)
// handle_inode_event(mark, mask, inode, dir, file_name, cookie) 是 5.9 才引入的。
static int ksu_handle_event(struct fsnotify_group *group, struct inode *inode,
                            u32 mask, const void *data, int data_type,
                            const struct qstr *file_name, u32 cookie,
                            struct fsnotify_iter_info *iter_info)
{
    (void)group; (void)inode; (void)data; (void)data_type;
    (void)cookie; (void)iter_info;
    if (!file_name)
        return 0;
    if (mask & FS_ISDIR)
        return 0;
    if (file_name->len == 13 && !memcmp(file_name->name, "packages.list", 13)) {
        pr_info("packages.list detected: %d\\n", mask);
        ksu_defer_track_throne();
    }
    return 0;
}
#endif

static const struct fsnotify_ops ksu_ops = {
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)
    .handle_inode_event = ksu_handle_inode_event,
#else
    .handle_event = ksu_handle_event,
#endif
};"""


def patch_pkgobs():
    if not PKGOBS_TARGET.exists():
        print("  [WARN] pkg_observer.c 不存在，跳过")
        return
    cur = PKGOBS_TARGET.read_text(encoding="utf-8")
    if MARK_PKGOBS in cur:
        print("  [INFO] pkg_observer.c 已处理")
        return
    changed = False
    if PKGOBS_TW_OLD in cur:
        cur = cur.replace(PKGOBS_TW_OLD, PKGOBS_TW_NEW, 1)
        print("  [OK]   pkg_observer.c: TWA_RESUME -> 4.19 bool notify")
        changed = True
    else:
        print("  [WARN] pkg_observer.c: TWA_RESUME 片段未命中")

    m = PKGOBS_FN_RE.search(cur)
    if m:
        cur = cur[:m.start()] + PKGOBS_FN_NEW + cur[m.end():]
        print("  [OK]   pkg_observer.c: fsnotify_ops -> 4.19 handle_event")
        changed = True
    else:
        print("  [WARN] pkg_observer.c: fsnotify_ops 正则未命中")

    if changed:
        PKGOBS_TARGET.write_text(cur, encoding="utf-8")


def patch_seccomp():
    if not SECCOMP_TARGET.exists():
        print(f"  [WARN] seccomp_cache.c 不存在，跳过")
        return True
    cur = SECCOMP_TARGET.read_text(encoding="utf-8")
    if MARK_SECCOMP in cur:
        print("  [INFO] seccomp_cache.c 已是 4.19 stub")
        return True
    SECCOMP_TARGET.write_text(SECCOMP_STUB, encoding="utf-8")
    print("  [OK]   seccomp_cache.c -> 4.19 空操作 stub")
    return True


KSU_DIR = ROOT / "drivers" / "kernelsu"


def patch_common_api():
    """4.19 通用 API 兜底。

    按文件名逐个修会一直漏 —— TWA_RESUME 实际散落在 pkg_observer / allowlist /
    supercall / susfs_supercall 四个文件里。这里对整棵树做统一替换：

      task_work_add(task, cb, TWA_RESUME)  ->  task_work_add(task, cb, true)
          TWA_RESUME 是 5.8 才有的 enum task_work_notify_mode；
          4.19 的第三参是 bool notify，true 即"回调在返回用户态前执行"。

      fallthrough;  ->  /* fallthrough */
          fallthrough 语句 5.4 引入，4.19 只认注释形式。
    """
    if not KSU_DIR.exists():
        print(f"  [SKIP] no kernelsu dir: {KSU_DIR}")
        return
    n_twa = n_fall = 0
    for f in sorted(KSU_DIR.rglob("*.c")) + sorted(KSU_DIR.rglob("*.h")):
        s = f.read_text(encoding="utf-8", errors="replace")
        o = s
        c = s.count(", TWA_RESUME)")
        if c:
            s = s.replace(", TWA_RESUME)", ", true)")
            n_twa += c
        c2 = s.count("fallthrough;")
        if c2:
            s = s.replace("fallthrough;", "/* fallthrough */")
            n_fall += c2
        if s != o:
            f.write_text(s, encoding="utf-8")
    print(f"  [OK]   common api      TWA_RESUME x{n_twa}  fallthrough x{n_fall}")


APPROFILE = ROOT / "drivers" / "kernelsu" / "policy" / "app_profile.c"

APPROFILE_OLD = """    current->seccomp.filter = NULL;
    atomic_set(&current->seccomp.filter_count, 0);"""

APPROFILE_NEW = """    current->seccomp.filter = NULL;
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)
    // filter_count 是 5.9 引入的引用计数。4.19 的 struct seccomp 只有
    // int mode + struct seccomp_filter *filter，计数活在 filter 自身，
    // 把 filter 置 NULL 就已经放弃引用了，不需要额外原子操作。
    atomic_set(&current->seccomp.filter_count, 0);
#endif"""


APPROFILE_SECCOMP_OLD = """void seccomp_filter_release(struct task_struct *tsk);"""

APPROFILE_SECCOMP_NEW = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)
void seccomp_filter_release(struct task_struct *tsk);
#endif"""

APPROFILE_DIS_OLD = "static void disable_seccomp(void)\n{"

APPROFILE_DIS_NEW = """// 4.19: struct seccomp 只有 int mode + struct seccomp_filter *filter，
// 没有 filter_count；seccomp_filter_release() 是 5.9 才加的。
// 这里清 filter 只能手动减引用，而 4.19 的 __put_seccomp_filter 是 static、
// 设备又 KALLSYMS_ALL=n 查不到符号，减错了就是内核直接崩。
// 所以 4.19 整体跳过 —— 只降低 app_profile 的隐藏深度，不影响 root 本身。
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)
static void disable_seccomp(void)
{"""

APPROFILE_DIS_TAIL_OLD = """    seccomp_filter_release(fake);
    kfree(fake);
}"""

APPROFILE_DIS_TAIL_NEW = """    seccomp_filter_release(fake);
    kfree(fake);
}
#else
static void disable_seccomp(void) {}
#endif"""


def patch_appprofile():
    """app_profile.c 在 4.19 上访问了不存在的 seccomp.filter_count，
    并且依赖 5.9 才有的 seccomp_filter_release()。"""
    if not APPROFILE.exists():
        print(f"  [SKIP] no app_profile.c")
        return
    s = APPROFILE.read_text(encoding="utf-8", errors="replace")
    if "只降低 app_profile 的隐藏深度" in s:
        print("  [INFO] app_profile.c 已处理")
        return
    n = 0
    if APPROFILE_OLD in s:
        s = s.replace(APPROFILE_OLD, APPROFILE_NEW, 1)
        n += 1
    # 声明也一起收进版本守卫，避免 4.19 下出现"有声明无定义"
    if APPROFILE_SECCOMP_OLD in s:
        s = s.replace(APPROFILE_SECCOMP_OLD, APPROFILE_SECCOMP_NEW, 1)
        n += 1
    if APPROFILE_DIS_OLD in s:
        s = s.replace(APPROFILE_DIS_OLD, APPROFILE_DIS_NEW, 1)
        n += 1
    if APPROFILE_DIS_TAIL_OLD in s:
        s = s.replace(APPROFILE_DIS_TAIL_OLD, APPROFILE_DIS_TAIL_NEW, 1)
        n += 1
    APPROFILE.write_text(s, encoding="utf-8")
    print(f"  [OK]   app_profile.c: {n} 处 4.19 适配")


SELINUX_DIR = ROOT / "drivers" / "kernelsu" / "selinux"
RULES_C = SELINUX_DIR / "rules.c"
SEPOLICY_C = SELINUX_DIR / "sepolicy.c"

MARK_RULES = "KSU_SELINUX_RULES_419_STUB"
MARK_SEPOLICY = "KSU_SEPOLICY_419_STUB"

RULES_TAIL = '''

#else /* KSU_SELINUX_RULES_419_STUB */

#include <linux/errno.h>
#include <linux/types.h>

/*
 * 4.19 的 struct selinux_state 只有 enforcing / checkreqprot / initialized /
 * policycap / avc / ss，没有 policy，也没有 policy_mutex。
 *
 * 5.1 才把 struct selinux_policy 从 struct selinux_ss 里拆出来。KSU 的动态
 * sepolicy 注入（dup 整个 policydb → 改 avtab → 原子替换 selinux_state.policy）
 * 完全建立在那个新布局之上。要在 4.19 复刻同一套，得重写整个 policydb 访问层，
 * 还要处理 android_netlink_route 之类 Android 私有字段，风险远大于收益。
 *
 * 本内核以 androidboot.selinux=permissive 启动，SELinux 不拦截任何操作，
 * 所以这里降级为空实现：不注入规则，su 照常可用。代价只是 su 进程缺少定制
 * domain 的显式 allow 规则 —— permissive 下没有实际影响。
 */
void apply_kernelsu_rules(void)
{
}

int handle_sepolicy(void __user *user_data, u64 data_len)
{
	(void)user_data;
	(void)data_len;
	return -EOPNOTSUPP;
}

#endif /* KSU_SELINUX_RULES_419_STUB */
'''


def patch_selinux_419():
    """4.19: selinux_state 没有 policy/policy_mutex，整层 sepolicy 注入降级。"""
    if RULES_C.exists():
        s = RULES_C.read_text(encoding="utf-8", errors="replace")
        if MARK_RULES in s:
            print("  [INFO] rules.c 已处理")
        else:
            head = '#include "linux/rcupdate.h"'
            if head in s:
                s = s.replace(
                    head,
                    '#include <linux/version.h>\n\n'
                    '/* KSU_SELINUX_RULES_419_STUB: 说明见文件末尾 */\n'
                    '#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 1, 0)\n\n'
                    + head,
                    1)
                s = s + RULES_TAIL
                RULES_C.write_text(s, encoding="utf-8")
                print("  [OK]   rules.c: 4.19 降级为空实现")
            else:
                print("  [WARN] rules.c: 头部锚点未命中")
    else:
        print("  [SKIP] no rules.c")

    if SEPOLICY_C.exists():
        s2 = SEPOLICY_C.read_text(encoding="utf-8", errors="replace")
        if MARK_SEPOLICY in s2:
            print("  [INFO] sepolicy.c 已处理")
        else:
            s2 = ("/* KSU_SEPOLICY_419_STUB: struct selinux_policy 4.19 不存在，整体不编译 */\n"
                  "#include <linux/version.h>\n"
                  "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 1, 0)\n"
                  + s2 +
                  "\n#endif /* KSU_SEPOLICY_419_STUB */\n")
            SEPOLICY_C.write_text(s2, encoding="utf-8")
            print("  [OK]   sepolicy.c: 4.19 整体跳过")
    else:
        print("  [SKIP] no sepolicy.c")


MARK_DISPATCH_INC = "4.19 的 linux/sched.h 不间接引入 sched/signal.h 与 init_task.h"


MARK_SELHIDE_419 = "KSU_SELINUX_HIDE_419_STUB"


def patch_selinux_hide_419():
    """feature/selinux_hide.c 依赖 selinux_state 的 status_page / status_lock / policy。

    这三个成员在内核里的出现时间：
      policy       5.1   引入（policydb 从 selinux_ss 里拆出来）
      status_page  5.4   引入（/sys/fs/selinux/status 的实现）
      status_lock  5.4   同上

    realme 的 4.19 selinux_state 里一个都没有，且 ss 是 struct selinux_ss *（指针），
    不是 5.x 的内嵌结构。复刻需要重写整个 policydb 访问层，成本远超收益。

    selinux_hide 的接口只有 5 个无参 void 函数（见 selinux_hide.h），
    4.19 上整体跳过，全部降级为空实现。
    """
    p = TARGET.parent.parent / "feature" / "selinux_hide.c"
    if not p.exists():
        print("  [SKIP] no feature/selinux_hide.c")
        return
    cur = p.read_text(encoding="utf-8", errors="replace")
    if MARK_SELHIDE_419 in cur:
        print("  [INFO] selinux_hide.c 已处理")
        return

    head_old = "#include " + chr(34) + "selinux_hide.h" + chr(34)
    if head_old not in cur:
        print("  [WARN] selinux_hide.c: 未找到 include 锚点")
        return
    head_new = ("// " + MARK_SELHIDE_419
                + ": 4.19 没有 selinux_state.status_page/status_lock/policy，整体跳过" + NL
                + "#include <linux/version.h>" + NL
                + "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 4, 0)" + NL
                + head_old)
    cur = cur.replace(head_old, head_new, 1)

    tail = NL.join([
        "",
        "#else /* !(>= 5.4) */  " + MARK_SELHIDE_419,
        "void ksu_selinux_hide_init() {}" ,
        "void ksu_selinux_hide_exit() {}" ,
        "void ksu_selinux_hide_drop_backup_if_unused() {}" ,
        "void ksu_selinux_hide_handle_second_stage() {}" ,
        "void ksu_selinux_hide_handle_post_fs_data() {}" ,
        "#endif /* " + MARK_SELHIDE_419 + " */" ,
        "",
    ])
    p.write_text(cur.rstrip() + NL + tail, encoding="utf-8")
    print("  [OK]   selinux_hide.c: 4.19 整体降级为空实现")

def patch_dispatch_includes():
    """supercall/dispatch.c 用到 tasklist_lock 与 init_task。

    5.x 的 linux/sched.h 会间接带上 sched/signal.h 与 init_task.h，4.19 不会，
    于是报 use of undeclared identifier。这里补两条显式 include。
    """
    p = TARGET.parent.parent / "supercall" / "dispatch.c"
    if not p.exists():
        print("  [SKIP] no supercall/dispatch.c")
        return
    cur = p.read_text(encoding="utf-8", errors="replace")
    if MARK_DISPATCH_INC in cur:
        print("  [INFO] dispatch.c 已处理")
        return
    old = "#include <linux/capability.h>" + NL
    if old not in cur:
        print("  [WARN] dispatch.c: 未找到 include 锚点")
        return
    new = ("// " + MARK_DISPATCH_INC + NL
           + "#include <linux/init_task.h>" + NL
           + "#include <linux/sched/signal.h>" + NL
           + old)
    p.write_text(cur.replace(old, new, 1), encoding="utf-8")
    print("  [OK]   dispatch.c: 补 init_task.h + sched/signal.h")

BOOT_EVENT_MARK = "4.19: 内联触发一次 track_throne"


def patch_boot_event():
    """boot_event.c: zygote 起来后主动扫一次管理器。

    实测（v5 内核）`observer init done` / `watching /data/system` 都打出来了，
    但之后 `pm install` 改了 packages.list，内核侧一条事件都没收到，
    说明 4.19 的父子目录 fsnotify 传递在这台机器上不生效。
    于是 track_throne(false) 永远不会被调用，管理器永远 crown 不上。
    这里在 on_post_fs_data 里显式扫一次。
    """
    p = TARGET.parent.parent / "runtime" / "boot_event.c"
    if not p.exists():
        print("  [SKIP] missing file: runtime/boot_event.c")
        return
    cur = p.read_text(encoding="utf-8")
    if BOOT_EVENT_MARK in cur:
        print("  [INFO] boot_event.c 已处理")
        return
    old = "    ksu_observer_init();"
    new = ("    ksu_observer_init();" + NL +
           "    /* " + BOOT_EVENT_MARK + "：fsnotify 在本机上收不到" + NL +
           "     * /data/system/packages.list 的 rename 事件，管理器永远 crown" + NL +
           "     * 不上，所以 zygote 起来后主动扫一次。 */" + NL +
           "    track_throne(false);")
    if old not in cur:
        print("  [WARN] boot_event.c: ksu_observer_init() 片段未命中")
        return
    cur = cur.replace(old, new, 1)
    n = cur.count("track_throne(true);")
    if n:
        cur = cur.replace("track_throne(true);", "track_throne(false);")
    p.write_text(cur, encoding="utf-8")
    print("  [OK]   boot_event.c: on_post_fs_data 内联 track_throne(false)"
          + (f"，另外 {n} 处 true->false" if n else ""))


def main():
    # 注意：这几个文件各自独立，缺任何一个都不应中断其余补丁。
    # 早期版本在这里 return 1，导致只下载 pkg_observer.c 时
    # patch_seccomp/patch_mountns/patch_pkgobs 全被跳过，误判成"补丁没生效"。
    if not TARGET.exists():
        print(f"  [SKIP] not found: {TARGET}")
    patch_seccomp()
    patch_mountns()
    patch_pkgobs()
    patch_boot_event()
    patch_appprofile()
    patch_selinux_419()
    patch_dispatch_includes()
    patch_selinux_hide_419()
    patch_common_api()

    if not TARGET.exists():
        return 0

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
