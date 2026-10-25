import logging
import os
import sqlite3
import threading
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# تنظیمات لاگ‌گیری
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TOKEN = "8832108690:AAGXYR-sijblIeMapfS_2sgwxK0X1sXWfUo"

# لیست آیدی‌های مجاز ادمین (بدون علامت @ و با حروف کوچک)
ADMIN_USERNAMES = {"arz_125", "atabakabadi", "arisdr"}


def is_admin(user):
  if not user or not user.username:
    return False
  return user.username.lower() in ADMIN_USERNAMES


def admin_required(func):
  async def wrapper(
      update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
  ):
    user = update.effective_user
    if not is_admin(user):
      await update.message.reply_text(
          "❌ متأسفم، شما دسترسی لازم برای استفاده از این بخش را ندارید."
      )
      return
    return await func(update, context, *args, **kwargs)

  return wrapper


# کیبورد پنل ادمین
def get_admin_keyboard():
  keyboard = [
      ["🕒 ساعت بازی", "📅 روز و تاریخ"],
      ["📍 مکان بازی", "👤 گرداننده"],
      ["📋 مشاهده لیست", "📤 ارسال دعوت همگانی"],
      ["📢 ارسال پیام آزاد به همه"],
      ["➕ افزودن بازیکن", "❌ حذف بازیکن"],
      ["✏️ ویرایش بازیکن", "👤 ثبت پروفایل"],
      ["💬 متن ادمین", "🙏 ارسال پیام تشکر"],
      ["🔄 ریست لیست‌ها"],
  ]
  return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# --- راه‌اندازی و ساخت پایگاه داده (SQLite) با قابلیت مهاجرت ایمن ---
def init_db():
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            identifier TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        )
    """)

  try:
    cursor.execute("ALTER TABLE known_users ADD COLUMN username TEXT")
  except sqlite3.OperationalError:
    pass

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS players_list (
            user_id INTEGER PRIMARY KEY,
            raw_name TEXT,
            status TEXT,
            arrival TEXT
        )
    """)

  conn.commit()

  default_configs = {
      "day": "شنبه",
      "date": "۳۰ آبان",
      "time": "۲۰:۰۰",
      "location": "کافه بازی پرپل",
      "host": "خانم آرزو صدرزاده",
      "admin_text": (
          "لطفاً وضعیت حضور خود را مشخص کنید تا برنامه‌ریزی نهایی انجام شود."
      ),
  }
  for k, v in default_configs.items():
    cursor.execute(
        "INSERT OR IGNORE INTO game_config (key, value) VALUES (?, ?)", (k, v)
    )

  conn.commit()
  conn.close()


init_db()


# --- توابع کمکی دیتابیس ---
def get_config(key):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT value FROM game_config WHERE key = ?", (key,))
  row = cursor.fetchone()
  conn.close()
  return row[0] if row else ""


def set_config(key, value):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO game_config (key, value) VALUES (?, ?)",
      (key, value),
  )
  conn.commit()
  conn.close()


def to_persian_numbers(text):
  if not text:
    return ""
  digits_map = {
      "0": "۰",
      "1": "۱",
      "2": "۲",
      "3": "۳",
      "4": "۴",
      "5": "۵",
      "6": "۶",
      "7": "۷",
      "8": "۸",
      "9": "۹",
  }
  return "".join(digits_map.get(char, char) for char in str(text))


def register_user_meta(user):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  uname = user.username.lower() if user.username else None
  cursor.execute(
      "INSERT OR REPLACE INTO known_users (user_id, username) VALUES (?, ?)",
      (user.id, uname),
  )
  conn.commit()
  conn.close()


def get_display_name(user_id, telegram_default_name):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT name FROM user_profiles WHERE identifier = ?", (str(user_id),)
  )
  row = cursor.fetchone()
  if not row or not row[0]:
    cursor.execute(
        "SELECT username FROM known_users WHERE user_id = ?", (user_id,)
    )
    uname_row = cursor.fetchone()
    if uname_row and uname_row[0]:
      cursor.execute(
          "SELECT name FROM user_profiles WHERE identifier = ?", (uname_row[0],)
      )
      row = cursor.fetchone()
  conn.close()
  if row and row[0]:
    return row[0]
  return telegram_default_name


def get_display_phone(user_id):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT phone FROM user_profiles WHERE identifier = ?", (str(user_id),)
  )
  row = cursor.fetchone()
  if not row or not row[0] or row[0] == "ثبت نشده":
    cursor.execute(
        "SELECT username FROM known_users WHERE user_id = ?", (user_id,)
    )
    uname_row = cursor.fetchone()
    if uname_row and uname_row[0]:
      cursor.execute(
          "SELECT phone FROM user_profiles WHERE identifier = ?", (uname_row[0],)
      )
      row = cursor.fetchone()
  conn.close()
  if row and row[0]:
    return to_persian_numbers(row[0])
  return "ثبت نشده"


def parse_game_time():
  try:
    clean_time = get_config("time")
    for en, fa in [
        ("۰", "0"),
        ("۱", "1"),
        ("۲", "2"),
        ("۳", "3"),
        ("۴", "4"),
        ("۵", "5"),
        ("۶", "6"),
        ("۷", "7"),
        ("۸", "8"),
        ("۹", "9"),
    ]:
      clean_time = clean_time.replace(fa, en)
    parts = clean_time.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return h, m
  except:
    return 20, 0


def get_time_picker_keyboard(hour, minute):
  time_str = to_persian_numbers(f"{hour:02d}:{minute:02d}")
  keyboard = [
      [
          InlineKeyboardButton(
              f"🕒 ساعت انتخابی: {time_str}", callback_data="tp_ignore"
          )
      ],
      [
          InlineKeyboardButton("➕ ساعت", callback_data="tp_h_plus"),
          InlineKeyboardButton("➖ ساعت", callback_data="tp_h_minus"),
      ],
      [
          InlineKeyboardButton("➕ ۱۵ دقیقه", callback_data="tp_m_plus"),
          InlineKeyboardButton("➖ ۱۵ دقیقه", callback_data="tp_m_minus"),
      ],
      [
          InlineKeyboardButton(
              "✅ ثبت نهایی ساعت حضور", callback_data="tp_submit"
          )
      ],
  ]
  return InlineKeyboardMarkup(keyboard)


user_time_state = {}
waiting_for_arrival = set()
waiting_for_reg = set()
admin_states = {}


# --- پنل مدیریت ادمین ---
@admin_required
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      "🛠 **پنل مدیریت ربات مافیا**\nلطفاً یکی از گزینه‌های زیر را از روی"
      " کیبورد انتخاب کنید:",
      reply_markup=get_admin_keyboard(),
      parse_mode="Markdown",
  )


# --- دستورات تنظیمات ادمین ---
@admin_required
async def set_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if context.args:
    new_t = " ".join(context.args)
    set_config("time", new_t)
    await update.message.reply_text(
        f"✅ ساعت بازی به '{to_persian_numbers(new_t)}' تغییر یافت.",
        reply_markup=get_admin_keyboard(),
    )
  else:
    admin_states[update.effective_user.id] = "waiting_for_time"
    await update.message.reply_text(
        "🕒 لطفاً ساعت جدید بازی را وارد کنید (مثال: `20:00`):",
        parse_mode="Markdown",
    )


@admin_required
async def set_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if context.args:
    day = context.args[0]
    date = " ".join(context.args[1:]) if len(context.args) > 1 else get_config(
        "date"
    )
    set_config("day", day)
    set_config("date", date)
    await update.message.reply_text(
        f"✅ روز بازی به '{day} ({date})' تغییر یافت.",
        reply_markup=get_admin_keyboard(),
    )
  else:
    admin_states[update.effective_user.id] = "waiting_for_day"
    await update.message.reply_text(
        "📅 لطفاً روز و تاریخ بازی را وارد کنید (مثال: `شنبه ۳۰ آبان`):",
        parse_mode="Markdown",
    )


@admin_required
async def set_loc(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if context.args:
    loc = " ".join(context.args)
    set_config("location", loc)
    await update.message.reply_text(
        f"✅ مکان بازی به '{loc}' تغییر یافت.", reply_markup=get_admin_keyboard()
    )
  else:
    admin_states[update.effective_user.id] = "waiting_for_loc"
    await update.message.reply_text("📍 لطفاً مکان جدید بازی را وارد کنید:")


@admin_required
async def set_host(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if context.args:
    host = " ".join(context.args)
    set_config("host", host)
    await update.message.reply_text(
        f"✅ گرداننده به '{host}' تغییر یافت.", reply_markup=get_admin_keyboard()
    )
  else:
    admin_states[update.effective_user.id] = "waiting_for_host"
    await update.message.reply_text("👤 لطفاً نام گرداننده جدید را وارد کنید:")


@admin_required
async def set_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if context.args:
    txt = " ".join(context.args)
    set_config("admin_text", txt)
    await update.message.reply_text(
        "✅ متن اختصاصی ادمین بروزرسانی شد.", reply_markup=get_admin_keyboard()
    )
  else:
    admin_states[update.effective_user.id] = "waiting_for_text"
    await update.message.reply_text(
        "💬 لطفاً متن اختصاصی ادمین برای پیام‌ها را وارد کنید:"
    )


@admin_required
async def set_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if len(context.args) < 2:
    admin_states[update.effective_user.id] = "waiting_for_setuser"
    await update.message.reply_text(
        "👤 لطفاً اطلاعات کاربر را به همراه یوزرنیم، نام و شماره تماس وارد"
        " کنید:\nمثال: `@Arz_125 علیرضا زهرابی 09369423155`",
        parse_mode="Markdown",
    )
    return
  await process_setuser_logic(update, context.args)


async def process_setuser_logic(update, args):
  identifier = args[0].replace("@", "").lower()
  rest_args = args[1:]
  phone = "ثبت نشده"
  name_parts = []

  for arg in rest_args:
    clean_arg = arg
    for fa, en in [
        ("۰", "0"),
        ("۱", "1"),
        ("۲", "2"),
        ("۳", "3"),
        ("۴", "4"),
        ("۵", "5"),
        ("۶", "6"),
        ("۷", "7"),
        ("۸", "8"),
        ("۹", "9"),
    ]:
      clean_arg = clean_arg.replace(fa, en)
    if clean_arg.isdigit() or clean_arg.startswith("+"):
      phone = arg
    else:
      name_parts.append(arg)

  raw_name = " ".join(name_parts) if name_parts else identifier
  name = raw_name.replace(".", " ").replace("_", " ")

  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO user_profiles (identifier, name, phone) VALUES"
      " (?, ?, ?)",
      (identifier, name, phone),
  )
  cursor.execute("SELECT user_id FROM known_users WHERE username = ?", (identifier,))
  user_row = cursor.fetchone()
  if user_row:
    numeric_id = str(user_row[0])
    cursor.execute(
        "INSERT OR REPLACE INTO user_profiles (identifier, name, phone) VALUES"
        " (?, ?, ?)",
        (numeric_id, name, phone),
    )
  conn.commit()
  conn.close()

  await update.message.reply_text(
      f"✅ اطلاعات با موفقیت ثبت شد:\n👤 نام: {name}\n📞 تلفن:"
      f" {to_persian_numbers(phone)}",
      reply_markup=get_admin_keyboard(),
      parse_mode="Markdown",
  )


# --- ارسال پیام همگانی دعوت به بازی ---
@admin_required
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT user_id FROM known_users")
  users = cursor.fetchall()
  conn.close()

  if not users:
    await update.message.reply_text(
        "هنوز هیچ کاربری ربات را استارت نکرده است.",
        reply_markup=get_admin_keyboard(),
    )
    return

  keyboard = [
      [
          InlineKeyboardButton("✅ بله، می‌آیم", callback_data="yes"),
          InlineKeyboardButton("❌ خیر، نمی‌آیم", callback_data="no"),
      ],
      [InlineKeyboardButton("⏳ بعداً خبر می‌دهم", callback_data="later")],
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)

  success_count = 0
  p_time = to_persian_numbers(get_config("time"))
  for (u_id,) in users:
    display_name = get_display_name(u_id, "دوست")
    message_text = (
        f"سلام {display_name} عزیز\nامروز {get_config('day')} تاریخ"
        f" {get_config('date')} در محل {get_config('location')} ساعت {p_time}"
        f" با گردانندگی {get_config('host')} بازی داریم.\n\n{get_config('admin_text')}"
    )
    try:
      await context.bot.send_message(
          chat_id=u_id, text=message_text, reply_markup=reply_markup
      )
      success_count += 1
    except Exception as e:
      logging.error(f"خطا در ارسال پیام به {u_id}: {e}")

  await update.message.reply_text(
      f"📤 پیام دعوت با موفقیت به {to_persian_numbers(success_count)} نفر ارسال"
      " شد.",
      reply_markup=get_admin_keyboard(),
  )


# --- ارسال پیام آزاد به همه کاربران ---
@admin_required
async def custom_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if context.args:
    await execute_custom_broadcast(update, context, " ".join(context.args))
  else:
    admin_states[update.effective_user.id] = "waiting_for_custom_broadcast"
    await update.message.reply_text(
        "📢 لطفاً متن دلخواه خود را برای ارسال به تمام کاربران وارد کنید:"
    )


async def execute_custom_broadcast(update, context, custom_text):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT user_id FROM known_users")
  users = cursor.fetchall()
  conn.close()

  if not users:
    await update.message.reply_text(
        "هنوز هیچ کاربری ربات را استارت نکرده است.",
        reply_markup=get_admin_keyboard(),
    )
    return

  success_count = 0
  for (u_id,) in users:
    try:
      await context.bot.send_message(chat_id=u_id, text=custom_text)
      success_count += 1
    except Exception as e:
      logging.error(f"خطا در ارسال پیام آزاد به {u_id}: {e}")

  await update.message.reply_text(
      f"📤 پیام دلخواه با موفقیت به {to_persian_numbers(success_count)} نفر ارسال"
      " شد.",
      reply_markup=get_admin_keyboard(),
  )


# --- ارسال پیام تشکر ---
@admin_required
async def send_thanks(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not context.args:
    admin_states[update.effective_user.id] = "waiting_for_thanks"
    await update.message.reply_text(
        "🙏 لطفاً متن پیام تشکر برای بازیکنان قطعی را وارد کنید:"
    )
    return
  await process_thanks_logic(update, " ".join(context.args))


async def process_thanks_logic(update, thank_message_body):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT user_id, raw_name FROM players_list WHERE status = 'yes'")
  confirmed = cursor.fetchall()
  conn.close()

  if not confirmed:
    await update.message.reply_text(
        "❌ لیست بازیکنان قطعی (بله) خالی است.",
        reply_markup=get_admin_keyboard(),
    )
    return

  success_count = 0
  for u_id, raw_name in confirmed:
    display_name = get_display_name(u_id, raw_name)
    message_text = f"سلام {display_name} عزیز\n{thank_message_body}"
    try:
      if u_id < 900000000:
        await update.bot.send_message(chat_id=u_id, text=message_text)
        success_count += 1
    except Exception as e:
      logging.error(f"خطا در ارسال پیام تشکر به {u_id}: {e}")

  await update.message.reply_text(
      f"🙏 پیام تشکر با موفقیت به {to_persian_numbers(success_count)} نفر ارسال"
      " شد.",
      reply_markup=get_admin_keyboard(),
  )


# --- مشاهده لیست‌ها ---
@admin_required
async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT user_id, raw_name, arrival FROM players_list WHERE status = 'yes'"
  )
  confirmed = cursor.fetchall()

  cursor.execute(
      "SELECT user_id, raw_name FROM players_list WHERE status = 'later'"
  )
  later = cursor.fetchall()

  cursor.execute("SELECT COUNT(*) FROM known_users")
  total_known = cursor.fetchone()[0]
  conn.close()

  rtl_mark = "\u200F"
  confirmed_text = f"{rtl_mark}🟢 **لیست بازیکنان قطعی (بله):**\n"
  if not confirmed:
    confirmed_text += f"{rtl_mark}هنوز کسی ثبت‌نام نکرده است.\n"
  else:
    for idx, (u_id, raw_name, arrival) in enumerate(confirmed, 1):
      display_name = get_display_name(u_id, raw_name)
      phone = get_display_phone(u_id)
      persian_idx = to_persian_numbers(idx)
      persian_arrival = to_persian_numbers(arrival)
      confirmed_text += (
          f"{rtl_mark}{persian_idx}. {display_name} | ساعت حضور:"
          f" {persian_arrival} | تلفن: {phone}\n"
      )

  later_text = f"\n{rtl_mark}🟡 **لیست افراد مردد (بعداً خبر می‌دهم):**\n"
  if not later:
    later_text += f"{rtl_mark}خالی است.\n"
  else:
    for idx, (u_id, raw_name) in enumerate(later, 1):
      display_name = get_display_name(u_id, raw_name)
      phone = get_display_phone(u_id)
      persian_idx = to_persian_numbers(idx)
      later_text += f"{rtl_mark}{persian_idx}. {display_name} | تلفن: {phone}\n"

  total_users_str = to_persian_numbers(total_known)
  full_output = (
      f"{confirmed_text}{later_text}\n{rtl_mark}📌 تعداد کل کاربران شناخته شده:"
      f" {total_users_str} نفر"
  )
  await update.message.reply_text(
      full_output, reply_markup=get_admin_keyboard(), parse_mode="Markdown"
  )


# --- ریست کردن لیست‌ها ---
@admin_required
async def reset_lists(update: Update, context: ContextTypes.DEFAULT_TYPE):
  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM players_list")
  conn.commit()
  conn.close()
  await update.message.reply_text(
      "🔄 تمام لیست‌های بازی پاک و ریست شدند.",
      reply_markup=get_admin_keyboard(),
  )


# --- دستور اضافه کردن دستی بازیکن /add ---
@admin_required
async def add_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not context.args:
    admin_states[update.effective_user.id] = "waiting_for_add"
    await update.message.reply_text(
        "➕ لطفاً مشخصات بازیکن را وارد کنید:\nمثال: `کاتیون ابدی 09123456789 yes"
        " 22:00`",
        parse_mode="Markdown",
    )
    return
  await process_add_logic(update, context.args)


async def process_add_logic(update, args):
  status = "yes"
  arrival = to_persian_numbers(get_config("time"))
  phone = "ثبت نشده"
  name_parts = []

  for arg in args:
    arg_lower = arg.lower()
    clean_arg = arg
    for fa, en in [
        ("۰", "0"),
        ("۱", "1"),
        ("۲", "2"),
        ("۳", "3"),
        ("۴", "4"),
        ("۵", "5"),
        ("۶", "6"),
        ("۷", "7"),
        ("۸", "8"),
        ("۹", "9"),
    ]:
      clean_arg = clean_arg.replace(fa, en)

    if arg_lower in ["yes", "بله"]:
      status = "yes"
    elif arg_lower in ["later", "مردد", "بعداً", "بعدا"]:
      status = "later"
    elif (
        clean_arg.isdigit()
        and (clean_arg.startswith("0") or clean_arg.startswith("+"))
        and len(clean_arg) >= 10
    ):
      phone = to_persian_numbers(arg)
    elif ":" in arg or (
        clean_arg.replace(":", "").isdigit() and len(clean_arg) >= 2
    ):
      arrival = to_persian_numbers(arg)
    else:
      name_parts.append(arg)

  if not name_parts:
    await update.message.reply_text(
        "❌ لطفاً نام بازیکن را وارد کنید.", reply_markup=get_admin_keyboard()
    )
    return

  raw_name = " ".join(name_parts)
  name = raw_name.replace(".", " ").replace("_", " ")
  dummy_id = 800000000 + hash(name) % 10000000

  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO user_profiles (identifier, name, phone) VALUES"
      " (?, ?, ?)",
      (str(dummy_id), name, phone),
  )
  cursor.execute(
      "INSERT OR REPLACE INTO players_list (user_id, raw_name, status,"
      " arrival) VALUES (?, ?, ?, ?)",
      (dummy_id, name, status, arrival if status == "yes" else "ثبت نشده"),
  )
  conn.commit()
  conn.close()

  await update.message.reply_text(
      f"✅ {name} با موفقیت ثبت شد.\n📞 تلفن: {phone} | وضعیت: {status}",
      reply_markup=get_admin_keyboard(),
  )


# --- دستور حذف بازیکن /remove ---
@admin_required
async def remove_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not context.args:
    admin_states[update.effective_user.id] = "waiting_for_remove"
    await update.message.reply_text(
        "❌ لطفاً نام بازیکنی که می‌خواهید حذف کنید را وارد نمایید:"
    )
    return
  await process_remove_logic(update, context.args)


async def process_remove_logic(update, args):
  target_list = "all"
  last_arg = args[-1].lower()
  if last_arg in ["yes", "بله", "قطعی"]:
    target_list = "yes"
    search_query = " ".join(args[:-1]).lower()
  elif last_arg in ["later", "مردد", "بعدا", "بعداً"]:
    target_list = "later"
    search_query = " ".join(args[:-1]).lower()
  else:
    search_query = " ".join(args).lower()

  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT user_id, raw_name, status FROM players_list")
  all_players = cursor.fetchall()

  found_uids = []
  for u_id, raw_name, status in all_players:
    if target_list != "all" and status != target_list:
      continue
    display_name = get_display_name(u_id, raw_name).lower()
    if search_query in display_name or search_query in raw_name.lower():
      found_uids.append(u_id)

  if found_uids:
    cursor.executemany(
        "DELETE FROM players_list WHERE user_id = ?",
        [(uid,) for uid in found_uids],
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ بازیکن با مشخصات '{search_query}' از لیست‌ها حذف شد.",
        reply_markup=get_admin_keyboard(),
    )
  else:
    conn.close()
    await update.message.reply_text(
        f"❌ بازیکنی با نام '{search_query}' پیدا نشد.",
        reply_markup=get_admin_keyboard(),
    )


# --- دستور ویرایش /edit ---
@admin_required
async def edit_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not context.args:
    admin_states[update.effective_user.id] = "waiting_for_edit"
    await update.message.reply_text(
        "✏️ لطفاً دستور ویرایش را به این فرمت وارد کنید:\n`اتابک ابدی"
        " 09369423155 23:00`",
        parse_mode="Markdown",
    )
    return
  await process_edit_logic(update, context.args)


async def process_edit_logic(update, args):
  status_val, phone_val, time_val = None, None, None
  name_end_index = len(args)

  i = len(args) - 1
  while i > 0 and (len(args) - i) <= 2:
    arg = args[i]
    arg_lower = arg.lower()
    norm_arg = arg_lower
    for fa, en in [
        ("۰", "0"),
        ("۱", "1"),
        ("۲", "2"),
        ("۳", "3"),
        ("۴", "4"),
        ("۵", "5"),
        ("۶", "6"),
        ("۷", "7"),
        ("۸", "8"),
        ("۹", "9"),
    ]:
      norm_arg = norm_arg.replace(fa, en)

    if arg_lower in ["yes", "بله"] and not status_val:
      status_val = "yes"
      name_end_index = i
      i -= 1
    elif arg_lower in ["later", "مردد", "بعدا", "بعداً"] and not status_val:
      status_val = "later"
      name_end_index = i
      i -= 1
    elif (
        norm_arg.isdigit()
        and (norm_arg.startswith("0") or norm_arg.startswith("+"))
        and len(norm_arg) >= 10
        and not phone_val
    ):
      phone_val = to_persian_numbers(arg)
      name_end_index = i
      i -= 1
    elif (
        ":" in arg or (norm_arg.replace(":", "").isdigit() and len(norm_arg) >= 2)
    ) and not time_val:
      time_val = to_persian_numbers(arg)
      name_end_index = i
      i -= 1
    else:
      break

  search_query = " ".join(args[:name_end_index]).lower()

  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT user_id, raw_name FROM players_list")
  all_players = cursor.fetchall()

  target_uid = None
  for u_id, raw_name in all_players:
    display_name = get_display_name(u_id, raw_name).lower()
    if search_query in display_name or search_query in raw_name.lower():
      target_uid = u_id
      break

  if not target_uid:
    conn.close()
    await update.message.reply_text(
        f"❌ بازیکنی با نام '{search_query}' پیدا نشد.",
        reply_markup=get_admin_keyboard(),
    )
    return

  updates_msg = []
  if status_val:
    cursor.execute(
        "UPDATE players_list SET status = ? WHERE user_id = ?",
        (status_val, target_uid),
    )
    updates_msg.append(f"وضعیت: {status_val}")

  if phone_val:
    cursor.execute(
        "SELECT name FROM user_profiles WHERE identifier = ?", (str(target_uid),)
    )
    profile_row = cursor.fetchone()
    if profile_row and profile_row[0]:
      cursor.execute(
          "UPDATE user_profiles SET phone = ? WHERE identifier = ?",
          (phone_val, str(target_uid)),
      )
    else:
      cursor.execute(
          "SELECT raw_name FROM players_list WHERE user_id = ?", (target_uid,)
      )
      r = cursor.fetchone()
      r_name = r[1] if r else "کاربر"
      cursor.execute(
          "INSERT OR REPLACE INTO user_profiles (identifier, name, phone)"
          " VALUES (?, ?, ?)",
          (str(target_uid), r_name, phone_val),
      )
    updates_msg.append(f"تلفن: {phone_val}")

  if time_val:
    cursor.execute(
        "UPDATE players_list SET arrival = ?, status = 'yes' WHERE user_id = ?",
        (time_val, target_uid),
    )
    updates_msg.append(f"ساعت حضور: {time_val}")

  conn.commit()
  conn.close()

  if updates_msg:
    await update.message.reply_text(
        "✅ اطلاعات بروزرسانی شد:\n"
        + "\n".join([f"🔹 {item}" for item in updates_msg]),
        reply_markup=get_admin_keyboard(),
    )
  else:
    await update.message.reply_text(
        "❌ تغییری اعمال نشد.", reply_markup=get_admin_keyboard()
    )


# --- دستور استارت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  register_user_meta(user)
  u_id = user.id

  if is_admin(user):
    await update.message.reply_text(
        "🛠 به پنل مدیریت ربات مافیا خوش آمدید.\nاز دکمه‌های زیر برای کنترل ربات"
        " استفاده کنید:",
        reply_markup=get_admin_keyboard(),
    )
    return

  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT name, phone FROM user_profiles WHERE identifier = ?", (str(u_id),)
  )
  row = cursor.fetchone()
  if not row and user.username:
    cursor.execute(
        "SELECT name, phone FROM user_profiles WHERE identifier = ?",
        (user.username.lower(),),
    )
  conn.close()

  if row and row[0]:
    display_name = row[0]
    phone = to_persian_numbers(row[1]) if row[1] else "ثبت نشده"
    await update.message.reply_text(
        f"سلام {display_name} عزیز! 👋\nشما در ربات ثبت‌نام کرده‌اید.\n👤 نام:"
        f" {display_name}\n📞 شماره تماس: {phone}\n\nلطفاً منتظر بمانید تا ادمین"
        " زمان و جزئیات بازی جدید را اعلام کند."
    )
  else:
    waiting_for_reg.add(u_id)
    await update.message.reply_text(
        "سلام! به ربات مدیریت بازی خوش آمدید. 🎮\n\nلطفاً در صورت تمایل، **نام و"
        " نام خانوادگی و شماره تماس** خود را وارد کنید تا در لیست‌ها به جای نام"
        " تلگرام نمایش داده شود.\n\nمثال:\n`علیرضا زهرابی 09369423155`",
        parse_mode="Markdown",
    )


# --- مدیریت کلیک روی دکمه‌های شیشه‌ای پیام بازی ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  await query.answer()

  user = query.from_user
  register_user_meta(user)
  u_id = user.id
  display_name = get_display_name(u_id, user.full_name)
  data = query.data

  conn = sqlite3.connect("mafia_bot.db")
  cursor = conn.cursor()

  if data.startswith("tp_"):
    if data == "tp_ignore":
      conn.close()
      return

    if u_id not in user_time_state:
      h, m = parse_game_time()
      user_time_state[u_id] = {"hour": h, "minute": m}

    current_state = user_time_state[u_id]
    h, m = current_state["hour"], current_state["minute"]

    if data == "tp_h_plus":
      h = (h + 1) % 24
    elif data == "tp_h_minus":
      h = (h - 1) % 24
    elif data == "tp_m_plus":
      m = (m + 15) % 60
    elif data == "tp_m_minus":
      m = (m - 15) % 60
    elif data == "tp_submit":
      final_time_str = to_persian_numbers(f"{h:02d}:{m:02d}")
      cursor.execute(
          "INSERT OR REPLACE INTO players_list (user_id, raw_name, status,"
          " arrival) VALUES (?, ?, 'yes', ?)",
          (u_id, user.full_name, final_time_str),
      )
      conn.commit()
      conn.close()
      if u_id in waiting_for_arrival:
        waiting_for_arrival.remove(u_id)
      if u_id in user_time_state:
        del user_time_state[u_id]

      await query.edit_message_text(
          text=(
              f"عالی {display_name} جان! ساعت حضور شما ({final_time_str}) در"
              " دیتابیس ثبت شد. منتظرتان هستیم! 🟢"
          )
      )
      return

    user_time_state[u_id] = {"hour": h, "minute": m}
    conn.close()
    await query.edit_message_reply_markup(
        reply_markup=get_time_picker_keyboard(h, m)
    )
    return

  if data == "yes":
    waiting_for_arrival.add(u_id)
    h, m = parse_game_time()
    user_time_state[u_id] = {"hour": h, "minute": m}
    conn.close()

    clean_prompt_text = (
        f"عالی! حضور {display_name} ثبت شد.\n\nلطفا ساعت تقریبی که میتوانید در"
        " محل بازی حضور داشته باشید رو اعلام کنید . با تشکر"
    )
    await query.edit_message_text(
        text=clean_prompt_text, reply_markup=get_time_picker_keyboard(h, m)
    )

  elif data == "no":
    cursor.execute("DELETE FROM players_list WHERE user_id = ?", (u_id,))
    conn.commit()
    conn.close()
    if u_id in waiting_for_arrival:
      waiting_for_arrival.remove(u_id)
    if u_id in user_time_state:
      del user_time_state[u_id]

    await query.edit_message_text(
        text=(
            f"مشکلی نیست {display_name} عزیز، امیدواریم در بازی‌های بعدی"
            " ببینیمت."
        )
    )

  elif data == "later":
    cursor.execute(
        "INSERT OR REPLACE INTO players_list (user_id, raw_name, status,"
        " arrival) VALUES (?, ?, 'later', 'ثبت نشده')",
        (u_id, user.full_name),
    )
    conn.commit()
    conn.close()
    if u_id in waiting_for_arrival:
      waiting_for_arrival.remove(u_id)
    if u_id in user_time_state:
      if u_id in user_time_state:
        del user_time_state[u_id]

    await query.edit_message_text(
        text=(
            f"باشه {display_name} عزیز، نام شما در لیست افراد مردد ثبت شد."
        )
    )


# --- مدیریت پیام‌ها و دکمه‌های پنل ادمین ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  register_user_meta(user)
  u_id = user.id
  text = update.message.text

  if is_admin(user):
    if text == "🕒 ساعت بازی":
      admin_states[u_id] = "waiting_for_time"
      await update.message.reply_text(
          "🕒 لطفاً ساعت جدید بازی را وارد کنید (مثال: `20:00`):",
          parse_mode="Markdown",
      )
      return
    elif text == "📅 روز و تاریخ":
      admin_states[u_id] = "waiting_for_day"
      await update.message.reply_text(
          "📅 لطفاً روز و تاریخ جدید بازی را وارد کنید (مثال: `شنبه ۳۰ آبان`):",
          parse_mode="Markdown",
      )
      return
    elif text == "📍 مکان بازی":
      admin_states[u_id] = "waiting_for_loc"
      await update.message.reply_text("📍 لطفاً مکان جدید بازی را وارد کنید:")
      return
    elif text == "👤 گرداننده":
      admin_states[u_id] = "waiting_for_host"
      await update.message.reply_text(
          "👤 لطفاً نام گرداننده جدید را وارد کنید:"
      )
      return
    elif text == "💬 متن ادمین":
      admin_states[u_id] = "waiting_for_text"
      await update.message.reply_text(
          "💬 لطفاً متن اختصاصی ادمین را وارد کنید:"
      )
      return
    elif text == "📋 مشاهده لیست":
      await show_list(update, context)
      return
    elif text == "📤 ارسال دعوت همگانی":
      await broadcast(update, context)
      return
    elif text == "📢 ارسال پیام آزاد به همه":
      admin_states[u_id] = "waiting_for_custom_broadcast"
      await update.message.reply_text(
          "📢 لطفاً متن دلخواه خود را برای ارسال به تمام کاربران وارد کنید:"
      )
      return
    elif text == "➕ افزودن بازیکن":
      admin_states[u_id] = "waiting_for_add"
      await update.message.reply_text(
          "➕ لطفاً نام، شماره و وضعیت بازیکن را وارد کنید:\nمثال: `کاتیون ابدی"
          " 09123456789 yes 22:00`",
          parse_mode="Markdown",
      )
      return
    elif text == "❌ حذف بازیکن":
      admin_states[u_id] = "waiting_for_remove"
      await update.message.reply_text(
          "❌ لطفاً نام بازیکنی که می‌خواهید حذف کنید را وارد کنید:"
      )
      return
    elif text == "✏️ ویرایش بازیکن":
      admin_states[u_id] = "waiting_for_edit"
      await update.message.reply_text(
          "✏️ لطفاً اطلاعات ویرایش را وارد کنید:\nمثال: `اتابک ابدی 09369423155"
          " 23:00`",
          parse_mode="Markdown",
      )
      return
    elif text == "👤 ثبت پروفایل":
      admin_states[u_id] = "waiting_for_setuser"
      await update.message.reply_text(
          "👤 لطفاً مشخصات را وارد کنید:\nمثال: `@Arz_125 علیرضا زهرابی"
          " 09369423155`",
          parse_mode="Markdown",
      )
      return
    elif text == "🙏 ارسال پیام تشکر":
      admin_states[u_id] = "waiting_for_thanks"
      await update.message.reply_text(
          "🙏 لطفاً متن پیام تشکر برای بازیکنان قطعی را وارد کنید:"
      )
      return
    elif text == "🔄 ریست لیست‌ها":
      await reset_lists(update, context)
      return

    if u_id in admin_states:
      state = admin_states[u_id]
      del admin_states[u_id]

      if state == "waiting_for_time":
        set_config("time", text)
        await update.message.reply_text(
            f"✅ ساعت بازی به '{to_persian_numbers(text)}' ثبت شد.",
            reply_markup=get_admin_keyboard(),
        )
        return
      elif state == "waiting_for_day":
        parts = text.split(maxsplit=1)
        day = parts[0]
        date = parts[1] if len(parts) > 1 else get_config("date")
        set_config("day", day)
        set_config("date", date)
        await update.message.reply_text(
            f"✅ روز بازی به '{day} ({date})' ثبت شد.",
            reply_markup=get_admin_keyboard(),
        )
        return
      elif state == "waiting_for_loc":
        set_config("location", text)
        await update.message.reply_text(
            f"✅ مکان بازی به '{text}' ثبت شد.",
            reply_markup=get_admin_keyboard(),
        )
        return
      elif state == "waiting_for_host":
        set_config("host", text)
        await update.message.reply_text(
            f"✅ گرداننده به '{text}' ثبت شد.",
            reply_markup=get_admin_keyboard(),
        )
        return
      elif state == "waiting_for_text":
        set_config("admin_text", text)
        await update.message.reply_text(
            "✅ متن اختصاصی ادمین ثبت شد.", reply_markup=get_admin_keyboard()
        )
        return
      elif state == "waiting_for_custom_broadcast":
        await execute_custom_broadcast(update, context, text)
        return
      elif state == "waiting_for_setuser":
        await process_setuser_logic(update, text.split())
        return
      elif state == "waiting_for_add":
        await process_add_logic(update, text.split())
        return
      elif state == "waiting_for_remove":
        await process_remove_logic(update, text.split())
        return
      elif state == "waiting_for_edit":
        await process_edit_logic(update, text.split())
        return
      elif state == "waiting_for_thanks":
        await process_thanks_logic(update, text)
        return

  if u_id in waiting_for_reg:
    parts = text.split()
    phone = "ثبت نشده"
    name_parts = []

    for arg in parts:
      clean_arg = arg
      for fa, en in [
          ("۰", "0"),
          ("۱", "1"),
          ("۲", "2"),
          ("۳", "3"),
          ("۴", "4"),
          ("۵", "5"),
          ("۶", "6"),
          ("۷", "7"),
          ("۸", "8"),
          ("۹", "9"),
      ]:
        clean_arg = clean_arg.replace(fa, en)
      if clean_arg.isdigit() or clean_arg.startswith("+") and len(clean_arg) >= 10:
        phone = arg
      else:
        name_parts.append(arg)

    name = " ".join(name_parts) if name_parts else user.full_name
    name = name.replace(".", " ").replace("_", " ")

    conn = sqlite3.connect("mafia_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO user_profiles (identifier, name, phone) VALUES"
        " (?, ?, ?)",
        (str(u_id), name, phone),
    )
    if user.username:
      cursor.execute(
          "INSERT OR REPLACE INTO user_profiles (identifier, name, phone)"
          " VALUES (?, ?, ?)",
          (user.username.lower(), name, phone),
      )
    conn.commit()
    conn.close()

    waiting_for_reg.remove(u_id)
    await update.message.reply_text(
        f"✅ اطلاعات شما با موفقیت ثبت شد!\n👤 نام: {name}\n📞 تلفن:"
        f" {to_persian_numbers(phone)}\n\nاز این به بعد نام شما در لیست‌ها با"
        " همین عنوان نمایش داده می‌شود. منتظر اعلام زمان بازی از طرف ادمین"
        " باشید."
    )
    return

  if u_id in waiting_for_arrival:
    arrival_time = to_persian_numbers(text)
    display_name = get_display_name(user.id, user.full_name)

    conn = sqlite3.connect("mafia_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO players_list (user_id, raw_name, status,"
        " arrival) VALUES (?, ?, 'yes', ?)",
        (u_id, user.full_name, arrival_time),
    )
    conn.commit()
    conn.close()

    waiting_for_arrival.remove(u_id)
    if u_id in user_time_state:
      del user_time_state[u_id]

    await update.message.reply_text(
        f"ممنون {display_name}! ساعت حضور تقریبی شما ({arrival_time}) ثبت"
        " شد. منتظرتان هستیم! 🟢"
    )


# --- وب‌سرور سبک Flask برای زنده نگه داشتن ربات روی رندر ---
app = Flask(__name__)


@app.route("/")
def home():
  return "Mafia Bot is running 24/7!"


def run_web():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)


def main():
  # اجرای Flask در یک نخ (Thread) جداگانه در پس‌زمینه
  web_thread = threading.Thread(target=run_web)
  web_thread.daemon = True
  web_thread.start()

  application = ApplicationBuilder().token(TOKEN).build()

  application.add_handler(CommandHandler("start", start))
  application.add_handler(CommandHandler("admin", admin_panel))
  application.add_handler(CommandHandler("list", show_list))
  application.add_handler(CommandHandler("reset", reset_lists))
  application.add_handler(CommandHandler("broadcast", broadcast))
  application.add_handler(CommandHandler("sendmsg", custom_broadcast_command))
  application.add_handler(CommandHandler("thanks", send_thanks))

  application.add_handler(CommandHandler("settime", set_time))
  application.add_handler(CommandHandler("setday", set_day))
  application.add_handler(CommandHandler("setloc", set_loc))
  application.add_handler(CommandHandler("sethost", set_host))
  application.add_handler(CommandHandler("settext", set_text))

  application.add_handler(CommandHandler("setuser", set_user))
  application.add_handler(CommandHandler("add", add_player))
  application.add_handler(CommandHandler("remove", remove_player))
  application.add_handler(CommandHandler("del", remove_player))
  application.add_handler(CommandHandler("edit", edit_player))

  application.add_handler(CallbackQueryHandler(button_handler))
  application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

  print("ربات همراه با وب‌سرور Flask روشن شد...")
  application.run_polling()


if __name__ == "__main__":
  main()