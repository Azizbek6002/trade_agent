# ⚡ Robo Trader Boy (RTB) v2.0 — XAUUSD AI Trading System

<div align="center">

![RTB Version](https://img.shields.io/badge/version-2.0.0-gold.svg)
![Asset](https://img.shields.io/badge/asset-XAUUSD%20%28Gold%29-yellow.svg)
![AI](https://img.shields.io/badge/AI-Google%20Gemini%20Pro-blue.svg)
![Terminal](https://img.shields.io/badge/execution-MetaTrader%205%20%28Exness%29-green.svg)
![Frontend](https://img.shields.io/badge/UI-Telegram%20Mini%20App-blueviolet.svg)

**Oltin (XAUUSD) bozoriga ixtisoslashgan, Gemini Pro AI va Exness MT5 bilan integratsiyalashgan to'liq avtomatlashtirilgan aqlli savdo tizimi.**

</div>

---

## 🎯 Loyiha Haqida

**Robo Trader Boy (RTB) v2.0** — professional treyderlar strategiyalari (Classica, SnR, Likvidlik tuzoqlari, Trendlines) asosida Telegram kanallaridan (TraderStage va boshqa VIP manbalar) kelgan signallarni 24/7 qabul qiladi, **Google Gemini Pro** orqali chuqur tahlil qiladi va qat'iy risk boshqaruvi bilan **Exness MT5** terminalida avtomat bitimlar ochadi.

Barcha jarayonlar Telegram ichida ochiluvchi zamonaviy **Telegram Mini App (Dashboard)** orqali boshqariladi.

---

## 🌟 Asosiy Xususiyatlar

### 1. 🤖 Gemini Pro Smart Signal Parser
- Telegram kanallaridagi har qanday formatdagi matnli xabarlar va rasm tagidagi izohlarni (captions) avtomatik o'qiydi.
- Google Gemini Pro orqali xabardan toza JSON formatida `BUY/SELL`, `Entry Zone`, `Stop Loss` va `Take Profit` larni ajratib oladi.
- Takroriy xabarlarga qarshi 10 daqiqalik xesh-keshlash himoyasi.

### 2. 🛡 Qat'iy Risk Guardrail (1.0% — 5.0%)
- Hech qanday bitimda risk **5.0% dan oshib ketishiga yo'l qo'yilmaydi** (backend darajasida qat'iy cheklov).
- Hisob balansi va Stop Loss masofasiga qarab aniq dinamik lot hajmini avtomat hisoblaydi.
- +1R foydaga yetganda Stop Lossni avtomatik ravishda **Break-Even (BE)** darajasiga suradi.
- 1:3 RR (Risk-to-Reward) maqsadli foyda strategiyasi.

### 3. 📅 Yuqori Ta'sirli Yangiliklar Filtri (News Filter)
- CPI, NFP, FOMC va Foiz stavkalari qarorlari e'lon qilinishidan **15 daqiqa oldin** va **15 daqiqa keyin** savdoni avtomat to'xtatuvchi `is_trading_allowed()` gatekeeper.
- O'zgaruvchanlik yuqori paytlarda hisobni kutilmagan qulashlardan asraydi.

### 4. 📱 Telegram Mini App (Web App UI)
Telegram chatida faqat bitta qulay tugma orqali quyidagi to'liq boshqaruv paneli ochiladi:
- **📊 Dashboard**: Exness hisob balansi, Equity, jonli XAUUSD (Bid/Ask) kursi, ochiq pozitsiyalar va har bir bitimni bitta bosishda yopish (`Yopish` tugmasi).
- **🤖 AI Chat & Maslahat**: Gemini Pro bilan real vaqtda muloqot va XAUUSD bozor tahlili.
- **📈 Statistika & Jurnal**: Jami savdolar, Win Rate %, PnL ($) va batafsil savdolar tarixi.
- **🛡 Risk Sozlamalari**: 1.0% dan 5.0% gacha risk slayderi va Master Kill-Switch (avto-savdoni to'xtatish/yoqish).
- **⚙️ Kanallar**: Kuzatuvdagi Telegram kanallari ro'yxati va yangi kanallarni qo'shish/o'chirish.

---

## 🏗 Loyiha Tuzilishi

```text
trade_agent/
├── ai_parser/                # Gemini Pro va Groq tahlil dvigateli
│   └── groq_parser.py
├── decision_engine/          # Kirish zonalari konsensusi va ijro rejasi
│   ├── execution_planner.py
│   └── zone_aggregator.py
├── execution_engine/         # Exness MT5 bridge va pozitsiya monitoringi
│   ├── mt5_bridge.py
│   └── position_monitor.py
├── news_filter/              # Iqtisodiy kalendar va yangiliklar filtri
│   └── economic_calendar.py
├── risk_manager/             # Dinamik lot kalkulyatori & Risk Guardrail
│   └── risk_manager.py
├── telegram/                 # Telethon kanallar tinglovchisi va Bot UI
│   ├── bot_ui.py
│   └── listener.py
├── webapp/                   # FastAPI backend va Mini App HTML/CSS
│   ├── server.py
│   └── templates/
│       └── index.html
├── config.py                 # Markaziy konfiguratsiya
├── database.py               # SQLite ma'lumotlar bazasi qatlami
├── main.py                   # Loyihani ishga tushiruvchi markaziy fayl
├── requirements.txt          # Python kutubxonalari
└── .env.example              # Konfiguratsiya shabloni
```

---

## 🚀 Ishga Tushirish (Quick Start)

### 1. Repozitoriyani klonlash:
```bash
git clone git@github.com:Azizbek6002/trade_agent.git
cd trade_agent
```

### 2. Virtual muhit yaratish va kutubxonalarni o'rnatish:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. `.env` faylini sozlash:
`.env.example` dan nusxa oling va o'z ma'lumotlaringizni kiriting:
```bash
cp .env.example .env
nano .env
```

Kerakli parametrlar:
- `TELEGRAM_API_ID` & `TELEGRAM_API_HASH` (my.telegram.org)
- `TELEGRAM_BOT_TOKEN` (@BotFather)
- `GEMINI_API_KEY` (Google AI Studio)
- `MT5_ACCOUNT`, `MT5_PASSWORD`, `MT5_SERVER` (Exness Demo yoki Real)

### 4. Tizimni ishga tushirish:
```bash
python3 main.py
```

---

## 🔒 Xavfsizlik Qoidalari

- `.env` va sessiya fayllari `.gitignore` orqali to'liq himoyalangan va GitHub'ga chiqmaydi.
- Faqat tasdiqlangan va ruxsat berilgan admin telegram ID buyruqlar bera oladi.
- Risk Guardrail doimo faol va har bir order ochilishidan avval mustaqil tekshiriladi.

---

## 👨‍💻 Muallif & Litsenziya

- **Tuzuvchi:** [Azizbek6002](https://github.com/Azizbek6002)
- **Litsenziya:** MIT License. Erkin foydalanish va rivojlantirish uchun ochiq.
