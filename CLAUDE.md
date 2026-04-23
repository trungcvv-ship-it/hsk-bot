# Trợ Lý Học Tập — Telegram Bot

Bot Telegram hỗ trợ học tiếng Trung (lộ trình HSK1→HSK3) và nhắc tập piano.

## Mục tiêu
- Hoàn thành HSK1 vào tháng 6/2026
- Hoàn thành HSK2 vào tháng 9/2026
- Hoàn thành HSK3 vào tháng 12/2026

## Cấu trúc project

```
bot.py                  # Bot chính + scheduler
data/
  hsk1.json             # 150 từ HSK1
  hsk2.json             # 150 từ HSK2
  hsk3.json             # 300 từ HSK3
  progress.json         # Tiến độ người dùng (tự tạo)
.env                    # TELEGRAM_TOKEN + TELEGRAM_CHAT_ID
requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

1. Copy token từ BotFather vào `.env`
2. Chạy bot: `python bot.py`
3. Mở Telegram, gõ `/start` → copy Chat ID
4. Dán Chat ID vào `.env` → khởi động lại bot

## Lệnh bot

| Lệnh | Chức năng |
|------|-----------|
| `/hoc` | Học 10 từ mới hôm nay |
| `/ontap` | Quiz ôn tập từ đã học |
| `/tiendo` | Xem tiến độ HSK + streak |
| `/piano` | Ghi nhận đã tập piano hôm nay |
| `/help` | Danh sách lệnh |

## Lịch nhắc tự động (GMT+7)

| Giờ | Nội dung |
|-----|----------|
| 7:00 | Bài học buổi sáng (10 từ mới) |
| 12:00 | Nhắc ôn tập buổi trưa |
| 19:00 | Nhắc tập piano |
| 21:00 | Tổng kết ngày |

## Cấu trúc progress.json

```json
{
  "learned": ["爱", "八", ...],       // Từ đã học và ôn xong
  "daily_words": ["爱", "八", ...],   // Từ của ngày hôm nay
  "quiz_pending": ["爱", ...],        // Từ chưa ôn hôm nay
  "last_lesson_date": "2026-04-23",
  "streak": 5,
  "piano_today": false,
  "piano_last_date": "2026-04-22"
}
```

## Thay đổi thường gặp

- **Số từ mỗi ngày**: đổi `count=10` trong `get_next_words()` ở `bot.py`
- **Thời gian nhắc**: sửa `CronTrigger(hour=X)` trong `main()`
- **Phút tập piano**: sửa `PIANO_MINUTES = 30` ở đầu `bot.py`

## Chạy như service (Windows)

```bash
# Chạy nền bằng pythonw (không mở cửa sổ terminal)
pythonw bot.py
```

Hoặc tạo Task Scheduler để tự khởi động khi đăng nhập Windows.
