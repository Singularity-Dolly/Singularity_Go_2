# Go2 hardware gate status for Singularity_Go_2 / go2ctl

## PASS (prior real-hardware LocalAP + AES no-motion evidence)

Verified on the actual Unitree Go2 (independent of this go2ctl preflight run):

- network
- LocalAP signalling (`192.168.12.1:9991`)
- AES authentication
- WebRTC peer connection (ICE gather / ICE connect / peer connected)
- data channel
- clean disconnect
- no-motion connection (no movement command sent)

## go2ctl software

- `--connection-mode ap|sta` and `--aes-key-file` on `preflight` / `start` / `console`
- AES priority: CLI file → `UNITREE_AES_128_KEY` → `~/.config/go2ctl/aes_key`
- Exact LocalAP / LocalSTA constructors via `unitree_webrtc_connect`
- unit tests: **61 passed**

## Latest go2ctl preflight attempt (this workspace)

```bash
go2ctl preflight \
  --connection-mode ap \
  --aes-key-file ~/.config/go2ctl/aes_key
```

Result: **FAILED** `LOCAL_AP_SIGNALING_FAILED` — robot not exposing signalling ports
`9991/8081` from this host (likely not joined to Go2 LocalAP at attempt time).
`nonzero_velocity_sent` remained `false`; disconnect completed.

## NOT YET VERIFIED (through go2ctl)

- advancing camera frames through go2ctl
- zero-velocity command through go2ctl
- low-speed manual motion
- detection-only
- tracker-only
- physical follow
- physical E-stop

## Exact no-motion preflight command

```bash
cd ~/Singularity_Go_2
source .venv/bin/activate
go2ctl preflight \
  --connection-mode ap \
  --aes-key-file ~/.config/go2ctl/aes_key
```

Do not run manual movement or follow until the remaining physical gates pass.
