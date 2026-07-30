# Runbook — grow the EC2 root volume

## Why

The root volume is 10 GiB. Every deploy runs a two-stage `docker build` (Node
frontend, then the Python app), and each run leaves image layers plus build-cache
behind. On 2026-07-29 the host reached 4.9 MB free and the deploy of `abd5ffb`
failed at `exporting to image` with `no space left on device`. Tests and lint were
green — an infrastructure failure wearing a code failure's clothes.

`2eb6f63` made `deploy.sh` prune before building, which mitigates it. This runbook
removes the class of failure instead of managing it.

## Verified facts for THIS host (checked 2026-07-30)

| | |
| --- | --- |
| instance | `i-0f62805f9ef0d89bb` |
| region / AZ | `ap-south-1` / `ap-south-1a` |
| volume | 10 GiB, attached as `/dev/xvda` (Xen device, not NVMe) |
| root partition | `/dev/xvda1`, 8.9 GiB, **physically last** (starts at sector 2324480) |
| filesystem | `ext4` -> use `resize2fs` (NOT `xfs_growfs`) |
| `growpart` | already installed |
| target size | 30 GiB |

The partition being last is what makes this safe: new space lands immediately after
it, so `growpart` can extend it in place. Do not run these commands on a host you
have not checked — if the root partition is not last, or the filesystem is XFS, the
steps differ.

## No downtime

EBS resize is online. No detach, no stop, no container restart. The service keeps
answering throughout.

## Steps

### 1. Snapshot first (cheap insurance)

Console: EC2 -> Volumes -> select the volume -> Actions -> Create snapshot.
Or:

```bash
aws ec2 create-snapshot --region ap-south-1 --volume-id <VOL_ID> \
  --description "pre-resize $(date -u +%FT%TZ)"
```

Find `<VOL_ID>` under EC2 -> Instances -> `i-0f62805f9ef0d89bb` -> Storage, or:

```bash
aws ec2 describe-instances --region ap-south-1 \
  --instance-ids i-0f62805f9ef0d89bb \
  --query 'Reservations[].Instances[].BlockDeviceMappings[].Ebs.VolumeId' --output text
```

### 2. Grow the volume (AWS side)

Console: Volumes -> Actions -> **Modify volume** -> Size `30` -> Modify.
Or:

```bash
aws ec2 modify-volume --region ap-south-1 --volume-id <VOL_ID> --size 30
```

Watch it reach `optimizing` or `completed` — the filesystem can be grown as soon as
it leaves `modifying`:

```bash
aws ec2 describe-volumes-modifications --region ap-south-1 \
  --volume-id <VOL_ID> --query 'VolumesModifications[].[ModificationState,Progress]' \
  --output text
```

### 3. Grow the partition and filesystem (host side)

```bash
ssh -i C:/workspace/aws-key/sai.pem ubuntu@3.7.240.120

lsblk                      # xvda should now show 30G, xvda1 still 8.9G
sudo growpart /dev/xvda 1  # extend the partition into the new space
sudo resize2fs /dev/xvda1  # extend the ext4 filesystem to fill it
df -h /                    # expect ~29G total
```

### 4. Confirm

```bash
df -h /
docker system df
curl -s https://3.7.240.120.nip.io/health     # {"status":"ok"}
```

## Gotchas

- **One resize per 6 hours.** EBS refuses a second modification on the same volume
  inside that window. Pick the target size once; going 10 -> 20 -> 30 means waiting.
- **Size only goes up.** Shrinking an EBS volume is not possible; you would have to
  create a smaller one and migrate.
- **`growpart` before `resize2fs`.** The filesystem cannot exceed its partition, so
  running `resize2fs` alone changes nothing and looks like a no-op.
- **`/dev/root` is an alias.** Pass the real device, `/dev/xvda1`, to `resize2fs`.
- **Cost.** Roughly 20 GiB more of gp2/gp3 in `ap-south-1` — a couple of dollars a
  month at current rates. Check the pricing page for the exact figure rather than
  trusting this line.

## Optional, while you are in there

The volume type may still be `gp2`. `gp3` is generally cheaper per GiB and lets you
set IOPS and throughput independently. The same **Modify volume** dialog changes
type, and it is also online:

```bash
aws ec2 modify-volume --region ap-south-1 --volume-id <VOL_ID> --size 30 --volume-type gp3
```

Do it in the same modification to avoid burning the 6-hour cooldown twice.

## After this

`deploy.sh` still prunes before building and logs free space either side. Keep that —
with 30 GiB the prune stops being load-bearing, but the logging is what makes the
next disk problem diagnosable from the CI output alone.
