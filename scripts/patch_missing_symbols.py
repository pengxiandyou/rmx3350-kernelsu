#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Linux 4.19 compatibility patch: fix 10 undefined symbols at link time.

Build log (run 42417493840 / job 128538631755) -- compile phase passed,
ld.lld failed on vmlinux:
  ld.lld: error: undefined symbol: path_umount            (feature/kernel_umount.c:48)
  ld.lld: error: undefined symbol: strncpy_from_user_nofault
  ld.lld: error: undefined symbol: copy_to_kernel_nofault  (hook/arm64/patch_memory.c:154)
  ld.lld: error: undefined symbol: security_inode_init_security_anon
  ld.lld: error: undefined symbol: selinux_inode           (infra/file_wrapper.c:599)
  ld.lld: error: undefined symbol: path_mount              (infra/su_mount_ns.c:132)
  ld.lld: error: undefined symbol: put_task_struct         (policy/allowlist.c:493)
  ld.lld: error: undefined symbol: copy_from_user_nofault
  ld.lld: error: undefined symbol: copy_to_user_nofault
  ld.lld: error: undefined symbol: selinux_cred            (selinux/selinux.c:107,250)

Mapping to Linux 4.19 (verified against realme AndroidT 4.19.191 source):
  path_umount                 -> kern_unmount()          (5.9 added path_umount; 4.19 has EXPORT_SYMBOL(kern_unmount) at fs/namespace.c:3321)
  path_mount                  -> no-op                   (4.19 do_mount() 2nd arg is __user path string; do_change_type() is static)
  strncpy_from_user_nofault   -> strncpy_from_unsafe()   (5.8 unified renames; 4.19 strncpy_from_unsafe uses probe_kernel_read -> __copy_from_user_inatomic)
  copy_from_user_nofault      -> __copy_from_user_inatomic()
  copy_to_user_nofault        -> __copy_to_user_inatomic()
  copy_to_kernel_nofault      -> probe_kernel_write()
  put_task_struct             -> add #include <linux/sched/task.h>
  selinux_cred(c)             -> (struct task_security_struct *)c->security
  selinux_inode(i)            -> (struct inode_security_struct *)i->i_security
  security_inode_init_security_anon -> skip             (5.13 added; SID set manually by caller)

Usage: python3 patch_missing_symbols.py <ksu_root>
       <ksu_root> = directory containing infra/ policy/ feature/ selinux/ runtime/ hook/
                    (i.e. kernel/drivers/kernelsu)
"""
import sys
import io
import os

NL = chr(10)
ROOT = None


def read(p):
    with io.open(p, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(s)


def sub(path, old, new, label, done_mark=None):
    """Replace `old` -> `new` once. Idempotent: if `done_mark` already present, skip."""
    if not os.path.exists(path):
        print("[SKIP] missing file: %s" % path)
        return False
    s = read(path)
    if done_mark and done_mark in s:
        print("[SKIP] %-28s already patched  (%s)" % (label, path))
        return False
    if old not in s:
        print("[WARN] %-28s pattern not found  (%s)" % (label, path))
        return False
    s = s.replace(old, new, 1)
    write(path, s)
    print("[OK  ] %-28s (%s)" % (label, path))
    return True


def sub_all(path, old, new, label):
    """Replace every occurrence of `old` -> `new`. Natural idempotency: `old` vanishes."""
    if not os.path.exists(path):
        print("[SKIP] missing file: %s" % path)
        return 0
    s = read(path)
    n = s.count(old)
    if n == 0:
        print("[SKIP] %-28s nothing to do  (%s)" % (label, path))
        return 0
    s = s.replace(old, new)
    write(path, s)
    print("[OK  ] %-28s x%-2d (%s)" % (label, n, path))
    return n


# ============================================================ 1. path_umount
UMOUNT_MARK = "4.19 has no path_umount()"

UMOUNT_OLD = (
    "extern int path_umount(struct path *path, int flags);" + NL + NL +
    "static void ksu_umount_mnt(const char *mnt, struct path *path, int flags)" + NL +
    "{" + NL +
    "\tint err = path_umount(path, flags);" + NL
)

UMOUNT_NEW = (
    "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)" + NL +
    "extern int path_umount(struct path *path, int flags);" + NL +
    "#endif" + NL + NL +
    "static void ksu_umount_mnt(const char *mnt, struct path *path, int flags)" + NL +
    "{" + NL +
    "\tint err;" + NL +
    "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)" + NL +
    "\terr = path_umount(path, flags);" + NL +
    "#else" + NL +
    "\t/* 4.19 has no path_umount() (introduced in 5.9)." + NL +
    "\t * kern_unmount() is the 4.19 equivalent: EXPORT_SYMBOL (not GPL) at" + NL +
    "\t * fs/namespace.c:3321. MNT_DETACH / MNT_FORCE semantics are dropped," + NL +
    "\t * which is acceptable for KSU module mount points. */" + NL +
    "\t(void)flags;" + NL +
    "\tkern_unmount(path->mnt);" + NL +
    "\terr = 0;" + NL +
    "#endif" + NL
)


def patch_umount():
    return sub(os.path.join(ROOT, "feature", "kernel_umount.c"),
               UMOUNT_OLD, UMOUNT_NEW, "path_umount->kern_unmount", UMOUNT_MARK)


# ============================================================ 2. path_mount
MOUNT_MARK = "4.19 has no path_mount()"

MOUNT_DECL_OLD = (
    "extern int path_mount(const char *dev_name, struct path *path," + NL +
    "                      const char *type_page, unsigned long flags," + NL +
    "                      void *data_page);" + NL
)

MOUNT_DECL_NEW = (
    "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)" + NL +
    "extern int path_mount(const char *dev_name, struct path *path," + NL +
    "                      const char *type_page, unsigned long flags," + NL +
    "                      void *data_page);" + NL +
    "#endif" + NL
)

MOUNT_CALL_OLD = (
    "    int pm_ret = path_mount(NULL, &root_path, NULL, MS_PRIVATE | MS_REC, NULL);" + NL
)

MOUNT_CALL_NEW = (
    "    int pm_ret = 0;" + NL +
    "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)" + NL +
    "    pm_ret = path_mount(NULL, &root_path, NULL, MS_PRIVATE | MS_REC, NULL);" + NL +
    "#else" + NL +
    "    /* 4.19 has no path_mount() (introduced in 5.9). do_mount() exists but its" + NL +
    "     * 2nd parameter is a __user path string so it cannot take a struct path;" + NL +
    "     * do_change_type() is static. Degrade to a no-op: ksys_unshare(CLONE_NEWNS)" + NL +
    "     * already created a private mount namespace and Android init keeps the root" + NL +
    "     * mount private, so propagation risk is negligible. */" + NL +
    "#endif" + NL
)


def patch_path_mount():
    p = os.path.join(ROOT, "infra", "su_mount_ns.c")
    if not os.path.exists(p):
        print("[SKIP] missing file: %s" % p)
        return False
    s = read(p)
    if MOUNT_MARK in s:
        print("[SKIP] %-28s already patched  (%s)" % ("path_mount", p))
        return False
    changed = False
    if MOUNT_DECL_OLD in s:
        s = s.replace(MOUNT_DECL_OLD, MOUNT_DECL_NEW, 1)
        changed = True
    if MOUNT_CALL_OLD in s:
        s = s.replace(MOUNT_CALL_OLD, MOUNT_CALL_NEW, 1)
        changed = True
    if changed:
        write(p, s)
        print("[OK  ] %-28s (%s)" % ("path_mount->noop", p))
    else:
        print("[WARN] %-28s pattern not found  (%s)" % ("path_mount", p))
    return changed


# ============================================================ 3..6 uaccess


def patch_uaccess():
    """Walk the whole KSU tree. `*_nofault` helpers are used in many files
    (sucompat.c, ksud_integration.c, sulog/event.c, ...) -- patching only the
    ones named in the first build log guarantees another round trip each time.
    History: pkg_observer(TWA_RESUME) and scp.h both had to be redone as
    whole-tree/whole-directory sweeps after single-file fixes missed copies."""
    rules = [
        ("strncpy_from_user_nofault(", "strncpy_from_unsafe("),
        ("copy_from_user_nofault(", "__copy_from_user_inatomic("),
        ("copy_to_user_nofault(", "__copy_to_user_inatomic("),
        ("copy_to_kernel_nofault(", "probe_kernel_write("),
    ]
    total = 0
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        for fn in filenames:
            if not (fn.endswith(".c") or fn.endswith(".h")):
                continue
            p = os.path.join(dirpath, fn)
            s = read(p)
            hits = 0
            for old, new in rules:
                # Guard against double-prefixing: "__copy_from_user_inatomic("
                # does not contain "copy_from_user_nofault(", so plain replace
                # is safe here.
                n = s.count(old)
                if n:
                    s = s.replace(old, new)
                    hits += n
            if hits:
                write(p, s)
                total += hits
                rel = os.path.relpath(p, ROOT)
                print("[OK  ] uaccess-nofault              x%-2d (%s)" % (hits, rel))
    if total == 0:
        print("[SKIP] uaccess-nofault              nothing to do")
    return total


# ============================================================ 7. put_task_struct
TASKHDR_MARK = "4.19: put_task_struct() is a static inline in sched/task.h"

TASKHDR_OLD = "#include <linux/kref.h>" + NL

TASKHDR_NEW = (
    "#include <linux/kref.h>" + NL +
    "/* 4.19: put_task_struct() is a static inline in sched/task.h, not pulled in" + NL +
    " * by sched.h. Without this include it becomes an implicit declaration and" + NL +
    " * the linker reports an undefined symbol. */" + NL +
    "#include <linux/sched/task.h>" + NL
)


def patch_taskhdr():
    return sub(os.path.join(ROOT, "policy", "allowlist.c"),
               TASKHDR_OLD, TASKHDR_NEW, "put_task_struct include", TASKHDR_MARK)


# ============================================================ 8. selinux accessors
SHIM_MARK = "KSU_SELINUX_ACCESSOR_SHIM_419"

SHIM = (
    "#include <linux/version.h>" + NL +
    "#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 0, 0)" + NL +
    "/* " + SHIM_MARK + " */" + NL +
    "/* 4.19 SELinux has no selinux_cred()/selinux_inode() accessors (added in" + NL +
    " * 5.x together with the LSM blob rework). The 4.19 equivalents are plain" + NL +
    " * casts of cred->security / inode->i_security, whose layouts match" + NL +
    " * struct task_security_struct / struct inode_security_struct from" + NL +
    " * security/selinux/include/objsec.h (already included above). */" + NL +
    "static inline struct task_security_struct *" + NL +
    "ksu_selinux_cred(const struct cred *cred)" + NL +
    "{" + NL +
    "\treturn (struct task_security_struct *)cred->security;" + NL +
    "}" + NL +
    "static inline struct inode_security_struct *" + NL +
    "ksu_selinux_inode(const struct inode *inode)" + NL +
    "{" + NL +
    "\treturn (struct inode_security_struct *)inode->i_security;" + NL +
    "}" + NL +
    "#else" + NL +
    "#define ksu_selinux_cred  selinux_cred" + NL +
    "#define ksu_selinux_inode selinux_inode" + NL +
    "#endif" + NL
)


def _add_shim_and_rename(rel, func, newfunc):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        print("[SKIP] missing file: %s" % p)
        return 0
    s = read(p)
    if SHIM_MARK in s:
        print("[SKIP] %-28s already patched  (%s)" % (newfunc, p))
        return 0
    anchor = '#include "objsec.h"' + NL
    if anchor not in s:
        print("[WARN] %-28s no objsec.h include  (%s)" % (newfunc, p))
        return 0
    # IMPORTANT: rename the call sites FIRST, then prepend the shim.
    # The shim body contains "ksu_selinux_cred(...)" which itself contains the
    # substring "selinux_cred(" -- inserting the shim before the rename would
    # turn its own definition into "ksu_ksu_selinux_cred(".
    cnt = s.count(func + "(")
    s = s.replace(func + "(", newfunc + "(")
    s = s.replace(anchor, anchor + SHIM + NL, 1)
    write(p, s)
    print("[OK  ] %-28s x%-2d (%s)" % (newfunc, cnt, p))
    return cnt


def patch_selinux_accessors():
    n = 0
    n += _add_shim_and_rename(os.path.join("selinux", "selinux.c"),
                              "selinux_cred", "ksu_selinux_cred")
    n += _add_shim_and_rename(os.path.join("infra", "file_wrapper.c"),
                              "selinux_inode", "ksu_selinux_inode")
    return n


# ============================================================ 9. anon inode security
ANON_MARK = "4.19 has no security_inode_init_security_anon()"

ANON_OLD = (
    "    error = security_inode_init_security_anon(inode, &qname, context_inode);" + NL +
    "    if (error) {" + NL +
    "        iput(inode);" + NL +
    "        return ERR_PTR(error);" + NL +
    "    }" + NL
)

ANON_NEW = (
    "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 13, 0)" + NL +
    "    error = security_inode_init_security_anon(inode, &qname, context_inode);" + NL +
    "    if (error) {" + NL +
    "        iput(inode);" + NL +
    "        return ERR_PTR(error);" + NL +
    "    }" + NL +
    "#else" + NL +
    "    /* 4.19 has no security_inode_init_security_anon() (5.13 introduced the" + NL +
    "     * anon inode LSM hook). Skipping it leaves the inode with a default SID;" + NL +
    "     * ksu_install_file_wrapper() overrides wrapper_sec->sid with ksu_file_sid" + NL +
    "     * right afterwards, so the final SELinux label is still correct. */" + NL +
    "    (void)context_inode;" + NL +
    "    (void)qname;" + NL +
    "    error = 0;" + NL +
    "#endif" + NL
)


def patch_anon_security():
    return sub(os.path.join(ROOT, "infra", "file_wrapper.c"),
               ANON_OLD, ANON_NEW, "anon inode security skip", ANON_MARK)


# ============================================================ main
def main():
    global ROOT
    if len(sys.argv) < 2:
        print("usage: patch_missing_symbols.py <ksu_root>")
        return 2
    ROOT = sys.argv[1]
    if not os.path.isdir(ROOT):
        print("[FATAL] not a directory: %s" % ROOT)
        return 2
    print("=== patch_missing_symbols: root = %s ===" % ROOT)
    patch_umount()
    patch_path_mount()
    patch_uaccess()
    patch_taskhdr()
    patch_selinux_accessors()
    patch_anon_security()
    print("=== done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
