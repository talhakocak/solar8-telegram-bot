import os
import json
import asyncio
import sys
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from openai import OpenAI
from dotenv import load_dotenv
from telegram import Bot
from telegram.request import HTTPXRequest

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_WEEKLY_CHAT_ID") or os.getenv("CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN bulunamadı.")
if not CHAT_ID:
    raise ValueError("CHAT_ID bulunamadı.")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY bulunamadı.")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

request = HTTPXRequest(
    connection_pool_size=8,
    connect_timeout=60,
    read_timeout=60,
    write_timeout=60,
    pool_timeout=60,
)

bot = Bot(token=BOT_TOKEN, request=request)

NEWS_ARCHIVE_FILE = "news_archive.json"

CATEGORY_EMOJI = {
    "Yatırım": "💰",
    "Teknoloji": "🔬",
    "Depolama": "🔋",
    "Regülasyon": "📜",
    "Araştırma": "🧪",
    "Genel": "📌",
}

REGION_EMOJI = {
    "Türkiye": "🇹🇷",
    "Küresel": "🌍",
}


def load_news_archive():
    if not os.path.exists(NEWS_ARCHIVE_FILE):
        return []
    try:
        with open(NEWS_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def get_week_range(reference_date=None):
    """
    Referans tarihe göre o haftanın Pazartesi–Pazar aralığını döndürür.
    Varsayılan: bu haftanın Pazartesi–Pazar'ı.
    """
    if reference_date is None:
        reference_date = datetime.now()

    weekday = reference_date.weekday()
    monday = reference_date - timedelta(days=weekday)
    sunday = monday + timedelta(days=6)

    return (
        monday.replace(hour=0, minute=0, second=0, microsecond=0),
        sunday.replace(hour=23, minute=59, second=59, microsecond=999999),
    )


def filter_weekly_news(archive, week_start, week_end):
    weekly = []
    for item in archive:
        try:
            sent_at = datetime.fromisoformat(item["sent_at"])
        except Exception:
            continue
        if week_start <= sent_at <= week_end:
            weekly.append(item)
    return weekly


def compute_statistics(items):
    total = len(items)
    if total == 0:
        return {}

    category_counts = Counter(item.get("category", "Genel") for item in items)

    region_counts = Counter(item.get("region", "Küresel") for item in items)

    scores = [item.get("importance_score", 5) for item in items]
    avg_score = round(sum(scores) / len(scores), 1)

    top_items = sorted(items, key=lambda x: x.get("importance_score", 0), reverse=True)[:5]

    daily_counts = Counter(item.get("sent_date", "?") for item in items)

    most_active_day = max(daily_counts, key=daily_counts.get) if daily_counts else None

    return {
        "total": total,
        "category_counts": dict(category_counts.most_common()),
        "region_counts": dict(region_counts),
        "avg_score": avg_score,
        "top_items": top_items,
        "daily_counts": dict(sorted(daily_counts.items())),
        "most_active_day": most_active_day,
    }


def ai_generate_weekly_theme(items_summary):
    """
    Haftanın öne çıkan temasını ve kısa yorumunu AI ile üretir.
    """
    summaries_text = "\n".join([
        f"- [{item.get('category','?')}] {item.get('title','')} | {item.get('summary','')}"
        for item in items_summary[:20]
    ])

    prompt = f"""
Aşağıda bu haftaya ait güneş enerjisi haberlerinin listesi var.
Bu haberlere bakarak haftanın öne çıkan 1-2 ana temasını ve kısa bir sektörel yorum yaz.

Haberler:
{summaries_text}

Sadece şu JSON formatında cevap ver:
{{
  "tema": "Haftanın öne çıkan teması tek cümleyle",
  "yorum": "Sektör açısından 2-3 cümlelik kısa, somut yorum. Genel ve boş ifadelerden kaçın."
}}
"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": "Sen güneş enerjisi sektörünü takip eden, haftalık trendleri analiz eden bir sektör analistisin. Sadece ham JSON döndürürsün."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
        temperature=0.3,
        max_tokens=300,
        # 🚀 İŞTE ARIZA ÇIKMASINI ENGELLEYECEK KRAL PARAMETRE:
        response_format={ 'type': 'json_object' } 
    )

    content = response.choices[0].message.content.strip()
    content = content.replace("```json", "").replace("```", "").strip()

    return json.loads(content)


def format_date_tr(date_str):
    """2026-05-23 → 23.05.2026"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%d.%m.%Y")
    except Exception:
        return date_str


def format_weekday_tr(date_str):
    """2026-05-23 → Cuma"""
    days_tr = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return days_tr[d.weekday()]
    except Exception:
        return date_str


def build_report_messages(stats, ai_result, week_start, week_end):
    """
    Telegram'a gönderilecek haftalık bülten mesajlarını oluşturur.
    Uzun linkler HTML parse moduna uygun olarak başlığa gömülmüştür.
    """
    import html # Eğer en üstte yoksa garanti olsun diye
    week_label = f"{week_start.strftime('%d.%m')} – {week_end.strftime('%d.%m.%Y')}"
    messages = []

    tr_count = stats["region_counts"].get("Türkiye", 0)
    global_count = stats["region_counts"].get("Küresel", 0)

    cat_lines = []
    total = stats["total"]
    for cat, count in stats["category_counts"].items():
        emoji = CATEGORY_EMOJI.get(cat, "📌")
        pct = round(count / total * 100)
        cat_lines.append(f"  {emoji} {cat}: {count} haber (%{pct})")

    most_active = stats.get("most_active_day")
    most_active_str = ""
    if most_active:
        most_active_str = (
            f"\n📅 En yoğun gün: {format_weekday_tr(most_active)} "
            f"({format_date_tr(most_active)}, "
            f"{stats['daily_counts'][most_active]} haber)"
        )

    msg1 = (
        f"☀️ <b>Solar8 Haftalık Enerji Bülteni</b>\n"
        f"📆 {week_label}\n"
        f"{'─' * 32}\n\n"
        f"📊 <b>Haftalık Özet</b>\n\n"
        f"🔢 Toplam gönderilen haber: {total}\n"
        f"🇹🇷 Türkiye: {tr_count}  |  🌍 Küresel: {global_count}\n"
        f"⭐ Haftalık ortalama önem puanı: {stats['avg_score']}/10\n"
        f"{most_active_str}\n\n"
        f"📂 <b>Kategori Dağılımı:</b>\n"
        + "\n".join(cat_lines)
    )
    messages.append(msg1)

    top_lines = []
    for i, item in enumerate(stats["top_items"], 1):
        region_emoji = REGION_EMOJI.get(item.get("region", "Küresel"), "🌍")
        score = item.get("importance_score", "?")
        title = html.escape(item.get("title", ""))
        summary = html.escape(item.get("summary", ""))
        link = item.get("link", "")
        sent_date = format_date_tr(item.get("sent_date", ""))

        top_lines.append(
            f"{i}. {region_emoji} [{score}/10] <a href='{link}'><b>{title}</b></a>\n"
            f"   📅 {sent_date}\n"
            f"   {summary}"
        )

    msg2 = (
        f"🏆 <b>Haftanın En Önemli 5 Haberi</b>\n"
        f"{'─' * 32}\n\n"
        + "\n\n".join(top_lines)
    )
    messages.append(msg2)

    tema = html.escape(ai_result.get("tema", ""))
    yorum = html.escape(ai_result.get("yorum", ""))

    msg3 = (
        f"🔍 <b>Haftanın Teması</b>\n"
        f"{'─' * 32}\n\n"
        f"💡 {tema}\n\n"
        f"📝 <b>Sektör Yorumu:</b>\n{yorum}\n\n"
        f"{'─' * 32}\n"
        f"Solar8 · Haftalık Güneş Enerjisi İstihbaratı"
    )
    messages.append(msg3)

    return messages


async def send_message(text):
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            print(f"Telegram gönderim hatası. Deneme {attempt + 1}/3: {e}")
            await asyncio.sleep(5)
    print("Telegram mesajı 3 denemede gönderilemedi.")
    return False


async def main(force=False, week_offset=0):
    """
    force=True → gün kontrolü yapmadan çalışır (test için)
    week_offset → kaç hafta geriye gidileceği (0=bu hafta, 1=geçen hafta)
    """
    now = datetime.now()

    if not force and now.weekday() != 6:
        days_tr = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
        print(f"Bugün {days_tr[now.weekday()]}. Haftalık rapor sadece Pazar günü çalışır.")
        print("Test için: python weekly_report.py force")
        return

    reference = now - timedelta(weeks=week_offset)
    week_start, week_end = get_week_range(reference)

    print(f"Hafta aralığı: {week_start.strftime('%d.%m.%Y')} – {week_end.strftime('%d.%m.%Y')}")

    archive = load_news_archive()
    weekly_items = filter_weekly_news(archive, week_start, week_end)

    if not weekly_items:
        print("Bu haftaya ait arşivde haber bulunamadı.")
        await send_message(
            f"☀️ Solar8 Haftalık Bülten | "
            f"{week_start.strftime('%d.%m')} – {week_end.strftime('%d.%m.%Y')}\n\n"
            "Bu haftaya ait arşivde haber bulunamadı."
        )
        return

    print(f"Bu hafta {len(weekly_items)} haber bulundu.")

    stats = compute_statistics(weekly_items)

    print("AI tema analizi yapılıyor...")
    try:
        ai_result = ai_generate_weekly_theme(weekly_items)
    except Exception as e:
        print(f"AI tema hatası: {e}")
        ai_result = {
            "tema": "Bu hafta güneş enerjisi sektöründe çeşitli gelişmeler yaşandı.",
            "yorum": "Haftalık haberler analiz edildi."
        }

    messages = build_report_messages(stats, ai_result, week_start, week_end)

    print(f"{len(messages)} mesaj gönderiliyor...")
    for i, msg in enumerate(messages, 1):
        ok = await send_message(msg)
        if ok:
            print(f"Mesaj {i}/{len(messages)} gönderildi.")
        else:
            print(f"Mesaj {i}/{len(messages)} gönderilemedi.")
        await asyncio.sleep(1)

    print("Haftalık rapor tamamlandı.")


if __name__ == "__main__":
    force = "force" in sys.argv
    week_offset = 0
    if "gecen" in sys.argv:
        week_offset = 1

    asyncio.run(main(force=force, week_offset=week_offset))
