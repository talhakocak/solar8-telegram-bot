import os
import json
import streamlit as st
from datetime import datetime

SENT_EVENTS_FILE = "sent_events.json"
STATS_FILE = "bot_stats.json"

st.set_page_config(
    page_title="Solar8 Panel",
    page_icon="☀️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
.block-container {
    padding-top: 3.1rem;
    padding-bottom: 0rem;
    padding-left: 2rem;
    padding-right: 2rem;
    max-width: 100%;
}

h1 {
    font-size: 1.8rem !important;
    margin-top: 0rem !important;
    margin-bottom: 0rem !important;
}

h2, h3 {
    margin-top: 0rem !important;
    margin-bottom: 0.4rem !important;
}

[data-testid="stMetricValue"] {
    font-size: 1.55rem;
}

[data-testid="stMetricLabel"] {
    font-size: 0.8rem;
}

hr {
    margin-top: 0.4rem;
    margin-bottom: 0.8rem;
}

div[data-testid="stVerticalBlock"] {
    gap: 0.25rem;
}

button {
    height: 2.4rem !important;
}

.memory {
    font-size: 0.74rem;
    line-height: 1.15rem;
    color: #d1d5db;
    word-break: break-word;
}

.status-good {
    color: #22c55e;
    font-weight: 700;
}

.status-warn {
    color: #facc15;
    font-weight: 700;
}

.footer {
    color: #9ca3af;
    font-size: 0.78rem;
    margin-top: 0.4rem;
}
</style>
""", unsafe_allow_html=True)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


sent_events = load_json(SENT_EVENTS_FILE, [])

stats = load_json(STATS_FILE, {
    "last_run": "Henüz çalışmadı",
    "last_token_usage": 0,
    "last_sent_count": 0,
    "total_runs": 0,
    "total_token_usage": 0
})

st.title("☀️ Solar8 Haber Bot Paneli")
st.caption("Ekip içi güneş enerjisi haber takip ekranı")

m1, m2, m3, m4, m5 = st.columns(5)

m1.metric("Hafızadaki Haber", len(sent_events))
m2.metric("Son Gönderim", stats.get("last_sent_count", 0))
m3.metric("Son Token", stats.get("last_token_usage", 0))
m4.metric("Toplam Çalışma", stats.get("total_runs", 0))
m5.metric("Toplam Token", stats.get("total_token_usage", 0))

st.divider()

left, middle, right = st.columns([1, 1.05, 1.45])

with left:
    st.subheader("⚙️ Kontroller")

    if st.button("🧹 Hafızayı Sıfırla", use_container_width=True):
        save_json(SENT_EVENTS_FILE, [])
        st.success("Hafıza sıfırlandı.")
        st.rerun()

    if st.button("📊 İstatistikleri Sıfırla", use_container_width=True):
        save_json(STATS_FILE, {
            "last_run": "Henüz çalışmadı",
            "last_token_usage": 0,
            "last_sent_count": 0,
            "total_runs": 0,
            "total_token_usage": 0
        })
        st.success("İstatistikler sıfırlandı.")
        st.rerun()

    st.markdown("**Otomatik:** 08:00")
    st.markdown("**Telegram:** `/news`")
    st.markdown("**Ucuz test:** `/test`")

with middle:
    st.subheader("📊 Son Durum")

    last_sent = stats.get("last_sent_count", 0)

    st.markdown(f"**Son çalışma:** {stats.get('last_run', 'Henüz çalışmadı')}")
    st.markdown(f"**Son haber:** {last_sent}")
    st.markdown(f"**Son token:** {stats.get('last_token_usage', 0)}")
    st.markdown(f"**Toplam token:** {stats.get('total_token_usage', 0)}")
    st.markdown(f"**Toplam çalışma:** {stats.get('total_runs', 0)}")

    if last_sent > 0:
        st.markdown('<span class="status-good">● Son çalışmada haber gönderildi.</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="status-warn">● Son çalışmada haber gönderilmedi.</span>', unsafe_allow_html=True)

with right:
    st.subheader("🧠 Son Haber Hafızası")

    if sent_events:
        last_items = sent_events[-6:]
        memory_text = "<br>".join([f"• {item}" for item in last_items])
        st.markdown(f'<div class="memory">{memory_text}</div>', unsafe_allow_html=True)
    else:
        st.caption("Hafıza boş.")

st.markdown(
    f'<div class="footer">Solar8 MVP Panel • Yenileme: {datetime.now().strftime("%d.%m.%Y %H:%M")}</div>',
    unsafe_allow_html=True
)