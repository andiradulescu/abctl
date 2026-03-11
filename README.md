# abctl

Minimal A/B boot control utility for Qualcomm devices. Open-source replacement for the proprietary Qualcomm `abctl` tool.

Manages A/B partitioned boot slots on Qualcomm UFS-based systems — slot selection, success marking, GUID swapping, and UFS boot LUN switching.

## Requirements

- Python 3 (standard library only, no external dependencies)
- Root privileges (reads/writes block devices directly)
- Qualcomm UFS-based device with A/B partition layout

## Installation

Copy the script to somewhere in your PATH:

```bash
cp abctl /usr/local/bin/
chmod +x /usr/local/bin/abctl
```

## Usage

```
abctl <command>
```

| Command | Description |
|---|---|
| `--boot_slot` | Print current boot slot (`_a` or `_b`) |
| `--set_success` | Mark current slot as successfully booted |
| `--set_active <0\|1>` | Set active slot (0 = slot A, 1 = slot B) |
| `--set_unbootable <0\|1>` | Mark a slot as unbootable |

### Examples

```bash
abctl --boot_slot           # prints _a or _b
abctl --set_success         # mark current slot successful
abctl --set_active 1        # switch to slot B
abctl --set_unbootable 0    # mark slot A unbootable
```

## How it works

The tool manipulates GPT (GUID Partition Table) attribute bits on the boot block devices to control A/B slot behavior, matching Qualcomm ABL's (Android Boot Loader) partitioning conventions.

**GPT attribute bits 48–55:**
| Bits | Field |
|---|---|
| 48–49 | Priority (0–3) |
| 50 | Active |
| 51–53 | Retry count (0–7) |
| 54 | Successful |
| 55 | Unbootable |

When switching active slots, `abctl`:
1. Swaps partition type GUIDs between `_a` and `_b` suffixed partitions
2. Updates GPT attribute flags (priority, active, retry count)
3. Sets the UFS boot LUN to point to the correct XBL (eXtensible Boot Loader) partition
4. Updates both primary and backup GPT tables with recomputed CRCs

**UFS boot LUN switching** supports three kernel interfaces (tried in order):
1. Qualcomm `UFS_IOCTL_QUERY` on SG device (kernel 4.9 + Qualcomm patches)
2. sysfs `boot_lun_en` attribute (mainline kernel 5.x+)
3. UFS BSG ioctl (mainline kernel with `CONFIG_SCSI_UFS_BSG`)

**Block devices:**
- `/dev/sda` — LUN 0 (system partitions)
- `/dev/sde` — LUN 4 (boot, aop, tz, abl, etc.)
- `/dev/sdb` — XBL boot device (UFS ioctl target)
