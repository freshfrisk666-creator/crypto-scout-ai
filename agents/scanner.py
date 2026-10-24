from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import ccxt
import requests
import yaml


CONFIG_PATH = Path("config/config.yaml")


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Load project configuration from YAML."""
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError(f"Invalid configuration file: {path}")

    return config


def get_top_crypto_universe(
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Fetch cryptocurrencies ranked by market capitalization.

    Stablecoins are excluded. Up to 250 assets are requested from
    CoinGecko, then the first configured number of eligible assets
    is returned.
    """
    market_config = config["market"]
    data_config = config["data"]

    limit = int(market_config["universe_size"])
    if limit < 1 or limit > 250:
        raise ValueError("universe_size must be between 1 and 250.")

    stablecoins = {
        str(symbol).strip().upper()
        for symbol in market_config.get("stablecoin_exclusions", [])
    }

    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    headers = {}

    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 250,
        "page": 1,
        "sparkline": "false",
    }

    url = data_config["coingecko_url"]

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=int(data_config.get("timeout_seconds", 20)),
        )
        response.raise_for_status()
        payload = response.json()

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None

        if status in (401, 403, 429):
            raise RuntimeError(
                "CoinGecko rejected the request or rate limit was reached. "
                "Create a free CoinGecko Demo API key if required, then "
                "set it in the COINGECKO_API_KEY environment variable. "
                "Do not put the key in a public config file."
            ) from exc

        raise RuntimeError(
            f"CoinGecko returned HTTP error {status}."
        ) from exc

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not connect to CoinGecko: {exc}"
        ) from exc

    if not isinstance(payload, list):
        raise RuntimeError("Unexpected response format from CoinGecko.")

    coins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for coin in payload:
        coin_id = str(coin.get("id") or "").strip()
        symbol = str(coin.get("symbol") or "").strip().upper()

        if not coin_id or not symbol:
            continue

        if symbol in stablecoins:
            continue

        if coin_id in seen_ids:
            continue

        seen_ids.add(coin_id)

        coins.append(
            {
                "id": coin_id,
                "name": str(coin.get("name") or symbol),
                "symbol": symbol,
                "ticker": symbol,
                "market_cap_rank": coin.get("market_cap_rank"),
                "market_cap": float(coin.get("market_cap") or 0),
                "current_price": float(coin.get("current_price") or 0),
            }
        )

        if len(coins) >= limit:
            break

    if not coins:
        raise RuntimeError(
            "CoinGecko returned no eligible cryptocurrencies."
        )

    return coins


def filter_binance_spot_pairs(
    coins: list[dict[str, Any]],
    exchange: Any,
    quote_currency: str = "USDT",
) -> list[dict[str, Any]]:
    """
    Keep only assets with an active Binance spot pair against quote_currency.

    The market symbol returned by CCXT is stored as exchange_symbol.
    """
    markets = exchange.load_markets()
    quote = quote_currency.upper()

    markets_by_base: dict[str, list[dict[str, Any]]] = {}

    for market in markets.values():
        if not market.get("spot", False):
            continue

        if market.get("active") is False:
            continue

        if str(market.get("quote") or "").upper() != quote:
            continue

        base = str(market.get("base") or "").upper()
        if not base:
            continue

        markets_by_base.setdefault(base, []).append(market)

    tradable: list[dict[str, Any]] = []

    for coin in coins:
        base = str(coin["symbol"]).upper()
        matches = markets_by_base.get(base, [])

        if not matches:
            continue

        # Deterministic choice if the exchange reports multiple matching markets.
        market = sorted(
            matches,
            key=lambda item: str(item.get("symbol") or ""),
        )[0]

        item = dict(coin)
        item["exchange_symbol"] = market["symbol"]
        tradable.append(item)

    return tradable


def get_tradable_crypto_universe(
    config: dict[str, Any],
    exchange: Any | None = None,
) -> list[dict[str, Any]]:
    """Return the configured CoinGecko universe filtered by Binance spot pairs."""
    if exchange is None:
        exchange_name = str(config["market"]["exchange"]).lower()

        if not hasattr(ccxt, exchange_name):
            raise ValueError(
                f"Unsupported CCXT exchange: {exchange_name}"
            )

        exchange_class = getattr(ccxt, exchange_name)
        exchange = exchange_class(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )

    coins = get_top_crypto_universe(config)

    return filter_binance_spot_pairs(
        coins=coins,
        exchange=exchange,
        quote_currency=config["market"]["quote_currency"],
    )


if __name__ == "__main__":
    project_config = load_config()
    universe = get_tradable_crypto_universe(project_config)

    print("\nCRYPTO SCOUT AI — TRADABLE UNIVERSE")
    print("=" * 80)
    print(f"Eligible CoinGecko assets: {project_config['market']['universe_size']}")
    print(f"Available spot pairs: {len(universe)}")
    print()

    for rank, coin in enumerate(universe, start=1):
        print(
            f"{rank:3d}. "
            f"{coin['name']} ({coin['symbol']}) | "
            f"Market cap rank: {coin['market_cap_rank']} | "
            f"Pair: {coin['exchange_symbol']}"
        )
