import os
import re
import html
import json
import hashlib
import asyncio
import sys
from datetime import datetime, timezone, timedelta

import feedparser
from openai import OpenAI
from newspaper import Article
from dotenv import load_dotenv
from telegram import Bot
from telegram.request import HTTPXRequest

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
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

bot = Bot(
    token=BOT_TOKEN,
    request=request
)

RSS_TR = "https://news.google.com/rss/search?q=%22güneş+enerjisi%22+OR+%22çatı+üstü+güneş%22+OR+%22GES%22+OR+%22solar+panel%22+OR+%22güneş+paneli%22+OR+%22fotovoltaik%22&hl=tr&gl=TR&ceid=TR:tr"
RSS_GLOBAL = "https://news.google.com/rss/search?q=%22solar+energy%22+OR+photovoltaic+OR+%22solar+panel%22+OR+%22solar+inverter%22+OR+%22solar+power%22&hl=en-US&gl=US&ceid=US:en"

SENT_EVENTS_FILE = "sent_events.json"
NEWS_ARCHIVE_FILE = "news_archive.json"

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


def is_recent_entry(entry, max_days=30):
    if not hasattr(entry, "published_parsed") or entry.published_parsed is None:
        return True

    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)

    return published >= cutoff


def load_sent_events():
    if not os.path.exists(SENT_EVENTS_FILE):
        return set()

    try:
        with open(SENT_EVENTS_FILE, "r", encoding="utf-8") as file:
            return set(json.load(file))
    except Exception:
        return set()


def save_sent_events(sent_events):
    with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as file:
        json.dump(sorted(list(sent_events)), file, ensure_ascii=False, indent=2)


def load_news_archive():
    if not os.path.exists(NEWS_ARCHIVE_FILE):
        return []

    try:
        with open(NEWS_ARCHIVE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_news_archive(news_archive):
    with open(NEWS_ARCHIVE_FILE, "w", encoding="utf-8") as file:
        json.dump(news_archive, file, ensure_ascii=False, indent=2)


def archive_sent_news(item):
    news_archive = load_news_archive()
    dedupe_key = item["dedupe_key"]
    sent_at = datetime.now()

    news_archive = [
        archived_item for archived_item in news_archive
        if archived_item.get("dedupe_key") != dedupe_key
    ]

    news_archive.append({
        "sent_at": sent_at.isoformat(timespec="seconds"),
        "sent_date": sent_at.strftime("%Y-%m-%d"),
        "title": item["title"],
        "link": item["link"],
        "region": item["region"],
        "category": item["category"],
        "importance_score": item["importance_score"],
        "importance_reason": item["importance_reason"],
        "summary": item["summary"],
        "canonical_topic": item["canonical_topic"],
        "dedupe_key": dedupe_key,
    })

    save_news_archive(news_archive)


def reset_memory():
    with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as file:
        json.dump([], file, ensure_ascii=False, indent=2)

    print("Hafıza sıfırlandı.")


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


def normalize_importance_score(value):
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 5

    return max(1, min(score, 10))


def ai_analyze_news(title, article_text, rss_region, previous_topics, current_topics):
    prompt = f"""
Aşağıdaki içeriği Solar8 günlük güneş enerjisi bülteni için haber editörü gibi analiz et.

Kaynak RSS bölgesi:
{rss_region}

Başlık:
{title}

Haber metni:
{article_text}

Daha önce gönderilen haber konuları:
{json.dumps(previous_topics, ensure_ascii=False, indent=2)}

Bu çalıştırmada zaten aday olarak seçilmiş haber konuları:
{json.dumps(current_topics, ensure_ascii=False, indent=2)}

Not: Bu listelerde canonical_topic, normalleştirilmiş dedupe key veya haber başlığı bulunabilir.

Sadece geçerli JSON döndür. Markdown kullanma.

JSON formatı:
{{
  "is_solar_related": true,
  "is_duplicate": false,
  "detected_region": "Türkiye / Küresel",
  "canonical_topic": "aynı olayı anlatan farklı haberlerde aynı kalacak kısa konu adı",
  "category": "Yatırım / Teknoloji / Depolama / Regülasyon / Genel / Araştırma",
  "importance_score": 8,
  "importance_reason": "Haberin sektör için neden önemli olduğunu açıklayan tek kısa cümle.",
  "summary": "Türkçe, somut bilgiler içeren, sayısal verileri koruyan, en fazla 3 kısa cümle."
}}

Karar kuralları:

- Ana konu güneş enerjisi, GES, fotovoltaik, güneş paneli, solar inverter, çatı GES veya güneşten elektrik üretimi değilse is_solar_related false.
- Haber yeni ve paylaşmaya değer bir gelişme içermiyorsa is_solar_related false.
- Genel bilgi sayfası, rehber, teknik standart dokümanı, strateji/hedef sayfası, evergreen kurum sayfası veya haber niteliği taşımayan kaynak sayfası ise false.
- "2030 goals", "cost benchmarks", "reliability", "hardening", "basics" gibi bilgi/dokümantasyon içerikleri genellikle false.
- Rüzgar, doğalgaz, kömür, nükleer veya genel enerji politikası ana konuysa false.
- Genel batarya haberi false; güneş enerjisiyle doğrudan ilişkili depolama haberi true olabilir.

Paylaşmaya değer içerikler:

- Yeni GES yatırımı
- Şirket yatırımı veya fabrika açılışı
- Yeni teknoloji, ürün veya panel geliştirmesi
- Bilimsel araştırma sonucu
- Regülasyon değişikliği
- Pazar/veri gelişmesi
- Rekor üretim veya önemli istatistik
- Büyük ölçekli proje duyurusu
- Kapasite, üretim, tasarruf veya yatırım miktarı içeren somut gelişmeler

Tekrar kontrolü:

- Daha önce gönderilen haber konularıyla aynı olayı anlatıyorsa is_duplicate true.
- Bu çalıştırmada zaten aday olarak seçilmiş haber konularıyla aynı olayı anlatıyorsa is_duplicate true.
- Aynı haber farklı kaynak, farklı başlık veya küçük kelime farkıyla gelmişse is_duplicate true.
- Aynı şirketin aynı GES yatırımı, aynı projesi veya aynı tesis haberi farklı kaynaklarda geçiyorsa is_duplicate true.
- Örnek: "Selva Gıda GES yatırımı" ile "Selva Gıda yeni GES tesisiyle elektrik maliyetini azaltacak" aynı olaydır, ikinci gelen duplicate olmalı.
- Farklı şirket, farklı proje, farklı ülke, farklı tesis veya farklı yatırım ise is_duplicate false.
- Benzer kategoriye ait olması duplicate sayılmaz; aynı olay olması gerekir.

canonical_topic kuralları:

- canonical_topic aynı olayı anlatan farklı haber kaynaklarında aynı veya çok benzer olmalı.
- Çok genel yazma. "Türkiye GES yatırımı", "solar teknoloji haberi", "enerji yatırımı" gibi ifadeler kullanma.
- Şirket, proje, teknoloji, şehir, ülke veya ayırt edici ana unsur korunmalı.
- Kaynak adını canonical_topic içine alma.
- Küçük başlık farklarını ve gereksiz kelimeleri canonical_topic içine alma.

Bölge kuralları:

- Haber Türkiye ile ilgiliyse detected_region "Türkiye" olsun.
- Diğer ülkeler, küresel pazar, ABD, Çin, Hindistan, Avrupa, Irak, Kürdistan Bölgesi vb. için detected_region "Küresel" olsun.
- Haberin Türkçe yazılmış olması tek başına Türkiye haberi olduğu anlamına gelmez.

Önem derecesi kuralları:

- importance_score 1 ile 10 arasında tam sayı olmalı.
- Ortalama haber puanı 5-6 civarında olmalı.
- 9-10 çok nadir kullanılmalı; sadece gerçekten büyük ölçekli, pazarı etkileyen veya stratejik önemdeki haberlerde ver.
- Büyük haber yoksa yüksek puan verme.
- 9-10: Büyük ölçekli GES yatırımı, ciddi kapasite artışı, önemli regülasyon değişikliği, büyük şirket/fabrika hamlesi, çığır açıcı teknoloji veya pazarı etkileyen somut veri.
- 7-8: Yeni proje, ölçülebilir kapasite/üretim/tasarruf bilgisi, şirket yatırımı, yerel ölçekte güçlü gelişme veya dikkat çekici ürün/teknoloji haberi.
- 5-6: Sektörle ilgili ama etkisi sınırlı haber, küçük ölçekli proje, etkinlik, niyet/hedef açıklaması veya detayları eksik gelişme.
- 3-4: Solar bağlantısı zayıf, somut verisi az, tekrar niteliğine yakın veya sektörel değeri düşük içerik.
- 1-2: Paylaşmaya değmez düzeyde zayıf içerik. Bu durumda genellikle is_solar_related false olmalı.
- Puanı şişirme. Haberde MW, kW, yatırım tutarı, şirket adı, tesis/proje adı, ülke/şehir veya regülasyon etkisi varsa puanı buna göre gerekçelendir.
- importance_reason tek kısa cümle olsun; genel övgü değil, puanın nedenini söylesin.

Özet kuralları:

- Genel ve boş ifadeler kullanma.
- Haberin en önemli somut bilgisini ilk cümlede ver.
- MW, kW, panel sayısı, yatırım miktarı, üretim kapasitesi, tasarruf miktarı, şirket adı, proje adı veya bölge bilgisi varsa özette mutlaka geçir.
- Kullanıcı habere tıklamadan ana bilgiyi anlayabilmeli.
- "Önemli bir adım", "odaklanıyor", "hedefliyor", "değerlendiriliyor", "katkı sağlayacak" gibi genel ifadeleri mümkün olduğunca kullanma.
- En fazla 3 kısa cümle yaz.
- Bilgi metinde yoksa uydurma; sadece haber metnindeki verileri kullan.
"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": "Sen güneş enerjisi sektörünü takip eden, tekrar haberleri ayıklayan ve somut verileri koruyarak kısa bülten özeti hazırlayan tarafsız bir haber editörüsün."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
        temperature=0.1,
        max_tokens=260,
    )

    content = response.choices[0].message.content.strip()
    content = content.replace("```json", "").replace("```", "").strip()

    return json.loads(content), response.usage


def select_news(candidates):
    candidates = sorted(
        candidates,
        key=lambda item: item.get("importance_score", 5),
        reverse=True,
    )
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


async def send_message(text, parse_mode=None):
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            print(f"Telegram gönderim hatası. Deneme {attempt + 1}/3: {e}")
            await asyncio.sleep(5)

    print("Telegram mesajı 3 denemede gönderilemedi, devam ediliyor.")
    return False


async def main():
    sent_events = load_sent_events()
    candidates = []
    total_tokens = 0

    sources = [
        ("Türkiye", RSS_TR, RSS_LIMIT_TR),
        ("Küresel", RSS_GLOBAL, RSS_LIMIT_GLOBAL),
    ]

    for rss_region, rss_url, limit in sources:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:limit]:
            if not is_recent_entry(entry):
                print(f"Geçildi eski haber: {getattr(entry, 'title', '')}")
                continue

            title = clean_text(entry.title)
            link = entry.link
            title_dedupe_key = normalize_for_key(title)

            if title_dedupe_key in sent_events:
                print(f"Geçildi tekrar başlık: {title_dedupe_key}")
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
    
                previous_topics = sorted(sent_events)
                current_topics = [
                    f"{item['canonical_topic']} | {item['title']}"
                    for item in candidates[-10:]
                ]
                data, usage = ai_analyze_news(
                    title,
                    article_text,
                    rss_region,
                    previous_topics,
                    current_topics
                )

                if usage:
                    total_tokens += usage.total_tokens

                if not data.get("is_solar_related", False):
                    print(f"Geçildi AI güneş değil: {title}")
                    continue
                    
                if data.get("is_duplicate", False):
                    print(f"Geçildi AI tekrar: {title}")
                    continue
                
                canonical_topic = data.get("canonical_topic", title)
                dedupe_key = normalize_for_key(canonical_topic)
                
                if dedupe_key in sent_events:
                    print(f"Geçildi tekrar konu: {dedupe_key}")
                    continue
                
                detected_region = data.get("detected_region", rss_region)
                
                if detected_region not in ["Türkiye", "Küresel"]:
                    detected_region = rss_region
                
                candidates.append({
                    "region": detected_region,
                    "title": title,
                    "link": link,
                    "category": data.get("category", "Genel"),
                    "importance_score": normalize_importance_score(data.get("importance_score")),
                    "importance_reason": data.get("importance_reason", "").strip(),
                    "dedupe_key": dedupe_key,
                    "canonical_topic": canonical_topic,
                    "summary": data.get("summary", "").strip(),
                    "tokens": usage.total_tokens if usage else 0,
                })

            except Exception as e:
                print(f"Hata: {title}")
                print(str(e))

    selected = select_news(candidates)

    if not selected:
        print("="*50)
        print("BUGÜN YETERLİ KALİTEDE HABER BULUNAMADI")
        print(f"Toplam analiz edilen aday: {len(candidates)}")
        print("="*50)
    
        return

    today = datetime.now().strftime("%d.%m.%Y")

    await send_message(
        f"☀️ Solar8 Günlük Enerji Bülteni | {today}\n\n"
        f"Bugün seçilen {len(selected)} güneş enerjisi gelişmesi aşağıda."
    )

    for item in selected:
        text = (
            "☀️ Solar8 Enerji Haberleri\n\n"
            f"🌍 Bölge: {item['region']}\n"
            f"🏷️ Kategori: {html.escape(item['category'])}\n\n"
            f"⭐ Önem: {item['importance_score']}/10 — {html.escape(item['importance_reason'])}\n\n"
            f'📰 <a href="{item["link"]}">{html.escape(item["title"])}</a>\n\n'
            f"🧠 Özet:\n{html.escape(item['summary'])}\n\n"
            f'🔗 <a href="{item["link"]}">Haberi Oku</a>'
        )

        sent_ok = await send_message(text, parse_mode="HTML")

        if sent_ok:
            sent_events.add(item["dedupe_key"])
            save_sent_events(sent_events)
            try:
                archive_sent_news(item)
            except Exception as e:
                print(f"Arşiv yazma hatası: {e}")

        print("=" * 40)
        print(item["title"])
        print(f"Canonical: {item.get('canonical_topic')}")
        print(f"Bölge: {item['region']}")
        print(f"Önem: {item['importance_score']}/10 - {item['importance_reason']}")
        print(f"Tokens: {item['tokens']}")

    print(f"Toplam token: {total_tokens}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        reset_memory()
    else:
        asyncio.run(main())
