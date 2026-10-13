# Tradebotten Aggressiva Lena 🚀
**High-Frequency Momentum & Orderbook Flow Scalper for OKX**  
*Inspirerad av Ken Griffin style quantitative agent execution.*

---

## ⚡ Om Botten
`tradebottenaggresivalena` är en högfrekvent algoritmisk tradingbot specialbyggd för att köra på subkontot **`aggresivalena-api`** på OKX. Den körs oberoende och parallellt med konservativa `tradebottenanna`.

### 🎯 Kärnstrategi: "Aggressive Flow Compounding"
1. **Multi-Asset Momentum Scanning**: Skannar kontinuerligt likvida par (`SOL-USDC`, `SUI-USDC`, `BTC-USDC`, `ETH-USDC`, `DOGE-USDC`, `APT-USDC`).
2. **Order Book Imbalance (OBI) & Tape Reading**: Upptäcker aggressiva köp-/säljordrar och spreads i realtid.
3. **Dynamic Stack Sizing & Compounding**:
   - Skalar positionsstorlek dynamiskt utifrån kontots levande kapital (USDC stack).
   - Vid vinnande sekvenser ökas vinståterinvesteringen (geometric compounding).
4. **Strikta Skyddsräcken (Risk Guards)**:
   - **Tight Invalidation**: Snabb cut-off om mikrostödet punkteras.
   - **Trailing Profit Lock**: Säkrar snabba spikes.
   - **Emergency Kill Switch**: Stoppar omedelbart om total drawdown understiger specificerad säkerhetsnivå.

---

## 🛠️ Snabbstart

### 1. Förutsättningar
- Python 3.10+ eller Node.js 20+
- OKX API-nycklar för subkontot `aggresivalena-api`

### 2. Konfigurera `.env`
Kopiera `.env.example` till `.env` och fyll i:
```bash
OKX_API_KEY="din_api_nyckel"
OKX_API_SECRET="din_hemlighet"
OKX_API_PASSPHRASE="ditt_passphrase"
OKX_IS_DEMO=false
MAX_POSITION_USDC=50.0
SL_PERCENT=0.015
TP_PERCENT=0.035
KILL_SWITCH_MIN_BALANCE=35.0
```

### 3. Körning via Python Daemon
```bash
pip install -r requirements.txt
python -u bot.py
```

### 4. Körning via Docker
```bash
docker build -t aggresivalena .
docker run -d --name lena --env-file .env aggresivalena
```

---

## 🛡️ Säkerhetsnotering
Detta subkonto är isolerat på OKX (`aggresivalena-api`). Kapital och risk är helt separerade från huvudkontot och subkontot `Tradebottenanna_real`.
