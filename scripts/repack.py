#!/usr/bin/env python3
"""
boot.img 重组工具（Android boot header v2，MTK / oplus ossi 平台专用）

用途：把 NeoCore 的 kernel（含 KernelSU + SuSFS，4.19.191）
      装进 RMX3350 自己 Android 13 原厂的 boot.img，
      保留本机的 ramdisk / dtb / 头部地址，跨 SKU 风险最小。

用法：
  python repack.py <原厂A13_boot.img> <neo_kernel.gz> <输出.img> [选项]

选项：
  --permissive   在 cmdline 追加 androidboot.selinux=permissive enforcing=0
                 （SuSFS 完整功能需要；不加则保持原厂 enforcing）
  --no-ksu-cmd   不追加 kernelsu.force_feature
  --keep-avb     保留原 AVB footer 原始字节（默认也会保留尾部，此开关仅显式声明）

输出后校验：python verify_boot.py <输出.img>
"""
import struct, sys, os

PAGE = 2048

def al(x):
    return (x + PAGE - 1) // PAGE * PAGE

def read_header(d):
    magic = d[:8]
    if magic != b'ANDROID!':
        raise SystemExit(f"not an Android boot image: {magic!r}")
    ks, ka = struct.unpack_from('<II', d, 0x08)
    rs, ra = struct.unpack_from('<II', d, 0x10)
    ss, sa = struct.unpack_from('<II', d, 0x18)
    ta, ps = struct.unpack_from('<II', d, 0x20)
    hv, ov = struct.unpack_from('<II', d, 0x28)
    return dict(kernel_size=ks, kernel_addr=ka, ramdisk_size=rs, ramdisk_addr=ra,
                second_size=ss, second_addr=sa, tags_addr=ta, page_size=ps,
                header_version=hv, os_version=ov)

def parse(path):
    d = open(path, 'rb').read()
    h = read_header(d)
    ps = h['page_size']
    if ps == 0:
        ps = PAGE
    # header_size 字段在 0x66C（v2）
    hdr_size = 1660
    if len(d) >= 0x670:
        hs, = struct.unpack_from('<I', d, 0x66C)
        if 0 < hs <= ps:
            hdr_size = hs
    dtb_size = 0
    dtb_addr = 0
    if len(d) >= 0x680:
        dtb_size, dtb_addr = struct.unpack_from('<IQ', d, 0x670)
    k_off = ps
    r_off = al(k_off + h['kernel_size'])
    d_off = al(r_off + h['ramdisk_size'])
    s_off = al(d_off + dtb_size) if h['second_size'] else None
    kernel = d[k_off:k_off + h['kernel_size']]
    ramdisk = d[r_off:r_off + h['ramdisk_size']]
    second = d[s_off:s_off + h['second_size']] if h['second_size'] else b''
    dtb = d[d_off:d_off + dtb_size] if dtb_size else b''
    body_end = al((s_off + h['second_size']) if s_off else (d_off + dtb_size))
    tail = d[body_end:] if body_end < len(d) else b''
    h['header_size'] = hdr_size
    h['dtb_size'] = dtb_size
    h['dtb_addr'] = dtb_addr
    h['header_raw'] = d[:hdr_size]
    return h, kernel, ramdisk, second, dtb, tail

def build(h, kernel, ramdisk, second, dtb, tail, cmdline=None, bootid=True, pad_to=0):
    ps = h['page_size'] or PAGE
    hdr = bytearray(h['header_raw'])
    if len(hdr) < 1660:
        hdr.extend(b'\x00' * (1660 - len(hdr)))
    struct.pack_into('<I', hdr, 0x08, len(kernel))
    struct.pack_into('<I', hdr, 0x10, len(ramdisk))
    struct.pack_into('<I', hdr, 0x18, len(second))
    if len(hdr) >= 0x680:
        struct.pack_into('<I', hdr, 0x670, len(dtb))
    if cmdline is not None:
        cb = cmdline.encode()[:511]
        hdr[0x40:0x240] = cb + b'\x00' * (512 - len(cb))
    out = bytearray()
    out += bytes(hdr)
    out += b'\x00' * (al(len(out)) - len(out))
    for blob in (kernel, ramdisk, second, dtb):
        if not blob:
            continue
        out += blob
        out += b'\x00' * (al(len(out)) - len(out))
    # boot_id = SHA1(kernel+ramdisk+second+dtb)
    if bootid:
        import hashlib
        sha = hashlib.sha1(kernel + ramdisk + second + dtb).digest()
        struct.pack_into('<32s', out, 0x240, sha + b'\x00' * 12)
    if tail:
        out += tail
    if pad_to and len(out) < pad_to:
        out += b'\x00' * (pad_to - len(out))
    return bytes(out)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    if len(args) < 3:
        print(__doc__)
        raise SystemExit(1)
    stock, neo_k, out_path = args[0], args[1], args[2]

    h, kernel, ramdisk, second, dtb, tail = parse(stock)
    print(f"[stock] kernel={len(kernel)} ramdisk={len(ramdisk)} dtb={len(dtb)} second={len(second)} tail={len(tail)}")
    print(f"[stock] addr k=0x{h['kernel_addr']:08x} r=0x{h['ramdisk_addr']:08x} tags=0x{h['tags_addr']:08x} page={h['page_size']}")

    new_k = open(neo_k, 'rb').read()
    if new_k[:2] != b'\x1f\x8b':
        raise SystemExit(f"{neo_k} 不是 gzip（前 2 字节 {new_k[:2]!r}）")
    print(f"[neo]   kernel={len(new_k)} (gzip)")

    base_cmd = h['header_raw'][0x40:0x240].split(b'\x00')[0].decode(errors='replace')
    cmd = base_cmd
    add = []
    if '--permissive' in flags:
        add.append('androidboot.selinux=permissive enforcing=0')
    if '--no-ksu-cmd' not in flags:
        add.append('kernelsu.force_feature=kernel_umount:1')
    for a in add:
        if a.split('=')[0] not in cmd:
            cmd = (cmd + ' ' + a).strip()
    if len(cmd) > 511:
        raise SystemExit("cmdline 超 511 字节，请裁剪")
    print(f"[cmdline] {base_cmd!r}")
    print(f"       -> {cmd!r}")

    keep_tail = tail if '--keep-tail' in flags else b''
    if keep_tail:
        print(f"[tail]  保留 {len(keep_tail)} 字节尾部")
    pad = 0 if '--no-pad' in flags else 33554432
    img = build(h, new_k, ramdisk, second, dtb, keep_tail, cmdline=cmd, pad_to=pad)
    _dir = os.path.dirname(os.path.abspath(out_path))
    if _dir:
        os.makedirs(_dir, exist_ok=True)
    open(out_path, 'wb').write(img)
    print(f"[out]   {out_path}  {len(img)} bytes ({len(img)/1048576:.2f} MiB)")
    if len(img) > 33554432:
        print("!! 警告：超过 boot 分区 32MiB，会刷不进去")
    # 自校验
    h2, k2, r2, s2, d2, t2 = parse(out_path)
    print(f"[verify] kernel={'OK' if k2 == new_k else 'FAIL'} ramdisk={'OK' if r2 == ramdisk else 'FAIL'} dtb={'OK' if d2 == dtb else 'FAIL'}")

if __name__ == '__main__':
    main()
