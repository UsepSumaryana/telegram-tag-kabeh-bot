"""
Telegram Tag-All Bot
--------------------
Men-tag (mention) semua anggota grup yang diketahui bot.

Catatan penting: Bot API Telegram TIDAK menyediakan endpoint untuk mengambil
seluruh daftar anggota grup. Karena itu bot ini "mencatat" anggota dari:
  - setiap pesan yang dikirim di grup,
  - anggota yang baru masuk (join),
  - update chat_member (butuh bot jadi admin),
  - daftar admin grup (diambil setiap kali /all dipakai).
Anggota yang belum pernah chat sejak bot masuk belum tercatat.
"""

import asyncio
import html
import logging
import os
import re
import sqlite3
from contextlib import closing

from dotenv import load_dotenv
from telegram import (
    ChatMember,
    LinkPreviewOptions,
    Message,
    ReplyParameters,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "members.db")
BATCH_SIZE = int(os.getenv("TAG_BATCH_SIZE", "5"))
BATCH_DELAY = float(os.getenv("TAG_DELAY", "1.5"))
ADMIN_ONLY = os.getenv("ADMIN_ONLY", "true").lower() in ("1", "true", "yes")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("tagall")

ALL_TRIGGER = re.compile(r"(?i)(?:^|\s)@(all|everyone|semua)\b")


# --------------------------------------------------------------------------- #
# Penyimpanan anggota (SQLite)
# --------------------------------------------------------------------------- #
class MemberStore:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        with self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS members (
                       chat_id  INTEGER NOT NULL,
                       user_id  INTEGER NOT NULL,
                       name     TEXT    NOT NULL,
                       username TEXT,
                       PRIMARY KEY (chat_id, user_id)
                   )"""
            )

    def upsert(self, chat_id: int, user: User) -> None:
        if user.is_bot:
            return
        with self.conn:
            self.conn.execute(
                """INSERT INTO members (chat_id, user_id, name, username)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, user_id)
                   DO UPDATE SET name = excluded.name, username = excluded.username""",
                (chat_id, user.id, user.full_name or str(user.id), user.username),
            )

    def remove(self, chat_id: int, user_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM members WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )

    def migrate_chat(self, old_id: int, new_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE OR IGNORE members SET chat_id = ? WHERE chat_id = ?",
                (new_id, old_id),
            )
            self.conn.execute("DELETE FROM members WHERE chat_id = ?", (old_id,))

    def list(self, chat_id: int) -> list[tuple[int, str]]:
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                "SELECT user_id, name FROM members WHERE chat_id = ? ORDER BY name",
                (chat_id,),
            )
            return cur.fetchall()

    def count(self, chat_id: int) -> int:
        with closing(self.conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM members WHERE chat_id = ?", (chat_id,))
            return cur.fetchone()[0]


store = MemberStore(DB_PATH)


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


async def is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except TelegramError:
        return False
    return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)


async def sync_admins(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except TelegramError as exc:
        log.warning("Gagal ambil admin chat %s: %s", chat_id, exc)
        return
    for adm in admins:
        store.upsert(chat_id, adm.user)


async def send_with_retry(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                          reply_to: int | None = None) -> None:
    for _ in range(3):
        try:
            await context.bot.send_message(
                chat_id,
                text,
                parse_mode=ParseMode.HTML,
                reply_parameters=ReplyParameters(reply_to, allow_sending_without_reply=True)
                if reply_to else None,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return
        except RetryAfter as exc:
            wait = exc.retry_after if isinstance(exc.retry_after, (int, float)) \
                else exc.retry_after.total_seconds()
            log.info("Kena flood limit, tunggu %ss", wait)
            await asyncio.sleep(wait + 1)


# --------------------------------------------------------------------------- #
# Pencatatan anggota
# --------------------------------------------------------------------------- #
async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type not in ("group", "supergroup"):
        return

    if msg.migrate_to_chat_id:
        store.migrate_chat(chat.id, msg.migrate_to_chat_id)
        return

    if update.effective_user:
        store.upsert(chat.id, update.effective_user)
    for user in msg.new_chat_members or []:
        store.upsert(chat.id, user)
    if msg.left_chat_member:
        store.remove(chat.id, msg.left_chat_member.id)


async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.chat_member
    if not cmu:
        return
    user = cmu.new_chat_member.user
    status = cmu.new_chat_member.status
    if status in (ChatMember.LEFT, ChatMember.BANNED):
        store.remove(cmu.chat.id, user.id)
    elif status == ChatMember.RESTRICTED and not getattr(cmu.new_chat_member, "is_member", True):
        store.remove(cmu.chat.id, user.id)
    else:
        store.upsert(cmu.chat.id, user)


# --------------------------------------------------------------------------- #
# Tag all
# --------------------------------------------------------------------------- #
async def run_tagging(context: ContextTypes.DEFAULT_TYPE, chat_id: int, caller_id: int,
                      header: str, reply_to: int) -> None:
    chat_data = context.application.chat_data[chat_id]
    try:
        await sync_admins(context, chat_id)
        members = [(uid, name) for uid, name in store.list(chat_id) if uid != caller_id]
        if not members:
            await send_with_retry(context, chat_id,
                                  "Belum ada anggota yang tercatat. Minta anggota mengirim pesan dulu.",
                                  reply_to)
            return

        first = True
        for i in range(0, len(members), BATCH_SIZE):
            if chat_data.get("cancel"):
                await send_with_retry(context, chat_id, "⛔ Tagging dibatalkan.")
                return
            batch = members[i:i + BATCH_SIZE]
            text = " ".join(mention(uid, name) for uid, name in batch)
            if first and header:
                text = f"{html.escape(header)}\n\n{text}"
            await send_with_retry(context, chat_id, text, reply_to if first else None)
            first = False
            await asyncio.sleep(BATCH_DELAY)
    finally:
        chat_data["running"] = False
        chat_data["cancel"] = False


async def start_tagging(update: Update, context: ContextTypes.DEFAULT_TYPE, header: str) -> None:
    chat = update.effective_chat
    user = update.effective_user
    msg: Message = update.effective_message
    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("Perintah ini hanya bisa dipakai di grup.")
        return
    if ADMIN_ONLY and not await is_admin(context, chat.id, user.id):
        await msg.reply_text("Hanya admin grup yang boleh tag semua anggota.")
        return
    if context.chat_data.get("running"):
        await msg.reply_text("Tagging sedang berjalan. Pakai /cancel untuk menghentikan.")
        return

    context.chat_data["running"] = True
    context.chat_data["cancel"] = False
    # Jalankan di background agar /cancel tetap bisa diproses.
    context.application.create_task(
        run_tagging(context, chat.id, user.id, header, msg.message_id),
        update=update,
    )


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_tagging(update, context, " ".join(context.args))


async def trigger_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or update.effective_message.caption or ""
    header = ALL_TRIGGER.sub(" ", text).strip()
    await start_tagging(update, context, header)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if ADMIN_ONLY and not await is_admin(context, chat.id, update.effective_user.id):
        return
    if context.chat_data.get("running"):
        context.chat_data["cancel"] = True
        await update.effective_message.reply_text("Menghentikan tagging...")
    else:
        await update.effective_message.reply_text("Tidak ada tagging yang sedang berjalan.")


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    await sync_admins(context, chat.id)
    tracked = store.count(chat.id)
    try:
        total = await context.bot.get_chat_member_count(chat.id)
    except TelegramError:
        total = "?"
    await update.effective_message.reply_text(
        f"Anggota tercatat: {tracked} (total grup termasuk bot: {total})."
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👋 Bot Tag-All\n\n"
        "/all [pesan] — tag semua anggota grup\n"
        "@all [pesan] — sama seperti /all\n"
        "/cancel — hentikan tagging yang sedang berjalan\n"
        "/members — jumlah anggota yang sudah tercatat\n\n"
        "Bot hanya bisa men-tag anggota yang sudah tercatat (pernah chat / join "
        "setelah bot masuk, atau admin)."
    )


# --------------------------------------------------------------------------- #
def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN belum di-set. Salin .env.example ke .env lalu isi token.")

    app = Application.builder().token(BOT_TOKEN).build()

    # group=-1: pencatatan jalan dulu untuk setiap pesan, tanpa menghalangi handler lain.
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, track_message), group=-1)
    app.add_handler(ChatMemberHandler(track_chat_member, ChatMemberHandler.CHAT_MEMBER), group=-1)

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler(["all", "tagall", "everyone"], cmd_all))
    app.add_handler(CommandHandler(["cancel", "stop"], cmd_cancel))
    app.add_handler(CommandHandler("members", cmd_members))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.Regex(ALL_TRIGGER) | filters.CaptionRegex(ALL_TRIGGER)),
        trigger_all,
    ))

    log.info("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
