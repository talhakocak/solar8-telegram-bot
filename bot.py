import os
import re
import html
import json
import hashlib
from datetime import time
from zoneinfo import ZoneInfo

import feedparser
from openai import OpenAI
from newspaper import Article
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

RSS_TR = "https://news.google.com/rss/search?q=%22güneş+enerjisi%22+OR+%22çatı+üstü+güneş%22+OR+%22GES%22+OR+%22solar+panel%22+OR+%22güneş+paneli%22+OR+%22fotovoltaik%22&hl=tr&gl=TR&ceid=TR:tr"
RSS_GLOBAL = "https://news.google.com/rss/search?q=%22solar+energy%22+OR+photovoltaic+OR+%22solar+panel%22+OR+%22solar+inverter%22+OR+%22solar+power%22&hl=en-US&gl=US&ceid=US:en"

SENT_EVENTS_FILE = "sent_events.json"

MAX_NEWS_PER_RUN = 5
MAX_TR_NEWS = 3
MAX_GLOBAL_NEWS = 2

RSS_LIMIT_TR = 10
RSS_LIMIT_GLOBAL = 10

SOLAR_KEYWORDS = [
    "güneş", "ges", "fotovoltaik", "solar", "pv",
    "panel", "inverter", "çatı üstü", "cati ustu",
    "photovoltaic", "solar energy", "solar panel",
    "solar power", "solar inverter"
]


def is_authorized(update: Update):
    return update.effective_user and update.effective_user.id == OWNER_ID


async def reject(update: Update):
    await update.message.reply_text("Bu bot özel kullanım içindir.")


def load_sent_events():
    if not os.path.exists(SENT_EVENTS_FILE):
        return set()

    with open(SENT_EVENTS_FILE, "r", encoding="utf-8") as file:
        return set(json.load(file))


def save_sent_events(sent_events):
    with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as file:
        json.dump(list(sent_events), file, ensure_ascii=False, indent=2)


def reset_memory():
    if os.path.exists(SENT_EVENTS_FILE):
        os.remove(SENT_EVENTS_FILE)


def clean_text(text):
    text = re.sub("<.*?>", "", text or "")
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return " ".join(text.split())


def normalize_for_key(text):
    text = clean_text(text).lower()

    replacements = {
        "ı": "i",
        "ğ": "g",
        "ü": "u",
        "ş": "s",
        "ö": "o",
        "ç": "c",
    }

    for tr_char, en_char in replacements.items():
        text = text.replace(tr_char, en_char)

    text = re.sub(r"[^a-z0-9\s]", " ", text)

    stopwords = [
        "ve", "ile", "icin", "bir", "bu", "kez", "son",
        "haber", "haberi", "aciklandi", "duyuruldu",
        "com", "www", "the", "and", "for", "with", "new"
    ]

    words = [
        word for word in text.split()
        if word not in stopwords and len(word) > 2
    ]

    normalized = "_".join(words[:12])

    if not normalized:
        normalized = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    return normalized


def is_solar_keyword_match(title):
    lowered = title.lower()
    return any(keyword in lowered for keyword in SOLAR_KEYWORDS)


def fetch_article_text(url):
    article = Article(url)
    article.download()
    article.parse()
    return article.text[:900]


def ai_analyze_news(title, article_text, region):
    prompt = f"""
Aşağıdaki haberin güneş enerjisi haberi olup olmadığını analiz et.

Bölge: {region}

Başlık:
{title}

Haber metni:
{article_text}

Sadece geçerli JSON döndür. Markdown kullanma.

JSON formatı:
{{
  "is_solar_related": true,
  "category": "Yatırım / Teknoloji / Depolama / Regülasyon / Genel / Araştırma",
  "summary": "Türkçe, tarafsız, en fazla 2 kısa cümle."
}}

Kurallar:
- Ana konu güneş enerjisi, GES, fotovoltaik, solar panel, güneş paneli, solar inverter, çatı GES veya solar elektrik üretimi ise true.
- Güneş enerjisi verimliliğini, hava kirliliğinin güneş üretimine etkisini veya bilimsel fotovoltaik araştırmaları anlatıyorsa true.
- Doğalgaz, kömür, nükleer, rüzgar veya sadece genel enerji politikası haberi ise false.
- Genel batarya haberi false olsun; ancak güneş enerjisiyle birlikte depolama anlatılıyorsa true olabilir.
- Haber metni kısa veya yetersizse başlığa göre karar ver.
"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": "Sen güneş enerjisi haberlerini filtreleyen ve kısa özetleyen tarafsız bir haber editörüsün."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
        temperature=0.1,
        max_tokens=180,
    )

    content = response.choices[0].message.content.strip()
    content = content.replace("```json", "").replace("```", "").strip()

    return json.loads(content), response.usage


def select_news(candidates):
    tr_news = [item for item in candidates if item["region"] == "Türkiye"]
    global_news = [item for item in candidates if item["region"] == "Küresel"]

    selected = []
    selected.extend(tr_news[:MAX_TR_NEWS])
    selected.extend(global_news[:MAX_GLOBAL_NEWS])

    if len(selected) < MAX_NEWS_PER_RUN:
        selected_keys = {item["dedupe_key"] for item in selected}
        remaining = [
            item for item in candidates
            if item["dedupe_key"] not in selected_keys
        ]
        selected.extend(remaining[:MAX_NEWS_PER_RUN - len(selected)])

    return selected[:MAX_NEWS_PER_RUN]


async def collect_and_send_news(send_func, silent_if_empty=False):
    sent_events = load_sent_events()
    candidates = []
    total_tokens = 0

    if not silent_if_empty:
        await send_func("☀️ Solar8 Günlük Enerji Bülteni hazırlanıyor...")

    sources = [
        ("Türkiye", RSS_TR, RSS_LIMIT_TR),
        ("Küresel", RSS_GLOBAL, RSS_LIMIT_GLOBAL),
    ]

    for region, rss_url, limit in sources:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:limit]:
            title = clean_text(entry.title)
            link = entry.link
            dedupe_key = normalize_for_key(title)

            if dedupe_key in sent_events:
                print(f"Geçildi tekrar başlık: {dedupe_key}")
                continue

            if not is_solar_keyword_match(title):
                print(f"Geçildi keyword yok: {title}")
                continue

            try:
                article_text = fetch_article_text(link)

                if len(article_text) < 200:
                    article_text = clean_text(getattr(entry, "summary", ""))

                if len(article_text) < 100:
                    article_text = title

                data, usage = ai_analyze_news(title, article_text, region)

                if usage:
                    total_tokens += usage.total_tokens

                if not data.get("is_solar_related", False):
                    print(f"Geçildi AI güneş odaklı değil: {title}")
                    continue

                candidates.append({
                    "region": region,
                    "title": title,
                    "link": link,
                    "category": data.get("category", "Genel"),
                    "dedupe_key": dedupe_key,
                    "summary": data.get("summary", "").strip(),
                    "tokens": usage.total_tokens if usage else 0,
                })

            except Exception as e:
                print(f"Hata: {title}")
                print(str(e))

    selected = select_news(candidates)

    if not selected:
        if not silent_if_empty:
            await send_func(
                "☀️ Solar8 Günlük Enerji Bülteni\n\n"
                "Bugün güneş enerjisi odaklı yeni bir haber bulunamadı."
            )
        return

    await send_func(
        f"☀️ Bugün {len(selected)} güneş enerjisi haberi bulundu.\n"
        f"📊 Yaklaşık AI kullanımı: {total_tokens} token"
    )

    for item in selected:
        text = (
            "☀️ Solar8 Enerji Haberleri\n\n"
            f"🌍 Bölge: {item['region']}\n"
            f"🏷️ Kategori: {html.escape(item['category'])}\n\n"
            f'📰 <a href="{item["link"]}">{html.escape(item["title"])}</a>\n\n'
            f"🧠 Özet:\n{html.escape(item['summary'])}\n\n"
            f'🔗 <a href="{item["link"]}">Haberi Oku</a>'
        )

        await send_func(text, parse_mode="HTML")

        sent_events.add(item["dedupe_key"])
        save_sent_events(sent_events)

        print("=" * 40)
        print(item["title"])
        print(f"Dedupe key: {item['dedupe_key']}")
        print(f"Tokens: {item['tokens']}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject(update)
        return

    await update.message.reply_text(
        "☀️ Solar8 Enerji Haber Botu aktif!\n\n"
        "/news → Sana özel manuel test gönderir\n"
        "/sendnow → Kanala şimdi bülten gönderir\n"
        "/test → 1 haberlik ucuz test\n"
        "/reset → Haber hafızasını sıfırlar\n\n"
        "Her sabah 08:00'de kanala otomatik bülten gönderir."
    )


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject(update)
        return

    async def send_func(text, parse_mode=None):
        await update.message.reply_text(text, parse_mode=parse_mode)

    await collect_and_send_news(send_func)


async def sendnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject(update)
        return

    await update.message.reply_text("Kanala bülten gönderimi başlatıldı.")

    async def send_func(text, parse_mode=None):
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode=parse_mode
        )

    await collect_and_send_news(send_func, silent_if_empty=False)


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject(update)
        return

    global MAX_NEWS_PER_RUN

    old_limit = MAX_NEWS_PER_RUN
    MAX_NEWS_PER_RUN = 1

    async def send_func(text, parse_mode=None):
        await update.message.reply_text(text, parse_mode=parse_mode)

    await collect_and_send_news(send_func)

    MAX_NEWS_PER_RUN = old_limit


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject(update)
        return

    reset_memory()
    await update.message.reply_text(
        "🧹 Haber hafızası sıfırlandı.\n\n"
        "Artık bot haberleri sıfırdan değerlendirecek."
    )


async def channeltest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject(update)
        return

    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="☀️ Solar8 kanal bağlantı testi başarılı."
    )

    await update.message.reply_text("Kanal test mesajı gönderildi.")


async def daily_news_job(context: ContextTypes.DEFAULT_TYPE):
    async def send_func(text, parse_mode=None):
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode=parse_mode
        )

    await collect_and_send_news(send_func, silent_if_empty=True)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("sendnow", sendnow))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("channeltest", channeltest))

    app.job_queue.run_daily(
        daily_news_job,
        time=time(hour=8, minute=0, tzinfo=ZoneInfo("Europe/Istanbul")),
        name="daily_solar8_news"
    )

    print("Bot çalışıyor...")
    print("Her sabah 08:00'de kanala otomatik haber gönderecek.")
    app.run_polling()


if __name__ == "__main__":
    main()