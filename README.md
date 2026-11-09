# Telegram Tag-All Bot

Bot Telegram untuk men-tag (mention) semua anggota grup dengan satu perintah.

## Perintah

| Perintah | Fungsi |
|---|---|
| `/all [pesan]` (alias `/tagall`, `/everyone`) | Tag semua anggota, pesan opsional ditaruh di atas |
| `@all [pesan]` (juga `@everyone`, `@semua`) | Sama seperti `/all`, cukup ketik di chat |
| `/cancel` (alias `/stop`) | Hentikan tagging yang sedang berjalan |
| `/members` | Lihat berapa anggota yang sudah tercatat |
| `/help` | Bantuan |

Mention dikirim per 5 orang tiap pesan dengan jeda 1,5 detik (bisa diatur di `.env`) supaya tidak kena flood limit Telegram. Anggota tanpa username tetap bisa di-tag (pakai link `tg://user?id=`).

## Keterbatasan penting

Bot API Telegram **tidak bisa mengambil daftar seluruh anggota grup**. Bot ini mencatat anggota dari:

- setiap orang yang mengirim pesan di grup,
- orang yang baru join,
- update keanggotaan (bot harus admin),
- daftar admin grup.

Jadi anggota yang diam sejak bot masuk belum akan ter-tag. Cek cakupannya dengan `/members`.

## Setup

1. Buat bot di [@BotFather](https://t.me/BotFather) → `/newbot`, simpan tokennya.
2. **Matikan privacy mode**: di BotFather `/setprivacy` → pilih bot → `Disable`. Tanpa ini bot tidak melihat semua pesan grup dan tidak bisa mencatat anggota.
3. Install dan jalankan:

   ```bash
   cd telegram-tagall-bot
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   ```

   Isi `BOT_TOKEN` di `.env`, lalu:

   ```bash
   python bot.py
   ```

4. Tambahkan bot ke grup dan **jadikan admin** (agar menerima update join/leave).

> Jika privacy mode baru dimatikan setelah bot sudah di grup, keluarkan lalu masukkan lagi bot ke grup agar setting berlaku.

## Deploy ke VPS

> Bot memakai long polling: **hanya satu instance** boleh jalan per token. Matikan bot di laptop sebelum menyalakan di VPS. Tidak perlu domain, HTTPS, atau port terbuka.

Ambil kode di VPS (repo private: login dulu dengan `gh auth login` atau pakai deploy key / personal access token):

```bash
git clone https://github.com/UsepSumaryana/telegram-tagall-bot.git ~/tagall-bot
```

Update ke versi terbaru nanti cukup `cd ~/tagall-bot && git pull`, lalu jalankan ulang perintah build/install di bawah.

### Opsi A — Docker (disarankan)

Di VPS:

```bash
cd ~/tagall-bot
cp .env.example .env && nano .env      # isi BOT_TOKEN
docker compose up -d --build
docker compose logs -f
```

Data anggota tersimpan di volume `bot-data`, aman saat container di-rebuild. Update: salin `bot.py` baru lalu `docker compose up -d --build`.

### Opsi B — systemd (tanpa Docker, Ubuntu/Debian)

```bash
cd ~/tagall-bot
cp .env.example .env && nano .env      # isi BOT_TOKEN
sudo bash deploy/install.sh
```

Script ini memasang bot ke `/opt/tagall-bot`, membuat user `tagallbot`, venv, dan service `tagall-bot` yang otomatis jalan saat boot dan restart jika crash.

```bash
sudo journalctl -u tagall-bot -f       # lihat log
sudo systemctl restart tagall-bot      # restart
```

Update: salin `bot.py` baru ke `~/tagall-bot` lalu jalankan lagi `sudo bash deploy/install.sh` (`.env` dan database tidak ditimpa).

## Konfigurasi `.env`

| Variabel | Default | Keterangan |
|---|---|---|
| `BOT_TOKEN` | – | Token dari BotFather (wajib) |
| `DB_PATH` | `members.db` | File SQLite penyimpan anggota |
| `TAG_BATCH_SIZE` | `5` | Jumlah mention per pesan |
| `TAG_DELAY` | `1.5` | Jeda antar pesan (detik) |
| `ADMIN_ONLY` | `true` | Hanya admin grup yang boleh `/all` |
