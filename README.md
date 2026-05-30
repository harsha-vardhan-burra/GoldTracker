# ⚡ GoldTracker

A Windows desktop application that tracks live gold prices for Indian 
markets with intelligent buy/sell signals, target price alerts, and 
silent system tray integration.

![Python](https://img.shields.io/badge/Python-3.14-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

- 📊 **Live Prices** — 24K and 22K gold updated every 5 minutes
- 🏙️ **City-Specific Retail Rates** — Hyderabad, Vijayawada, Mumbai and more
- 🧠 **Intelligent Buy/Sell Signals** — scored using moving averages, 
  momentum, and volatility
- 🔔 **Target Price Alerts** — Windows notifications when your price is hit
- 📈 **Price History Charts** — 24H, 7D, 30D views
- 🔲 **System Tray Integration** — runs silently in background
- 🚀 **Auto-Launch on Startup** — starts with Windows automatically
- 💾 **Local SQLite Storage** — all history stored privately on your machine

---

## Screenshots

> Dashboard and popup screenshots coming soon

---

## Data Sources

| Source | Purpose |
|--------|---------|
| [GoldAPI.io](https://goldapi.io) | International spot price (USD/oz) |
| [Frankfurter API](https://api.frankfurter.dev) | Live USD/INR conversion |
| [GoodReturns.in](https://goodreturns.in) | Indian city retail rates |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| UI | CustomTkinter |
| Database | SQLite |
| Scheduling | Python threading |
| Tray | pystray |
| Scraping | BeautifulSoup4 + lxml |
| Packaging | PyInstaller |

---

## Setup

### Prerequisites
- Windows 10 or 11
- Python 3.9+
- Free API key from [goldapi.io](https://goldapi.io)

### Installation

```bash
# Clone the repository
git clone https://github.com/harsha-vardhan-burra/GoldTracker.git
cd GoldTracker

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up config
copy config\settings.example.json config\settings.json
# Edit config\settings.json and add your GoldAPI key
```

### Run

```bash
# Normal launch (full dashboard)
python main.py

# Startup mode (notification-style popup)
python main.py --startup
```

---

## Project Structure
GoldTracker/
├── core/
│   ├── data_engine.py      # Fetches from 3 live sources
│   ├── analytics.py        # Buy/sell scoring engine
│   ├── scheduler.py        # Background polling every 5 mins
│   └── alert_engine.py     # Target alerts + spike detection
├── database/
│   └── db_manager.py       # SQLite layer
├── ui/
│   ├── dashboard.py        # Full 5-tab dashboard
│   ├── startup_popup.py    # Borderless notification popup
│   └── tray_icon.py        # System tray integration
├── utils/
│   └── startup_manager.py  # Windows startup registry
├── config/
│   └── settings.example.json
├── assets/
│   └── icon.ico
└── main.py                 # Entry point
---

## How the Buy/Sell Signal Works

The scoring engine analyses 4 signals:

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| Price vs 7-day MA | 30pts | Short-term deviation |
| Price vs 30-day MA | 30pts | Medium-term deviation |
| Momentum | 25pts | Trend direction and speed |
| Volatility | 15pts | Signal reliability |

Score → Label mapping:
- **75–100** → Perfect time to buy
- **55–74** → Good time to buy
- **35–54** → Wait a bit more
- **0–34** → Bad time to buy

Signals improve in accuracy after 7–30 days of data collection.

---

## Important Notes

- `config/settings.json` is gitignored — never committed
- All data stored locally — no external servers
- Free tier APIs only — no paid subscriptions required
- Signals are analytical indicators, not financial advice

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Author

**Harsha Vardhan Burra**  
[GitHub](https://github.com/harsha-vardhan-burra)