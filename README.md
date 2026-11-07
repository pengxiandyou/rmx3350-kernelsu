# RMX3350 KernelSU 内核构建

真我 GT Neo 闪速版（RMX3350 / RE5469 / MT6893 天玑1200）的 KernelSU 内嵌内核，
通过 GitHub Actions 云端编译，产物是可直接 `fastboot flash boot` 的 32MB boot 镜像。

## 为什么要自己编

这台机器上 KernelSU 只有一条路可走：

| 模式 | 结论 |
|---|---|
| 官方 KernelSU GKI 镜像 | 不可用 —— 内核 4.19.191 不属于 GKI 分支 |
| LKM（`insmod ksu.ko`） | 不可用 —— `CONFIG_MODULE_SIG_FORCE=y`，签不出 OPPO 原厂私钥 |
| **编进内核（built-in）** | **唯一可行** |

## 为什么之前的 NeoCore 内核刷不进去

NeoCore（`gimgiwer/kernel-realme-gt-neo-2t`）是给 RMX3357 编的，直接刷到 RMX3350 上会无限重启。
逐层排查后确认两个硬性障碍：

1. **vermagic 差一个 `+`**
   - 原厂：`4.19.191+ SMP preempt mod_unload modversions aarch64`
   - NeoCore：`4.19.191 SMP preempt mod_unload modversions aarch64`
   - 内核 `check_modinfo()` 逐字节比对，不匹配即拒绝加载 → vendor 全部 `.ko` 加载失败 → 黑屏

2. **`CONFIG_MODVERSIONS` 的符号 CRC 对不上**
   NeoCore 在原厂基线上叠加了 `CONFIG_HZ=250→360`、`CONFIG_KALLSYMS_ALL=y`、`CONFIG_MODULE_SIG_FORCE` 关闭等改动，
   `genksyms` 重算出的 `__crc_*` 与原厂不同 → vendor `.ko` 的符号版本校验失败。
   即使把 vermagic 手动补上 `+`（已验证过，`ota_probe/f28_ksu_v2.img`），仍然无限重启。

**结论**：必须用官方源码 + 原厂 config 一字不动地编，只加 `CONFIG_KSU`。

## 本仓库做了什么

```
kernel  ←  realme-kernel-opensource/realme_X7max_GTneo_GTneo2T-AndroidT-kernel-source
        （Makefile 里 VERSION=4 PATCHLEVEL=19 SUBLEVEL=191，即设备上的 4.19.191+）
  ├── drivers/kernelsu/        ← KernelSU-Next v3.4.2（git clone 进来）
  ├── drivers/Kconfig          ← 注入 source "drivers/kernelsu/Kconfig"
  └── drivers/Makefile         ← 注入 obj-$(CONFIG_KSU) += kernelsu/
```

config 用 `merge_config.sh` 合并两份：

- `configs/stock_f28.config`（6138 行）—— 从设备 `/proc/config.gz` 原样导出，F.28 固件的真实配置
  - `CONFIG_HZ=250`、`CONFIG_MODVERSIONS=y`、`# CONFIG_KALLSYMS_ALL is not set`、`CONFIG_MODULE_SIG_FORCE=y` 全部保持原样
- `configs/custom_rmx3350.config` —— 只加两项：
  ```
  CONFIG_KSU=y
  CONFIG_KSU_MANUAL_HOOK=y
  CONFIG_MODULE_SIG=y
  # CONFIG_MODULE_SIG_FORCE is not set
  ```

`drivers/Kconfig` 和 `drivers/Makefile` 被改动但不提交，源码树保持 dirty，
`scripts/setlocalversion` 会自动追加 `+`，vermagic 自然变成 `4.19.191+`。
workflow 里另有一道兜底：若编出来的 Image.gz 里 vermagic 没有 `+`，就地打补丁并复验。

## 产物

| 文件 | 说明 |
|---|---|
| `boot-rmx3350-f28-ksu.img` | 可直接刷的 boot（F.28 原厂 ramdisk/dtb + 自编译 KSU 内核） |
| `Image.gz` | 裸内核，需要自己重打包时用 |
| `boot-rmx3350-f28-stock-rollback.img` | 回滚用原厂 boot，MD5 `ef89ab09c8719edc1e903fc4f20d93ce` |

## 刷入

```bash
adb reboot bootloader
fastboot devices                        # 应显示 59AUOB4PPVORVWVO
fastboot flash boot boot-rmx3350-f28-ksu.img
fastboot reboot
```

首次开机后装 **KernelSU Next** Manager（`KernelSU_Next_v3.4.2.apk`），应显示「已安装 / 工作中」。

## 回滚

刷坏了不会变砖（bootloader 已解锁，`seccfg` 分区不参与任何刷写，不回锁）：

```bash
# 设备自己会进 fastboot，或长按电源 10-15 秒强制重启后音量下+电源
fastboot flash boot boot-rmx3350-f28-stock-rollback.img
fastboot reboot
```

如果连 fastboot 都不进，realme 的 lk 会在连续启动失败后自动进入修复模式，
联网下载官方固件重刷（上次实测有效，从 F.12 自动恢复到 F.28）。

## 前提条件（跑 workflow 之前）

- GitHub 账号 + 一个有 `repo` 和 `workflow` 权限的 PAT
- 仓库设为 public 或保证 Actions 有额度（public 仓库免费无限）

## 已知风险

- F.28 是 2024-08-20 编译的，`scripts/setlocalversion` 的 `+` 行为依赖源码树 dirty 状态，
  workflow 已加兜底逻辑，但如果 CI 上 git 行为异常，请检查 "Verify and enforce vermagic" 那一步的日志
- `CONFIG_MODULE_SIG_ALL=y` 保留着，内核会给编出来的模块用新生成的 key 签名，但因为
  `MODULE_SIG_FORCE` 已关，加载时不强制验证，不影响 vendor 模块
- 出问题优先看 `fastboot getvar all` 和串口日志，本机无串口时只能靠回滚镜像
