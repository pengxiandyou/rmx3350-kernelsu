#!/usr/bin/env python3
"""把 KernelSU 注入内核树的 Kconfig / Makefile。幂等。"""
import sys, os

def patch_kconfig(path):
    s = open(path, encoding='utf-8', errors='replace').read()
    if 'drivers/kernelsu/Kconfig' in s:
        print('[=] drivers/Kconfig already patched'); return
    idx = s.rfind('endmenu')
    assert idx > 0, 'endmenu not found'
    s = s[:idx] + 'source "drivers/kernelsu/Kconfig"\n' + s[idx:]
    open(path, 'w', encoding='utf-8').write(s)
    print('[+] drivers/Kconfig patched')

def patch_makefile(path):
    s = open(path, encoding='utf-8', errors='replace').read()
    if 'CONFIG_KSU' in s:
        print('[=] drivers/Makefile already patched'); return
    s = s.rstrip('\n') + '\n\nobj-$(CONFIG_KSU) += kernelsu/\n'
    open(path, 'w', encoding='utf-8').write(s)
    print('[+] drivers/Makefile patched')

if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    patch_kconfig(os.path.join(root, 'drivers/Kconfig'))
    patch_makefile(os.path.join(root, 'drivers/Makefile'))
