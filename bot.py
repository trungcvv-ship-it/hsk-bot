import asyncio
import json
import os
import random
import sys
from datetime import date, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

# Fix UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

from google import genai
import redis as redis_lib
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DATA_DIR = Path("data")
PROGRESS_FILE = DATA_DIR / "progress.json"
REDIS_URL = os.getenv("REDIS_URL")
REDIS_KEY = "hsk_progress"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PIANO_MINUTES = 30

_redis = redis_lib.from_url(REDIS_URL) if REDIS_URL else None
_ai = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SYSTEM_PROMPT = """Bạn là trợ lý học tập cá nhân, giao tiếp bằng tiếng Việt.

Người dùng đang:
- Học tiếng Trung theo lộ trình HSK1 → HSK3 (mục tiêu hoàn thành HSK3 vào tháng 12/2026)
- Tập piano mỗi ngày (mục tiêu 30 phút/ngày)

Bạn có thể giúp:
- Giải thích từ vựng tiếng Trung: pinyin, thanh điệu, nghĩa, ví dụ
- Giải thích ngữ pháp tiếng Trung đơn giản
- So sánh từ dễ nhầm lẫn
- Tư vấn phương pháp học HSK hiệu quả
- Động viên và theo dõi tiến độ học
- Tư vấn về tập piano, bài tập, kỹ thuật cơ bản
- Trả lời câu hỏi thông thường

Phong cách: thân thiện, ngắn gọn, dùng emoji phù hợp. Khi giải thích tiếng Trung luôn kèm pinyin và thanh điệu."""


def load_hsk_data():
    all_words = []
    for level in [1, 2, 3]:
        path = DATA_DIR / f"hsk{level}.json"
        with open(path, encoding="utf-8") as f:
            words = json.load(f)
        for w in words:
            w["level"] = level
        all_words.extend(words)
    return all_words


HSK_WORDS = load_hsk_data()
HSK_WORDS_BY_HANZI = {w["hanzi"]: w for w in HSK_WORDS}


_DEFAULT_PROGRESS = {
    "learned": [],
    "daily_words": [],
    "last_lesson_date": None,
    "streak": 0,
    "quiz_pending": [],
    "piano_today": False,
    "piano_last_date": None,
}


def load_progress():
    if _redis:
        raw = _redis.get(REDIS_KEY)
        return json.loads(raw) if raw else dict(_DEFAULT_PROGRESS)
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return dict(_DEFAULT_PROGRESS)


def save_progress(p):
    if _redis:
        _redis.set(REDIS_KEY, json.dumps(p, ensure_ascii=False))
        return
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def get_next_words(progress, count=10):
    learned = set(progress["learned"])
    remaining = [w for w in HSK_WORDS if w["hanzi"] not in learned]
    return remaining[:count]


def current_hsk_level(progress):
    learned = set(progress["learned"])
    hsk1 = [w for w in HSK_WORDS if w["level"] == 1]
    hsk1_learned = sum(1 for w in hsk1 if w["hanzi"] in learned)
    if hsk1_learned < len(hsk1):
        return 1, hsk1_learned, len(hsk1)
    hsk2 = [w for w in HSK_WORDS if w["level"] == 2]
    hsk2_learned = sum(1 for w in hsk2 if w["hanzi"] in learned)
    if hsk2_learned < len(hsk2):
        return 2, hsk2_learned, len(hsk2)
    hsk3 = [w for w in HSK_WORDS if w["level"] == 3]
    hsk3_learned = sum(1 for w in hsk3 if w["hanzi"] in learned)
    return 3, hsk3_learned, len(hsk3)


def format_word(word, index=None):
    prefix = f"*{index}.* " if index else ""
    return (
        f"{prefix}🈵 *{word['hanzi']}*  _{word['pinyin']}_\n"
        f"   📖 {word['meaning']}\n"
        f"   💬 {word.get('example', '')}"
    )


def progress_bar(done, total, length=12):
    if total == 0:
        return "░" * length
    filled = min(int(done / total * length), length)
    return "█" * filled + "░" * (length - filled)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 *Xin chào! Tôi là trợ lý học tập của bạn.*\n\n"
        f"📌 Chat ID: `{chat_id}`\n"
        f"_(Copy ID này, dán vào file .env rồi khởi động lại bot)_\n\n"
        f"🎯 *Lộ trình HSK đến 12/2026:*\n"
        f"• HSK1 (150 từ) → Tháng 6/2026\n"
        f"• HSK2 (300 từ) → Tháng 9/2026\n"
        f"• HSK3 (600 từ) → Tháng 12/2026\n\n"
        f"⏰ *Lịch nhắc tự động:*\n"
        f"• 7:00 🌅 Bài học buổi sáng\n"
        f"• 12:00 ☀️ Ôn tập buổi trưa\n"
        f"• 19:00 🎹 Nhắc tập piano\n"
        f"• 21:00 🌙 Tổng kết ngày\n\n"
        f"Gõ /hoc để bắt đầu! 💪",
        parse_mode="Markdown",
    )


async def cmd_hoc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    progress = load_progress()
    today = str(date.today())
    already_learned_today = progress["last_lesson_date"] == today

    if already_learned_today and progress["daily_words"]:
        words = [HSK_WORDS_BY_HANZI[h] for h in progress["daily_words"] if h in HSK_WORDS_BY_HANZI]
        await update.message.reply_text(
            "📚 *Bạn đã học hôm nay rồi!* Đây là từ ôn lại:\n",
            parse_mode="Markdown",
        )
    else:
        words = get_next_words(progress, 10)
        if not words:
            await update.message.reply_text(
                "🎉 *Xuất sắc!* Bạn đã học hết toàn bộ từ vựng HSK1→HSK3!\n"
                "Hãy tiếp tục ôn tập với /ontap",
                parse_mode="Markdown",
            )
            return

        yesterday = str(date.fromordinal(date.today().toordinal() - 1))
        if progress["last_lesson_date"] == yesterday:
            progress["streak"] += 1
        elif progress["last_lesson_date"] != today:
            progress["streak"] = 1

        progress["last_lesson_date"] = today
        progress["daily_words"] = [w["hanzi"] for w in words]
        progress["quiz_pending"] = [w["hanzi"] for w in words]
        save_progress(progress)

    level, done, total = current_hsk_level(progress)
    total_learned = len(progress["learned"])

    header = (
        f"📅 *Bài học — {today}*\n"
        f"📊 HSK{level}  [{progress_bar(done, total)}]  {done}/{total}\n"
        f"🔥 Streak: *{progress['streak']}* ngày liên tiếp\n"
        f"{'─' * 28}\n"
    )
    await update.message.reply_text(header, parse_mode="Markdown")
    await asyncio.sleep(0.3)

    msg_parts = []
    for i, word in enumerate(words, 1):
        msg_parts.append(format_word(word, i))

    await update.message.reply_text("\n\n".join(msg_parts), parse_mode="Markdown")
    await update.message.reply_text(
        f"✅ *Xong!* Học {len(words)} từ hôm nay.\n"
        f"Dùng /ontap để kiểm tra ngay, hoặc đợi nhắc lúc 12h.",
        parse_mode="Markdown",
    )


async def _send_quiz(send_fn, progress):
    if not progress["quiz_pending"]:
        await send_fn("🎉 Bạn đã ôn hết từ hôm nay! Tuyệt vời! Gõ /hoc để học thêm.")
        return

    word_char = random.choice(progress["quiz_pending"])
    word = HSK_WORDS_BY_HANZI.get(word_char)
    if not word:
        return

    wrong_pool = [w for w in HSK_WORDS if w["hanzi"] != word_char]
    wrong_words = random.sample(wrong_pool, min(3, len(wrong_pool)))
    options = [word["meaning"]] + [w["meaning"] for w in wrong_words]
    random.shuffle(options)

    keyboard = [[InlineKeyboardButton(opt, callback_data=f"quiz:{word_char}:{i}")] for i, opt in enumerate(options)]
    # Store correct answer index in callback_data won't work cleanly; store meaning instead (truncated)
    keyboard = [
        [InlineKeyboardButton(opt, callback_data=f"quiz:{word_char}:{opt[:40]}")]
        for opt in options
    ]
    markup = InlineKeyboardMarkup(keyboard)
    remaining = len(progress["quiz_pending"])
    await send_fn(
        f"❓ *{word['hanzi']}*  _{word['pinyin']}_\nNghĩa là gì?  (còn {remaining} từ)",
        parse_mode="Markdown",
        reply_markup=markup,
    )


async def cmd_ontap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    progress = load_progress()
    if not progress["daily_words"]:
        await update.message.reply_text("Chưa có bài học nào! Dùng /hoc trước nhé.")
        return
    await _send_quiz(update.message.reply_text, progress)


async def quiz_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, hanzi, chosen = parts

    word = HSK_WORDS_BY_HANZI.get(hanzi)
    if not word:
        return

    progress = load_progress()
    correct = word["meaning"]
    is_correct = chosen.strip() == correct.strip() or correct.startswith(chosen.strip())

    from telegram.error import BadRequest as TGBadRequest

    if is_correct:
        if hanzi in progress["quiz_pending"]:
            progress["quiz_pending"].remove(hanzi)
        if hanzi not in progress["learned"]:
            progress["learned"].append(hanzi)
        save_progress(progress)

        remaining = len(progress["quiz_pending"])
        try:
            await query.edit_message_text(
                f"✅ *Đúng!*  {hanzi} = {correct}\n"
                f"{'🎉 Xong tất cả! /tiendo để xem tiến độ' if remaining == 0 else f'Còn {remaining} từ...'}",
                parse_mode="Markdown",
            )
        except TGBadRequest:
            pass
        if remaining > 0:
            await asyncio.sleep(1)
            await _send_quiz(query.message.reply_text, progress)
    else:
        try:
            await query.edit_message_text(
                f"❌ *Sai!*  _{hanzi}_ = *{correct}*\n_Gõ /ontap để thử lại_",
                parse_mode="Markdown",
            )
        except TGBadRequest:
            pass


async def cmd_tiendo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    progress = load_progress()
    learned = set(progress["learned"])

    hsk1 = [w for w in HSK_WORDS if w["level"] == 1]
    hsk2 = [w for w in HSK_WORDS if w["level"] == 2]
    hsk3 = [w for w in HSK_WORDS if w["level"] == 3]

    h1 = sum(1 for w in hsk1 if w["hanzi"] in learned)
    h2 = sum(1 for w in hsk2 if w["hanzi"] in learned)
    h3 = sum(1 for w in hsk3 if w["hanzi"] in learned)

    total = len(learned)
    target_2026 = 600

    days_left = (date(2026, 12, 31) - date.today()).days
    words_per_day_needed = max(0, (target_2026 - total) / max(days_left, 1))

    msg = (
        f"📊 *Tiến độ học tập*\n"
        f"{'─' * 28}\n"
        f"🔥 Streak: *{progress['streak']}* ngày liên tiếp\n"
        f"📚 Tổng từ đã học: *{total}/{target_2026}*\n\n"
        f"*HSK1* [{progress_bar(h1, 150)}] {h1}/150\n"
        f"*HSK2* [{progress_bar(h2, 150)}] {h2}/150\n"
        f"*HSK3* [{progress_bar(h3, 300)}] {h3}/300\n\n"
        f"🎯 Mục tiêu: HSK3 vào *12/2026*\n"
        f"📅 Còn {days_left} ngày\n"
        f"⚡ Cần học ~{words_per_day_needed:.1f} từ/ngày\n"
        f"📖 Học gần nhất: {progress['last_lesson_date'] or 'Chưa học'}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_piano(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    progress = load_progress()
    today = str(date.today())
    if progress.get("piano_last_date") == today:
        await update.message.reply_text(
            f"🎹 Bạn đã tập piano hôm nay rồi! Tuyệt vời! 🌟\n"
            f"Giữ vững thói quen nhé!",
        )
        return
    progress["piano_today"] = True
    progress["piano_last_date"] = today
    save_progress(progress)
    await update.message.reply_text(
        f"🎹 *Ghi nhận tập piano hôm nay!* ✅\n"
        f"Mục tiêu: {PIANO_MINUTES} phút mỗi ngày\n"
        f"Cố gắng lên! 💪",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Danh sách lệnh:*\n\n"
        "/hoc — Học 10 từ mới hôm nay\n"
        "/ontap — Ôn tập (quiz trắc nghiệm)\n"
        "/tiendo — Xem tiến độ HSK\n"
        "/piano — Đánh dấu đã tập piano\n"
        "/start — Hiện thông tin & chat ID\n"
        "/help — Menu này\n\n"
        "⏰ *Nhắc tự động (GMT+7):*\n"
        "7:00 🌅 Bài học sáng\n"
        "12:00 ☀️ Ôn tập trưa\n"
        "19:00 🎹 Piano\n"
        "21:00 🌙 Tổng kết ngày",
        parse_mode="Markdown",
    )


# ── Scheduled jobs (PTB JobQueue) ───────────────────────────────
# Callbacks nhận (context) thay vì (app)

async def job_morning_lesson(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    bot = context.bot
    progress = load_progress()
    today = str(date.today())
    words = get_next_words(progress, 10)
    if not words:
        await bot.send_message(CHAT_ID, "🎉 Bạn đã học hết từ vựng HSK1-3! Hãy ôn tập với /ontap")
        return

    yesterday = str(date.fromordinal(date.today().toordinal() - 1))
    if progress["last_lesson_date"] == yesterday:
        progress["streak"] += 1
    elif progress["last_lesson_date"] != today:
        progress["streak"] = 1

    progress["last_lesson_date"] = today
    progress["daily_words"] = [w["hanzi"] for w in words]
    progress["quiz_pending"] = [w["hanzi"] for w in words]
    progress["piano_today"] = False
    save_progress(progress)

    level, done, total = current_hsk_level(progress)
    header = (
        f"🌅 *Chào buổi sáng! — {today}*\n"
        f"📊 HSK{level}  [{progress_bar(done, total)}]  {done}/{total}\n"
        f"🔥 Streak: *{progress['streak']}* ngày\n"
        f"{'─' * 28}\n"
    )
    await bot.send_message(CHAT_ID, header, parse_mode="Markdown")
    await asyncio.sleep(0.5)
    msg_parts = [format_word(w, i) for i, w in enumerate(words, 1)]
    await bot.send_message(CHAT_ID, "\n\n".join(msg_parts), parse_mode="Markdown")
    await bot.send_message(CHAT_ID, "✅ Xong bài sáng! Dùng /ontap để quiz ngay.", parse_mode="Markdown")


async def job_noon_review(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    progress = load_progress()
    if not progress["quiz_pending"]:
        await context.bot.send_message(
            CHAT_ID, "☀️ *Ôn tập buổi trưa!*\nBạn đã ôn hết rồi! Dùng /hoc để học thêm.", parse_mode="Markdown"
        )
        return
    await context.bot.send_message(
        CHAT_ID,
        f"☀️ *Đến giờ ôn tập!* Còn {len(progress['quiz_pending'])} từ chưa ôn.\nGõ /ontap để bắt đầu.",
        parse_mode="Markdown",
    )


async def job_piano_reminder(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    progress = load_progress()
    if progress.get("piano_last_date") == str(date.today()):
        return
    await context.bot.send_message(
        CHAT_ID,
        f"🎹 *Nhắc tập piano!*\nMục tiêu {PIANO_MINUTES} phút hôm nay.\nTập xong gõ /piano để ghi nhận! 💪",
        parse_mode="Markdown",
    )


async def job_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    progress = load_progress()
    today = str(date.today())
    daily_total = len(progress["daily_words"])
    ontap_done = daily_total - len(progress["quiz_pending"])
    piano_done = progress.get("piano_last_date") == today
    total_learned = len(progress["learned"])
    level, done, total = current_hsk_level(progress)
    piano_status = "✅ Đã tập" if piano_done else "❌ Chưa tập"

    await context.bot.send_message(
        CHAT_ID,
        f"🌙 *Tổng kết ngày {today}*\n"
        f"{'─' * 28}\n"
        f"📚 Từ học hôm nay: {daily_total}\n"
        f"✅ Từ đã ôn: {ontap_done}/{daily_total}\n"
        f"🎹 Piano: {piano_status}\n"
        f"📖 Tổng tích lũy: {total_learned} từ\n"
        f"🔥 Streak: {progress['streak']} ngày\n"
        f"📊 HSK{level}: {done}/{total}\n\n"
        f"Ngủ ngon! Hẹn gặp 7h sáng mai. 😴",
        parse_mode="Markdown",
    )


# ── AI Chat handler ─────────────────────────────────────────────

async def cmd_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _ai:
        await update.message.reply_text("Chưa cấu hình GEMINI_API_KEY.")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    await update.message.chat.send_action("typing")

    progress = load_progress()
    level, done, _ = current_hsk_level(progress)

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"[Ngữ cảnh người dùng: đang học HSK{level}, "
        f"đã học {len(progress['learned'])} từ, streak {progress['streak']} ngày]\n\n"
        f"{user_text}"
    )

    try:
        response = _ai.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        err = str(e)
        if "429" in err or "RESOURCE_EXHAUSTED" in err:
            await update.message.reply_text("Gemini đang bận, thử lại sau 1 phút nhé!")
        else:
            await update.message.reply_text(f"Lỗi AI: {err[:200]}")


# ── Main ─────────────────────────────────────────────────────────

def main():
    # Python 3.14+ does not create a default event loop — must create one explicitly
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if not TOKEN:
        print("Chua co TELEGRAM_TOKEN trong file .env!")
        return
    if not CHAT_ID:
        print("TELEGRAM_CHAT_ID chua duoc set.")
        print("Chay bot, go /start de lay Chat ID, roi dien vao .env va khoi dong lai.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("hoc", cmd_hoc))
    app.add_handler(CommandHandler("ontap", cmd_ontap))
    app.add_handler(CommandHandler("tiendo", cmd_tiendo))
    app.add_handler(CommandHandler("piano", cmd_piano))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(quiz_callback, pattern=r"^quiz:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_chat))

    jq = app.job_queue
    jq.run_daily(job_morning_lesson, time=dtime(7, 0, tzinfo=TZ))
    jq.run_daily(job_noon_review,    time=dtime(12, 0, tzinfo=TZ))
    jq.run_daily(job_piano_reminder, time=dtime(19, 0, tzinfo=TZ))
    jq.run_daily(job_daily_summary,  time=dtime(21, 0, tzinfo=TZ))

    print("Bot dang chay... (Ctrl+C de dung)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
