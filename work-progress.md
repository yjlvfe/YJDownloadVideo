# Work Progress
- Portal extraction used direct IP while HLS download used VPN; signed URL ASN changed from 35916 to 7738 after aligning portal HTTP with VPN.
- Added persistent httpx SOCKS support. First probe was incomplete due skipped fragments; not accepted as success.
- Strict portal probe at 8 fragments running: proc_2e2424550620; log /root/.hermes/cache/scratch/yj-complete.log.
- Fixed invalid faststart temporary extension; media/seek verification pending.
- Building all app images with SOCKS support: proc_363d0c7c589f. Deployment pending ongoing download. No Telegram test sends.
