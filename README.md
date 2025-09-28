# Bluesky Auto-Like Bot (Multi-Account)

Bot Bluesky untuk auto-like **post terbaru (non-reply)** dari followers suatu akun sumber.
- **Multi-account** pakai **satu `.env`** (prefix `BOT1_*` ... `BOT5_*`)
- **State & log terisolasi** per akun
- **Mode sinkron**: semua akun like post yang sama (`SHARD_TOTAL=1`)

## Quick start
1. Copy `.env.example` → `.env`, isi kredensial (App Password Bluesky) & handle.
2. Simpan `autolike.py` di folder project.
3. (Opsional) pasang systemd template di `systemd/` lalu enable:
   ```bash
   sudo cp systemd/bsky-autolike@.service /etc/systemd/system/
   sudo cp systemd/bsky-autolike@.timer   /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now bsky-autolike@BOT1.timer
4. Jalankan manual untuk test:
    ```bash
    BOT_ID=BOT1 python3 autolike.py

## Konfigurasi kunci

`FOLLOWERS_SOURCE_HANDLE` — akun sumber daftar followers

`SHARD_TOTAL=1` — semua akun like post yang sama

`POSTS_PER_USER` — seberapa banyak post terbaru yang dicek per user

# Changelog:

## v1.3.0
- Single `.env` untuk multi-bot (prefix `BOT1_*` dst.)
- State & log terpisah per bot (suffix `-BOT_ID`)
- Mode sinkron: semua bot bisa like post yang sama (`SHARD_TOTAL=1`)
- Kompatibel berbagai versi `atproto` (fallback `service/base_url`)
