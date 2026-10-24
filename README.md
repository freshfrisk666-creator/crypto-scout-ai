# Crypto Scout AI

**Crypto Scout AI V1** is an independent research and paper-trading project designed to test a systematic trading strategy on the cryptocurrency market.

The project is completely separate from `stock-scout-ai`.

No real money is used and no real orders are sent to any exchange.

---

## Objective

The first objective is to monitor the **Top 200 cryptocurrencies by market capitalization**, apply a traceable technical scoring methodology, identify the best opportunities, and manage a virtual paper portfolio.

The initial pipeline is:

```text
Top 200 Crypto
      ↓
Universe & Liquidity Filter
      ↓
Technical Analysis
      ↓
Technical Scoring
      ↓
Top 10 Opportunities
      ↓
Paper Portfolio
      ↓
24/7 Position Monitoring
      ↓
Target / Stop
      ↓
Closed Trade
      ↓
Performance Analysis
```

The purpose of V1 is **not to predict the market**.

The purpose is to determine whether a systematic and reproducible strategy produces meaningful results when applied to cryptocurrency markets.

---

## Project Independence

This repository is intentionally independent from the stock project.

### Crypto Scout AI

- Cryptocurrency market only
- Top 200 crypto universe
- 24/7 market
- Separate database
- Separate paper portfolio
- Separate GitHub Actions
- Separate dashboard
- Separate performance statistics

### Stock Scout AI

- S&P 500 only
- Equity market
- Separate database
- Separate paper portfolio
- Separate workflows
- Separate results

The two projects may use similar concepts, but their data, trades and results must remain completely separate.

---

# V1 Strategy

The first version intentionally keeps the strategy simple.

The core technical components are:

1. Trend
2. Momentum
3. RSI
4. Volume
5. Breakout
6. ATR-based risk management

The initial scoring model is:

| Component | Weight |
|---|---:|
| Trend | 30% |
| Momentum | 25% |
| RSI | 15% |
| Volume | 15% |
| Breakout | 15% |

Total:

```text
100%
```

---

## Trend

The trend component evaluates:

```text
Price > SMA20
SMA20 > SMA50
SMA50 > SMA200
Price > SMA200
```

Each condition contributes equally to the trend score.

---

## Momentum

Momentum uses two horizons:

- 21 periods
- 63 periods

The objective is to combine short/intermediate momentum without relying on a single lookback period.

---

## RSI

The RSI component rewards a constructive momentum zone rather than simply maximizing RSI.

The initial model considers the area around:

```text
55–68
```

as the strongest zone.

Extreme RSI values are penalized because very strong momentum can also indicate an overextended move.

---

## Volume

Volume is evaluated relative to its recent average.

The objective is to determine whether the current move is supported by meaningful trading activity.

The universe also applies a minimum average dollar-volume filter to avoid extremely illiquid assets.

---

## Breakout

The breakout component evaluates the relationship between the current price and recent highs.

The purpose is to identify assets showing evidence of price expansion rather than simply ranking assets by recent performance.

---

# Risk Management

V1 uses ATR-based risk management.

### Entry

The initial paper entry is based on the latest available market price.

### Stop

```text
Stop = Entry − 1.5 × ATR14
```

### Target

```text
Target = Entry + 3 × ATR14
```

This produces a theoretical:

```text
Risk / Reward = 2 : 1
```

These values are research parameters and are not guarantees of performance.

---

# Paper Portfolio

The initial virtual account is:

```text
$100,000
```

Maximum number of simultaneous positions:

```text
10
```

The system does not send orders to an exchange.

Each trade records:

- ticker
- symbol
- entry time
- entry price
- position size
- stop
- target
- technical score
- individual component scores
- reason for opening
- exit time
- exit price
- exit reason
- realized P&L
- realized return

---

# Market

Cryptocurrency markets operate continuously.

Unlike the stock project, there is no:

```text
Market Open
Market Close
```

The crypto project therefore uses a continuous monitoring architecture.

The initial design uses:

```text
Signal timeframe: 4h
Monitoring timeframe: 5m
```

The 4-hour timeframe is used to generate swing-trading signals.

The 5-minute timeframe is used to monitor open paper positions for stop/target events.

---

# Universe

The initial universe consists of approximately the:

```text
Top 200 cryptocurrencies by market capitalization
```

Stablecoins are excluded.

The scanner also applies a liquidity filter.

The objective is to avoid producing signals on assets that could not realistically support the paper strategy.

The exact universe can change over time as cryptocurrency market capitalization changes.

---

# Data

V1 uses public market data.

The initial architecture uses:

- CoinGecko for market-cap universe discovery
- CCXT for exchange market data
- Binance public spot OHLCV/ticker endpoints

No private exchange API key is required for the initial paper-trading implementation.

No real orders are sent.

---

# Repository Structure

```text
crypto-scout-ai/
│
├── README.md
├── requirements.txt
├── .gitignore
│
├── config/
│   └── config.yaml
│
├── data/
│
├── agents/
│   ├── scanner.py
│   ├── technical.py
│   └── intelligence.py
│
├── scoring/
│   └── signal_engine.py
│
├── portfolio/
│   └── paper_trading.py
│
├── database/
│   └── db.py
│
├── runner/
│   ├── crypto_scanner.py
│   └── crypto_monitor.py
│
├── scripts/
│   └── build_dashboard.py
│
├── dashboard/
│   ├── index.html
│   ├── app.js
│   └── style.css
│
├── tests/
│
└── .github/
    └── workflows/
        ├── scanner.yml
        ├── monitor.yml
        └── dashboard.yml
```

Some components may initially be placeholders and will be implemented progressively.

---

# Installation

Clone the repository:

```bash
git clone https://github.com/YOUR-USERNAME/crypto-scout-ai.git
cd crypto-scout-ai
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

---

# Local Test

The project should always be tested before pushing changes.

Run:

```bash
python -m pytest -q
```

The scanner can later be executed with:

```bash
python -m runner.crypto_scanner
```

The paper monitor can be executed with:

```bash
python -m runner.crypto_monitor
```

---

# GitHub Actions

The project is designed to run automatically through GitHub Actions.

The initial workflows are intended to provide:

### Scanner

Periodically:

```text
Top 200
→ analysis
→ scoring
→ Top 10
→ paper portfolio
```

### Monitor

Continuously checks open paper positions:

```text
Current Price
      ↓
Stop?
      ↓
Target?
      ↓
Close Paper Trade
```

### Dashboard

Builds a simple web representation of the current paper portfolio.

GitHub Actions schedules may occasionally be delayed. This project therefore treats scheduled execution as a research/paper-trading mechanism rather than a guaranteed real-time trading infrastructure.

---

# Performance Analysis

The first meaningful performance report will be based on closed trades.

The main metrics will include:

- Win Rate
- Loss Rate
- Average Win
- Average Loss
- Profit Factor
- Expectancy
- Average R
- Maximum Drawdown
- Target Rate
- Stop Rate
- Average Holding Time

The system should collect enough trades before drawing conclusions.

A small number of trades is not considered sufficient evidence that the strategy works.

---

# Research Philosophy

The project follows a simple principle:

> **Measure first. Optimize later.**

The first version should remain deliberately simple.

We do not initially add:

- news
- sentiment
- rumors
- social media analysis
- machine learning
- predictive AI
- leverage
- futures
- real-money execution

These can be evaluated later as independent additions.

---

# Future Development

After enough paper trades have been collected, the next phase can evaluate:

```text
TRADE CLOSED
      ↓
WHY DID IT WIN / LOSE?
      ↓
WHICH COMPONENT WAS WRONG?
      ↓
WHICH PATTERN RECURRED?
      ↓
CHALLENGER STRATEGY
      ↓
BACKTEST
      ↓
WALK-FORWARD TEST
      ↓
CRYPTO SCOUT AI V1.1
```

Possible future additions include:

- news intelligence
- earnings/events where applicable
- crypto-specific fundamental data
- sentiment
- volatility regimes
- BTC market regime
- dominance analysis
- sector/category analysis
- challenger strategies
- backtesting
- walk-forward validation

These additions should only be introduced after the V1 baseline has been measured.

---

# Important Disclaimer

Crypto Scout AI is a research and paper-trading project.

It is not financial advice.

No part of this repository should be interpreted as a recommendation to buy or sell cryptocurrency.

Past or simulated performance does not guarantee future results.

The V1 system does not execute real trades.

---

# Version

Current version:

```text
Crypto Scout AI V1.0
```

Status:

```text
Development / Paper Trading
```