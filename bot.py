import asyncio
import html
import logging
import os

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ================== SOZLAMALAR ==================
BOT_TOKEN = "8603889269:AAHOOVgL5IOxRdsK34DRn5kXdNdTz6g0XWQ"
ADMIN_ID = 8327302562
OTZIV_CHAT_ID = "@vip_starsgift"  # Buyurtmalar boradigan kanal username yoki IDsi
DB_PATH = os.getenv("DB_PATH", "bot.db")

REF_REWARD = 3  # Taklif qilingan do'st kanallarga obuna bo'lsa beriladigan stars

CREATOR = "@N_57001"
CREATED_DATE = "20/10/2026"

# ================== TUGMALAR ==================
B_STARS = "⭐️ Stars olish"
B_WITHDRAW = "🎁 Gift yechish"
B_ORDERS = "📦 Buyurtmalar"
B_ACCOUNT = "👤 Mening hisobim"
B_REF = "🗣 Referal"
B_TOPUP = "💰 Hisob to'ldirish"
B_SUPPORT = "☎️ Murojaat"
B_GUIDE = "📚 Qo'llanma"
B_INFO = "ℹ️ Ma'lumot"
B_ADMIN = "🛠 Admin panel"

MENU_TEXTS = {B_STARS, B_WITHDRAW, B_ORDERS, B_ACCOUNT, B_REF, B_TOPUP, B_SUPPORT, B_GUIDE, B_INFO, B_ADMIN}

router = Router()


class States(StatesGroup):
    waiting_support = State()
    waiting_channel = State()
    waiting_ban = State()
    waiting_topup = State()
    waiting_minus = State()
    waiting_st_plus = State()
    waiting_st_minus = State()
    waiting_reply = State()


# ================== YORDAMCHI ==================
def user_label(full_name, username) -> str:
    name = html.escape(full_name or "Noma'lum")
    uname = f"@{html.escape(username)}" if username else "username yo'q"
    return f"<b>{name}</b> ({uname})"


async def send_chunks(message: Message, lines: list[str], per_chunk: int = 20, sep: str = "\n\n"):
    for i in range(0, len(lines), per_chunk):
        await message.answer(sep.join(lines[i:i + per_chunk]))


# ================== BAZA ==================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 0,
                stars INTEGER DEFAULT 0,
                referrer_id INTEGER,
                rewarded INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                stars INTEGER,
                price TEXT
            );
            CREATE TABLE IF NOT EXISTS withdraws(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                stars INTEGER
            );
            CREATE TABLE IF NOT EXISTS channels(
                username TEXT PRIMARY KEY,
                title TEXT
            );
            """
        )
        # Baza avval yaratilgan bo'lsa, 'stars' ustunini qo'shib qo'yish
        try:
            await db.execute("ALTER TABLE users ADD COLUMN stars INTEGER DEFAULT 0")
        except Exception:
            pass

        cur = await db.execute("SELECT COUNT(*) FROM channels")
        if (await cur.fetchone())[0] == 0:
            await db.executemany(
                "INSERT OR IGNORE INTO channels(username, title) VALUES(?, ?)",
                [("@Luckasuzb", "📢 Luckasuzb"), ("@vip_starsgift", "📢 VIP Stars Gift")],
            )
        await db.commit()


async def get_channels() -> list[tuple[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT username, title FROM channels")
        return await cur.fetchall()


async def add_user(user, ref: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE id=?", (user.id,))
        exists = await cur.fetchone()
        if not exists:
            ref_ok = None
            if ref and ref != user.id:
                c2 = await db.execute("SELECT 1 FROM users WHERE id=?", (ref,))
                if await c2.fetchone():
                    ref_ok = ref
            await db.execute(
                "INSERT INTO users(id, username, full_name, referrer_id) VALUES(?,?,?,?)",
                (user.id, user.username, user.full_name, ref_ok),
            )
        else:
            await db.execute(
                "UPDATE users SET username=?, full_name=? WHERE id=?",
                (user.username, user.full_name, user.id),
            )
        await db.commit()


async def get_user_data(uid: int) -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, stars FROM users WHERE id=?", (uid,))
        row = await cur.fetchone()
        return (row[0], row[1]) if row else (0, 0)


def main_kb(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=B_STARS), KeyboardButton(text=B_WITHDRAW)],
        [KeyboardButton(text=B_ORDERS), KeyboardButton(text=B_ACCOUNT)],
        [KeyboardButton(text=B_REF), KeyboardButton(text=B_TOPUP)],
        [KeyboardButton(text=B_SUPPORT), KeyboardButton(text=B_GUIDE)],
        [KeyboardButton(text=B_INFO)],
    ]
    if uid == ADMIN_ID:
        rows.append([KeyboardButton(text=B_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def sub_kb(channels: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, url=f"https://t.me/{username.lstrip('@')}")]
        for username, title in channels
    ]
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ================== MAJBURIY OBUNA ==================
def _is_member(m) -> bool:
    if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return False
    if m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", True) is False:
        return False
    return True


async def get_missing(bot: Bot, uid: int) -> list[tuple[str, str]]:
    missing = []
    for ch, title in await get_channels():
        try:
            m = await bot.get_chat_member(ch, uid)
            if not _is_member(m):
                missing.append((ch, title))
        except Exception as e:
            logging.warning("get_chat_member xato (%s): %s", ch, e)
    return missing


def sub_text(missing: list[tuple[str, str]]) -> str:
    ch_list_str = "\n".join(f"📢 {ch}" for ch, _ in missing)
    return (
        "❌ Kechirasiz, botimizdan to'liq foydalanish uchun quyidagi kanallarga obuna bo'lishingiz kerak!\n\n"
        f"{ch_list_str}\n\n"
        "👇 Obuna bo'lgach <b>✅ Tekshirish</b> tugmasini bosing."
    )


# ================== REFERAL MUKOFOTI ==================
async def try_reward_referrer(bot: Bot, uid: int):
    if await get_missing(bot, uid):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET rewarded=1 WHERE id=? AND rewarded=0 AND referrer_id IS NOT NULL",
            (uid,),
        )
        if cur.rowcount == 0:
            await db.commit()
            return
        cur = await db.execute("SELECT referrer_id, full_name FROM users WHERE id=?", (uid,))
        referrer_id, full_name = await cur.fetchone()
        await db.execute("UPDATE users SET stars = stars + ? WHERE id=?", (REF_REWARD, referrer_id))
        await db.commit()

    try:
        await bot.send_message(
            referrer_id,
            f"🎉 Siz taklif qilgan <b>{html.escape(full_name or 'foydalanuvchi')}</b> "
            f"kanallarga obuna bo'ldi!\n⭐️ Hisobingizga <b>{REF_REWARD} ta Stars</b> qo'shildi!",
        )
    except Exception:
        pass


# ================== MIDDLEWARE (BAN + OBUNA) ==================
class SubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        if not user:
            return await handler(event, data)

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT is_banned FROM users WHERE id=?", (user.id,))
            row = await cur.fetchone()
            if row and row[0] == 1:
                if isinstance(event, Message):
                    await event.answer("❌ Siz botdan bloklangansiz!")
                elif isinstance(event, CallbackQuery):
                    await event.answer("❌ Siz botdan bloklangansiz!", show_alert=True)
                return

        if isinstance(event, CallbackQuery):
            msg = event.message
            if msg is None or msg.chat.type != "private":
                return await handler(event, data)
            if event.data == "check" or user.id == ADMIN_ID:
                return await handler(event, data)
            missing = await get_missing(event.bot, user.id)
            if not missing:
                return await handler(event, data)
            await event.answer("❌ Avval kanallarga obuna bo'ling!", show_alert=True)
            return

        if event.chat.type != "private":
            return
        text = event.text or ""

        if text.startswith("/start"):
            return await handler(event, data)

        await add_user(user)
        if user.id == ADMIN_ID:
            return await handler(event, data)

        missing = await get_missing(event.bot, user.id)
        if not missing:
            return await handler(event, data)

        await event.answer(sub_text(missing), reply_markup=sub_kb(missing))


# ================== /start ==================
@router.message(CommandStart())
async def start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ref = int(command.args) if command.args and command.args.isdigit() else None
    await add_user(m.from_user, ref)
    missing = [] if m.from_user.id == ADMIN_ID else await get_missing(m.bot, m.from_user.id)
    if not missing:
        await m.answer("👋 Assalomu alaykum! Botimizga xush kelibsiz.", reply_markup=main_kb(m.from_user.id))
        await try_reward_referrer(m.bot, m.from_user.id)
    else:
        await m.answer(sub_text(missing), reply_markup=sub_kb(missing))


@router.callback_query(F.data == "check")
async def check(c: CallbackQuery):
    await add_user(c.from_user)
    missing = await get_missing(c.bot, c.from_user.id)
    if not missing:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer("💻 <b>Asosiy menyudasiz!</b>", reply_markup=main_kb(c.from_user.id))
        await c.answer()
        await try_reward_referrer(c.bot, c.from_user.id)
    else:
        await c.answer("❌ Siz hali hamma kanalga obuna bo'lmadingiz!", show_alert=True)


# ================== ASOSIY MENYU ==================
@router.message(F.text.in_(MENU_TEXTS))
async def menu(m: Message, state: FSMContext):
    await state.clear()
    t = m.text
    uid = m.from_user.id

    if t == B_STARS:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⭐️ 50 ta Stars — 11,000 so'm", callback_data="buy_50")],
                [InlineKeyboardButton(text="⭐️ 100 ta Stars — 21,000 so'm", callback_data="buy_100")],
            ]
        )
        await m.answer(
            "⭐️ <b>Telegram Stars sotib olish</b> ⭐️\n\n"
            "Kerakli miqdorni tanlang:\n\n"
            "• 50 ta Stars = 11,000 so'm\n"
            "• 100 ta Stars = 21,000 so'm",
            reply_markup=kb,
        )

    elif t == B_WITHDRAW:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎁 15 ta Stars yechish", callback_data="withdraw_15")],
                [InlineKeyboardButton(text="🎁 25 ta Stars yechish", callback_data="withdraw_25")],
            ]
        )
        await m.answer(
            "🎁 <b>Gift yechish bo'limi</b> 🎁\n\n"
            "Hisobingizdagi starstan gift sifatida yechib olishingiz mumkin:\n\n"
            "• 15 ta Stars\n"
            "• 25 ta Stars",
            reply_markup=kb,
        )

    elif t == B_REF:
        me = await m.bot.get_me()
        link = f"https://t.me/{me.username}?start={uid}"
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT id, full_name, username, rewarded FROM users WHERE referrer_id=? ORDER BY rowid", (uid,)
            )
            invited_rows = await cur.fetchall()
        active = sum(1 for _, _, _, r in invited_rows if r == 1)
        await m.answer(
            "🗣 <b>Referal tizimi</b> 🗣\n\n"
            f"🔗 Sizning havolangiz:\n<code>{link}</code>\n\n"
            f"👥 Taklif qilgan do'stlaringiz: <b>{len(invited_rows)}</b> ta\n"
            f"✅ Obuna bo'lib, mukofot olib kelganlar: <b>{active}</b> ta\n\n"
            f"🎁 Har bir do'stingiz <b>barcha kanallarga obuna bo'lsa</b>, "
            f"sizga <b>{REF_REWARD} ta Stars</b> beriladi!"
        )
        if invited_rows:
            lines = [
                f"{i}. 👤 {user_label(name, uname)} | 🆔 <code>{r_id}</code>\n    Status: {'✅ obuna bo`lgan' if r == 1 else '⏳ obuna bo`lmagan'}"
                for i, (r_id, name, uname, r) in enumerate(invited_rows, 1)
            ]
            await m.answer("👥 <b>Siz taklif qilganlar ro'yxati:</b>")
            await send_chunks(m, lines, per_chunk=20, sep="\n\n")

    elif t == B_ORDERS:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT stars, price FROM orders WHERE user_id=? ORDER BY id DESC", (uid,))
            rows = await cur.fetchall()
            cur2 = await db.execute("SELECT stars FROM withdraws WHERE user_id=? ORDER BY id DESC", (uid,))
            w_rows = await cur2.fetchall()

        text_parts = []
        if rows:
            lines = [f"✅ ⭐️ {s} ta stars — 💵 {p}" for s, p in rows]
            text_parts.append("📦 <b>Stars xaridlaringiz:</b>\n" + "\n".join(lines))
        if w_rows:
            w_lines = [f"🎁 ⭐️ {s} ta stars gift yechish" for (s,) in w_rows]
            text_parts.append("📦 <b>Gift yechish buyurtmalaringiz:</b>\n" + "\n".join(w_lines))

        if not text_parts:
            await m.answer("📭 <b>Sizda hali buyurtmalar yo'q</b>")
        else:
            await m.answer("\n\n".join(text_parts))

    elif t == B_ACCOUNT:
        bal, stars = await get_user_data(uid)
        await m.answer(
            f"👤 <b>Mening hisobim</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💰 Balans: <b>{bal}</b> so'm\n"
            f"⭐️ Stars: <b>{stars}</b> ta"
        )

    elif t == B_TOPUP:
        await m.answer("💰 <b>Hisob to'ldirish</b>\n\nHisobni to'ldirish uchun <b>☎️ Murojaat</b> tugmasi orqali adminga yozing.")

    elif t == B_GUIDE:
        await m.answer("📚 <b>Qo'llanma:</b>\n\nStars sotib olish yoki gift yechish uchun tegishli bo'limni tanlang.")

    elif t == B_SUPPORT:
        await state.set_state(States.waiting_support)
        await m.answer("✍️ <b>Savolingizni yozib qoldiring</b> — admin tez orada javob beradi 📨")

    elif t == B_INFO:
        await m.answer(f"ℹ️ <b>Ma'lumot</b>\n\n👨‍💻 Yaratuvchi: {CREATOR}\n📅 Sana: {CREATED_DATE}")

    elif t == B_ADMIN:
        if uid != ADMIN_ID:
            return await m.answer("❌ Siz admin emassiz!")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="users")],
                [InlineKeyboardButton(text="🔗 Referallar", callback_data="admin_refs")],
                [InlineKeyboardButton(text="🚫 Ban / Unban qilish", callback_data="admin_ban_menu")],
                [InlineKeyboardButton(text="💰 Pul qo'shish", callback_data="admin_topup_menu")],
                [InlineKeyboardButton(text="💸 Pul ayirish", callback_data="admin_minus_menu")],
                [InlineKeyboardButton(text="⭐️ Stars qo'shish", callback_data="admin_st_plus_menu")],
                [InlineKeyboardButton(text="📉 Stars ayirish", callback_data="admin_st_minus_menu")],
                [InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="add_ch")],
                [InlineKeyboardButton(text="🗑 Kanalni o'chirish", callback_data="del_ch_menu")],
            ]
        )
        await m.answer("🛠 <b>Admin panelga xush kelibsiz!</b>", reply_markup=kb)


# ================== STARS SOTIB OLISH ==================
@router.callback_query(F.data.in_({"buy_50", "buy_100"}))
async def process_buy_stars(c: CallbackQuery):
    u = c.from_user
    if c.data == "buy_50":
        stars_count, price_val, price_str = 50, 11000, "11,000 so'm"
    else:
        stars_count, price_val, price_str = 100, 21000, "21,000 so'm"

    bal, stars = await get_user_data(u.id)
    if bal < price_val:
        await c.answer("❌ Hisobingizda yetarli mablag' mavjud emas!", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ?, stars = stars + ? WHERE id=?", (price_val, stars_count, u.id))
        await db.execute("INSERT INTO orders(user_id, stars, price) VALUES(?, ?, ?)", (u.id, stars_count, price_str))
        await db.commit()

    uname = f"@{u.username}" if u.username else "mavjud emas"
    order_text = (
        f"⭐️ <b>Yangi Stars Xaridi!</b>\n\n"
        f"👤 Xaridor: {html.escape(u.full_name)}\n"
        f"🔗 Username: {uname}\n"
        f"🆔 ID: <code>{u.id}</code>\n\n"
        f"⭐️ Miqdori: <b>{stars_count} ta Stars</b>\n"
        f"💵 Narxi: <b>{price_str}</b>"
    )

    try:
        await c.bot.send_message(OTZIV_CHAT_ID, order_text)
    except Exception as e:
        logging.error("Xaridni kanalga yuborishda xatolik: %s", e)

    try:
        await c.bot.send_message(ADMIN_ID, f"🔔 <b>Yangi xarid amalga oshirildi!</b>\n\n{order_text}")
    except Exception as e:
        logging.error("Xaridni adminga yuborishda xatolik: %s", e)

    await c.message.edit_text(
        f"✅ <b>Stars xaridi muvaffaqiyatli amalga oshirildi!</b>\n\n"
        f"⭐️ Miqdori: {stars_count} ta Stars\n"
        f"💵 Narxi: {price_str}\n"
        f"💰 Balansingizdan yechildi va Starslar qo'shildi!"
    )
    await c.answer()


# ================== GIFT YECHISH ==================
@router.callback_query(F.data.in_({"withdraw_15", "withdraw_25"}))
async def process_withdraw(c: CallbackQuery):
    u = c.from_user
    stars_count = 15 if c.data == "withdraw_15" else 25

    bal, user_stars = await get_user_data(u.id)
    if user_stars < stars_count:
        await c.answer(f"❌ Hisobingizda yetarli Stars mavjud emas! (Sizda: {user_stars} ta ⭐️)", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - ? WHERE id=?", (stars_count, u.id))
        await db.execute("INSERT INTO withdraws(user_id, stars) VALUES(?, ?)", (u.id, stars_count))
        await db.commit()

    uname = f"@{u.username}" if u.username else "mavjud emas"
    order_text = (
        f"🎁 <b>Yangi Gift Yechish So'rovi!</b>\n\n"
        f"👤 Foydalanuvchi: {html.escape(u.full_name)}\n"
        f"🔗 Username: {uname}\n"
        f"🆔 ID: <code>{u.id}</code>\n\n"
        f"⭐️ Miqdori: <b>{stars_count} ta Stars</b>"
    )

    try:
        await c.bot.send_message(OTZIV_CHAT_ID, order_text)
    except Exception as e:
        logging.error("Gift so'rovini kanalga yuborishda xatolik: %s", e)

    try:
        await c.bot.send_message(ADMIN_ID, f"🔔 <b>Yangi gift so'rovi tushdi!</b>\n\n{order_text}")
    except Exception as e:
        logging.error("Gift so'rovini adminga yuborishda xatolik: %s", e)

    await c.message.edit_text(
        f"✅ <b>Gift yechish so'rovingiz qabul qilindi!</b>\n\n"
        f"⭐️ Miqdori: {stars_count} ta Stars\n"
        f"💰 Hisobingizdan yechildi."
    )
    await c.answer()


# ================== ADMIN: BAN / UNBAN ==================
@router.callback_query(F.data == "admin_ban_menu")
async def admin_ban_menu(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    await state.set_state(States.waiting_ban)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 Bandan olish menyusi", callback_data="unban_list")]]
    )
    await c.message.answer("🚫 Ban qilmoqchi bo'lgan foydalanuvchi <b>ID sini</b> yuboring:", reply_markup=kb)
    await c.answer()


@router.message(States.waiting_ban, F.text)
async def confirm_ban_user(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("⚠️ Faqat raqamdan iborat foydalanuvchi ID sini yuboring:")

    target_id = int(m.text)
    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚨 Ha, Banlash", callback_data=f"do_ban_{target_id}")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_admin")],
        ]
    )
    await m.answer(f"❓ Foydalanuvchi ID: <code>{target_id}</code>\nNima qilmoqchisiz?", reply_markup=kb)


@router.callback_query(F.data.regexp(r"^do_ban_\d+$"))
async def execute_ban(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    target_id = int(c.data.replace("do_ban_", ""))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=1 WHERE id=?", (target_id,))
        await db.commit()

    try:
        await c.bot.send_message(target_id, "❌ Siz botdan bloklandingiz!")
    except Exception:
        pass

    await c.message.edit_text(f"✅ <code>{target_id}</code> ID raqamli foydalanuvchi muvaffaqiyatli ban qilindi!")
    await c.answer()


@router.callback_query(F.data == "unban_list")
async def unban_list_menu(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, full_name FROM users WHERE is_banned=1")
        rows = await cur.fetchall()

    if not rows:
        return await c.answer("📭 Ban qilingan foydalanuvchilar yo'q.", show_alert=True)

    rows_kb = [
        [InlineKeyboardButton(text=f"✅ Bandan olish: {name} ({uid})", callback_data=f"unban_{uid}")]
        for uid, name in rows
    ]
    await c.message.edit_text(
        "📋 Ban qilinganlar ro'yxati (bandan olish uchun bosing):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb),
    )
    await c.answer()


@router.callback_query(F.data.regexp(r"^unban_\d+$"))
async def execute_unban(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    target_id = int(c.data.replace("unban_", ""))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=0 WHERE id=?", (target_id,))
        await db.commit()

    try:
        await c.bot.send_message(target_id, "✅ Bandan olindingiz! Endi botdan yana foydalanishingiz mumkin 🎉")
    except Exception:
        pass

    await c.message.edit_text(f"✅ <code>{target_id}</code> ID li foydalanuvchi bandan olindi!")
    await c.answer()


# ================== ADMIN: PUL QO'SHISH ==================
@router.callback_query(F.data == "admin_topup_menu")
async def admin_topup_menu(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    await state.set_state(States.waiting_topup)
    await c.message.answer(
        "💰 <b>Pul qo'shish uchun quyidagi formatda yuboring:</b>\n\n"
        "<code>[summa] [foydalanuvchi_id]</code>\n"
        "Masalan: <code>11000 8327302562</code>"
    )
    await c.answer()


@router.message(States.waiting_topup, F.text)
async def process_topup(m: Message, state: FSMContext):
    parts = m.text.strip().split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return await m.answer(
            "⚠️ Xato format! Qaytadan quyidagicha yuboring:\n"
            "<code>[summa] [foydalanuvchi_id]</code> (Masalan: <code>11000 8327302562</code>)"
        )

    summa = int(parts[0])
    target_id = int(parts[1])
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE id=?", (target_id,))
        if not await cur.fetchone():
            return await m.answer(f"❌ <code>{target_id}</code> ID raqamli foydalanuvchi bazadan topilmadi!")

        await db.execute("UPDATE users SET balance = balance + ? WHERE id=?", (summa, target_id))
        await db.commit()

    try:
        await m.bot.send_message(target_id, f"🎉 Sizning hisobingizga <b>{summa} so'm</b> qo'shildi! 💰")
    except Exception:
        pass

    await m.answer(f"✅ Muvaffaqiyatli! <code>{target_id}</code> ID raqamli foydalanuvchiga <b>{summa} so'm</b> qo'shildi.")


# ================== ADMIN: PUL AYIRISH ==================
@router.callback_query(F.data == "admin_minus_menu")
async def admin_minus_menu(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    await state.set_state(States.waiting_minus)
    await c.message.answer(
        "💸 <b>Pul ayirish uchun quyidagi formatda yuboring:</b>\n\n"
        "<code>[summa] [foydalanuvchi_id]</code>\n"
        "Masalan: <code>5000 8327302562</code> yoki <code>ayir 5000 8327302562</code>"
    )
    await c.answer()


@router.message(States.waiting_minus, F.text)
async def process_minus_balance(m: Message, state: FSMContext):
    text = m.text.strip()
    if text.lower().startswith("ayir"):
        text = text[4:].strip()

    parts = text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return await m.answer(
            "⚠️ Xato format! Qaytadan quyidagicha yuboring:\n"
            "<code>[summa] [foydalanuvchi_id]</code> (Masalan: <code>5000 8327302562</code>)"
        )

    summa = int(parts[0])
    target_id = int(parts[1])
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE id=?", (target_id,))
        row = await cur.fetchone()
        if not row:
            return await m.answer(f"❌ <code>{target_id}</code> ID raqamli foydalanuvchi bazadan topilmadi!")

        await db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (summa, target_id))
        await db.commit()

    try:
        await m.bot.send_message(target_id, f"⚠️ Sizning hisobingizdan <b>{summa} so'm</b> ayirildi! 💸")
    except Exception:
        pass

    await m.answer(f"✅ Muvaffaqiyatli! <code>{target_id}</code> ID li foydalanuvchidan <b>{summa} so'm</b> ayirildi.")


# ================== ADMIN: STARS QO'SHISH ==================
@router.callback_query(F.data == "admin_st_plus_menu")
async def admin_st_plus_menu(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    await state.set_state(States.waiting_st_plus)
    await c.message.answer(
        "⭐️ <b>Stars qo'shish uchun quyidagi formatda yuboring:</b>\n\n"
        "<code>[stars_soni] [foydalanuvchi_id]</code>\n"
        "Yoki: <code>st [stars_soni] [foydalanuvchi_id]</code>\n"
        "Masalan: <code>10 8327302562</code>"
    )
    await c.answer()


@router.message(States.waiting_st_plus, F.text)
async def process_st_plus(m: Message, state: FSMContext):
    text = m.text.strip()
    if text.lower().startswith("st"):
        text = text[2:].strip()

    parts = text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return await m.answer(
            "⚠️ Xato format! Qaytadan quyidagicha yuboring:\n"
            "<code>[stars_soni] [foydalanuvchi_id]</code> (Masalan: <code>10 8327302562</code>)"
        )

    stars = int(parts[0])
    target_id = int(parts[1])
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE id=?", (target_id,))
        if not await cur.fetchone():
            return await m.answer(f"❌ <code>{target_id}</code> ID raqamli foydalanuvchi bazadan topilmadi!")

        await db.execute("UPDATE users SET stars = stars + ? WHERE id=?", (stars, target_id))
        await db.commit()

    try:
        await m.bot.send_message(target_id, f"🎉 Sizga admin tomonidan <b>{stars} ta ⭐️ Stars</b> qo'shildi!")
    except Exception:
        pass

    await m.answer(f"✅ Muvaffaqiyatli! <code>{target_id}</code> ID li foydalanuvchiga <b>{stars} ta ⭐️ Stars</b> qo'shildi.")


# ================== ADMIN: STARS AYIRISH ==================
@router.callback_query(F.data == "admin_st_minus_menu")
async def admin_st_minus_menu(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    await state.set_state(States.waiting_st_minus)
    await c.message.answer(
        "📉 <b>Stars ayirish uchun quyidagi formatda yuboring:</b>\n\n"
        "<code>[stars_soni] [foydalanuvchi_id]</code>\n"
        "Yoki: <code>sta [stars_soni] [foydalanuvchi_id]</code>\n"
        "Masalan: <code>5 8327302562</code>"
    )
    await c.answer()


@router.message(States.waiting_st_minus, F.text)
async def process_st_minus(m: Message, state: FSMContext):
    text = m.text.strip()
    if text.lower().startswith("sta") or text.lower().startswith("st"):
        text = text[3:].strip() if text.lower().startswith("sta") else text[2:].strip()

    parts = text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return await m.answer(
            "⚠️ Xato format! Qaytadan quyidagicha yuboring:\n"
            "<code>[stars_soni] [foydalanuvchi_id]</code> (Masalan: <code>5 8327302562</code>)"
        )

    stars = int(parts[0])
    target_id = int(parts[1])
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT stars FROM users WHERE id=?", (target_id,))
        row = await cur.fetchone()
        if not row:
            return await m.answer(f"❌ <code>{target_id}</code> ID raqamli foydalanuvchi bazadan topilmadi!")

        await db.execute("UPDATE users SET stars = stars - ? WHERE id=?", (stars, target_id))
        await db.commit()

    try:
        await m.bot.send_message(target_id, f"⚠️ Hisobingizdan admin tomonidan <b>{stars} ta ⭐️ Stars</b> ayirildi!")
    except Exception:
        pass

    await m.answer(f"✅ Muvaffaqiyatli! <code>{target_id}</code> ID li foydalanuvchidan <b>{stars} ta ⭐️ Stars</b> ayirildi.")


# ================== ADMIN: KANAL QO'SHISH/O'CHIRISH ==================
@router.callback_query(F.data == "add_ch")
async def admin_add_channel(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    await state.set_state(States.waiting_channel)
    await c.message.answer(
        "📢 Yangi kanal username-ini yuboring (masalan: <code>@kanal_username</code>):\n\n"
        "⚠️ Avval botni shu kanalga <b>admin</b> qilib qo'shing!"
    )
    await c.answer()


@router.message(States.waiting_channel, F.text)
async def save_new_channel(m: Message, state: FSMContext):
    ch_username = m.text.strip()
    if not ch_username.startswith("@") or " " in ch_username:
        return await m.answer("⚠️ Xato format! Username <code>@</code> bilan boshlanishi kerak:")

    try:
        chat = await m.bot.get_chat(ch_username)
        me = await m.bot.get_me()
        bot_member = await m.bot.get_chat_member(ch_username, me.id)
    except Exception:
        return await m.answer("⚠️ Kanal topilmadi yoki bot u yerda admin emas. Qaytadan yuboring:")

    if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return await m.answer("⚠️ Bot bu kanalda <b>admin emas</b>! Botni admin qiling va qaytadan yuboring:")

    await state.clear()
    title = f"📢 {chat.title or ch_username[1:]}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO channels(username, title) VALUES(?, ?)", (ch_username, title))
        await db.commit()

    await m.answer(f"✅ <b>{html.escape(ch_username)}</b> majburiy obuna ro'yxatiga qo'shildi! 🎉")


@router.callback_query(F.data == "del_ch_menu")
async def delete_channel_menu(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    channels = await get_channels()
    if not channels:
        return await c.answer("📭 Hozircha ulangan kanallar yo'q", show_alert=True)

    rows = [
        [InlineKeyboardButton(text=f"❌ O'chirish: {title}", callback_data=f"delch_{username}")]
        for username, title in channels
    ]
    await c.message.answer("🗑 O'chirmoqchi bo'lgan kanalni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@router.callback_query(F.data.startswith("delch_"))
async def remove_channel(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    ch_username = c.data.replace("delch_", "", 1)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM channels WHERE username=?", (ch_username,))
        await db.commit()

    await c.message.edit_text(f"✅ <b>{html.escape(ch_username)}</b> majburiy obunadan olib tashlandi!")
    await c.answer()


# ================== MUROJAAT + ADMIN JAVOBI ==================
@router.message(States.waiting_support, F.text)
async def support_msg(m: Message, state: FSMContext):
    await state.clear()
    u = m.from_user
    uname = f"@{u.username}" if u.username else "yo'q"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✍️ Javob berish", callback_data=f"reply_{u.id}_{m.message_id}")
        ]]
    )
    try:
        await m.bot.send_message(
            ADMIN_ID,
            "☎️ <b>Yangi murojaat</b>\n\n"
            f"👤 {html.escape(u.full_name)}\n🔗 Username: {uname}\n🆔 <code>{u.id}</code>\n\n"
            f"💬 {html.escape(m.text)}",
            reply_markup=kb,
        )
    except Exception as e:
        logging.warning("Adminga yuborilmadi: %s", e)
    await m.answer("✅ Savolingiz adminga yuborildi. Tez orada javob beramiz 📨")


@router.callback_query(F.data.regexp(r"^reply_\d+_\d+$"))
async def admin_reply_start(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    _, uid, mid = c.data.split("_")
    await state.set_state(States.waiting_reply)
    await state.update_data(target_id=int(uid), target_msg_id=int(mid))
    await c.message.answer(
        f"✍️ <code>{uid}</code> ga yuboriladigan javobni yozing.\n"
        "Bekor qilish uchun /start bosing."
    )
    await c.answer()


@router.message(States.waiting_reply, F.text)
async def admin_reply_send(m: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data["target_id"]
    target_msg_id = data["target_msg_id"]
    await state.clear()

    text = f"📩 <b>Admin javobi:</b>\n\n{html.escape(m.text)}"
    try:
        await m.bot.send_message(target_id, text, reply_to_message_id=target_msg_id)
    except Exception:
        try:
            await m.bot.send_message(target_id, text)
        except Exception as e:
            logging.warning("Javob yuborilmadi: %s", e)
            return await m.answer("❌ Javob yuborilmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).")

    await m.answer("✅ Javob foydalanuvchiga yuborildi.")


@router.callback_query(F.data == "cancel_admin")
async def cancel_admin(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("❌ Amaliyot bekor qilindi.")
    await c.answer()


# ================== ADMIN: FOYDALANUVCHILAR ==================
@router.callback_query(F.data == "users")
async def users_list(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT u.id, u.username, u.full_name, u.balance, u.stars,
                   (SELECT COUNT(*) FROM users r WHERE r.referrer_id = u.id)
            FROM users u ORDER BY u.rowid
            """
        )
        rows = await cur.fetchall()
    await c.answer()
    if not rows:
        return await c.message.answer("📭 Foydalanuvchilar yo'q")

    lines = []
    for i, (uid, username, full_name, bal, stars, invited) in enumerate(rows, 1):
        uname = f"@{html.escape(username)}" if username else "username yo'q"
        name = html.escape(full_name or "Noma'lum")
        lines.append(
            f"{i}. 👤 <b>{name}</b>\n"
            f"    🔗 {uname}\n"
            f"    🆔 <code>{uid}</code> | 💰 {bal} so'm | ⭐️ {stars} ta Stars | 👥 {invited} ta taklif"
        )

    await c.message.answer(f"👥 <b>Foydalanuvchilar: {len(rows)} ta</b>")
    await send_chunks(c.message, lines, per_chunk=20)


# ================== ADMIN: REFERALLAR ==================
@router.callback_query(F.data == "admin_refs")
async def admin_refs(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("❌ Ruxsat yo'q", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT u.id, u.full_name, u.username,
                   r.id, r.full_name, r.username, r.rewarded
            FROM users r
            JOIN users u ON r.referrer_id = u.id
            ORDER BY u.id, r.rowid
            """
        )
        rows = await cur.fetchall()
    await c.answer()
    if not rows:
        return await c.message.answer("📭 Hali hech kim hech kimni taklif qilmagan.")

    groups: dict[int, dict] = {}
    for uid, u_full, u_name, r_id, r_name, r_uname, rewarded in rows:
        g = groups.setdefault(uid, {"label": user_label(u_full, u_name), "items": []})
        g["items"].append(
            f"    • {user_label(r_name, r_uname)} | 🆔 <code>{r_id}</code> — {'✅ obuna bo`lgan' if rewarded == 1 else '⏳ obuna bo`lmagan'}"
        )

    blocks = [
        f"👤 {g['label']}\n🆔 <code>{uid}</code> — taklif qilganlari ({len(g['items'])} ta):\n"
        + "\n".join(g["items"])
        for uid, g in groups.items()
    ]

    await c.message.answer(f"🔗 <b>Referallar: {len(rows)} ta taklif</b>")
    await send_chunks(c.message, blocks, per_chunk=10)


# ================== MAIN (ISHGA TUSHIRISH) ==================
async def main():
    logging.basicConfig(level=logging.INFO)
    if ":" not in BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN to'g'ri emas! Faylning tepasida BOT_TOKEN = \"...\" qatoriga "
            "@BotFather bergan haqiqiy tokenni yozing."
        )
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    sub_mw = SubMiddleware()
    router.message.middleware(sub_mw)
    router.callback_query.middleware(sub_mw)
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
