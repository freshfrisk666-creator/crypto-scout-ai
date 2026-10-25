from __future__ import annotations

import os
import time
from collections import Counter
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


def is_excluded_asset(
    coin: dict[str, Any],
    config: dict[str, Any],
) -> tuple[bool, str | None]:
    """Exclude stablecoins, commodities and configured non-crypto products."""
    market_config = config["market"]

    symbol = str(coin.get("symbol") or "").strip().upper()
    name = str(coin.get("name") or "").strip().lower()

    exclusion_groups = (
        (
            "stablecoin_symbol",
            "stablecoin_exclusions",
        ),
        (
            "tokenized_commodity_symbol",
            "tokenized_commodity_exclusions",
        ),
        (
            "non_crypto_symbol",
            "non_crypto_asset_symbols",
        ),
    )

    for reason, key in exclusion_groups:
        excluded_symbols = {
            str(value).strip().upper()
            for value in market_config.get(key, [])
        }

        if symbol in excluded_symbols:
            return True, reason

    keyword_groups = (
        (
            "stablecoin_name",
            "stablecoin_name_keywords",
        ),
        (
            "tokenized_commodity_name",
            "tokenized_commodity_name_keywords",
        ),
        (
            "non_crypto_name",
            "non_crypto_name_keywords",
        ),
    )

    for reason, key in keyword_groups:
        keywords = {
            str(value).strip().lower()
            for value in market_config.get(key, [])
        }

        for keyword in keywords:
            if keyword and keyword in name:
                return True, f"{reason}:{keyword}"

    return False, None


def fetch_coingecko_page(
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    retries: int = 3,
) -> list[dict[str, Any]]:
    """Fetch one CoinGecko page with bounded retries."""
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )

            status = response.status_code

            if status in (401, 403):
                raise RuntimeError(
                    f"CoinGecko returned HTTP {status}. "
                    "Check API access and COINGECKO_API_KEY."
                )

            if status == 429 or status >= 500:
                if attempt < retries:
                    retry_after = response.headers.get("Retry-After")

                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = min(2 ** (attempt + 1), 20)

                    time.sleep(max(1.0, min(delay, 30.0)))
                    continue

            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, list):
                raise RuntimeError(
                    "Unexpected CoinGecko response: expected a list."
                )

            return payload

        except requests.RequestException as exc:
            last_error = exc

            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))
                continue

            break

    raise RuntimeError(
        f"Unable to retrieve CoinGecko page "
        f"{params.get('page')}: {last_error}"
    )


def get_top_crypto_universe(
    config: dict[str, Any],
    target_size: int | None = None,
    *,
    require_target: bool = True,
) -> list[dict[str, Any]]:
    """
    Fetch CoinGecko assets in market-cap order, using pagination.

    With require_target=False, return all eligible assets collected
    within the configured page limit, even if the target is not reached.
    """
    market_config = config["market"]
    data_config = config["data"]

    if target_size is None:
        target_size = int(market_config["universe_size"])

    target_size = int(target_size)
    page_size = int(data_config.get("coingecko_page_size", 250))
    max_pages = int(data_config.get("coingecko_max_pages", 20))
    page_delay = float(
        data_config.get("coingecko_page_delay_seconds", 1.2)
    )
    timeout = int(data_config.get("timeout_seconds", 20))

    if target_size < 1:
        raise ValueError("target_size must be positive.")

    if not 1 <= page_size <= 250:
        raise ValueError(
            "coingecko_page_size must be between 1 and 250."
        )

    if max_pages < 1:
        raise ValueError("coingecko_max_pages must be positive.")

    if require_target and target_size > page_size * max_pages:
        raise ValueError(
            "Requested target exceeds pagination capacity. "
            "Increase coingecko_max_pages."
        )

    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    headers: dict[str, str] = {}

    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    url = data_config["coingecko_url"]

    coins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    excluded_counts: Counter[str] = Counter()

    rows_seen = 0
    pages_fetched = 0

    for page in range(1, max_pages + 1):
        print(
            f"[CoinGecko] Pagina {page}/{max_pages}: "
            "richiesta in corso...",
            flush=True,
        )
        if page > 1 and page_delay > 0:
            time.sleep(page_delay)

        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": page_size,
            "page": page,
            "sparkline": "false",
        }

        payload = fetch_coingecko_page(
            url=url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

        pages_fetched += 1
        rows_seen += len(payload)

        if not payload:
            break

        for item in payload:
            coin_id = str(item.get("id") or "").strip()
            symbol = str(item.get("symbol") or "").strip().upper()
            name = str(item.get("name") or symbol).strip()

            if not coin_id or not symbol or coin_id in seen_ids:
                continue

            seen_ids.add(coin_id)

            coin = {
                "id": coin_id,
                "name": name,
                "symbol": symbol,
                "ticker": symbol,
                "market_cap_rank": item.get("market_cap_rank"),
                "market_cap": float(item.get("market_cap") or 0),
                "current_price": float(item.get("current_price") or 0),
            }

            excluded, reason = is_excluded_asset(coin, config)

            if excluded:
                excluded_counts[reason or "other"] += 1
                continue

            coins.append(coin)

            if len(coins) >= target_size:
                break

        if len(coins) >= target_size:
            break

        if len(payload) < page_size:
            break

    print("\nCOINGECKO CANDIDATE POOL")
    print("=" * 65)
    print(f"Pages fetched: {pages_fetched}")
    print(f"Raw market rows scanned: {rows_seen}")
    print(f"Eligible assets collected: {len(coins)}")
    print(f"Candidate target: {target_size}")
    print(
            f"[CoinGecko] Pagina {page}: "
            f"{len(payload)} righe ricevute; "
            f"{len(coins)}/{target_size} asset idonei raccolti.",
            flush=True,
          )

    if excluded_counts:
        print("Excluded assets by rule:")
        for reason, count in excluded_counts.most_common():
            print(f"  {reason}: {count}")

    if require_target and len(coins) < target_size:
        raise RuntimeError(
            f"Only {len(coins)} eligible CoinGecko assets found; "
            f"{target_size} requested."
        )

    return coins[:target_size]


def filter_binance_spot_pairs(
    coins: list[dict[str, Any]],
    exchange: Any,
    quote_currency: str = "USDT",
) -> list[dict[str, Any]]:
    """Match unambiguous assets to active Binance spot markets."""
    markets = exchange.load_markets()
    quote = quote_currency.upper()

    symbol_counts = Counter(
        str(coin.get("symbol") or "").upper()
        for coin in coins
    )

    markets_by_base: dict[str, list[dict[str, Any]]] = {}

    for market in markets.values():
        if not market.get("spot", False):
            continue

        if market.get("active") is False:
            continue

        if str(market.get("quote") or "").upper() != quote:
            continue

        base = str(market.get("base") or "").upper()

        if base:
            markets_by_base.setdefault(base, []).append(market)

    tradable: list[dict[str, Any]] = []
    ambiguous_symbols: set[str] = set()

    for coin in coins:
        base = str(coin["symbol"]).upper()

        # Do not guess where different CoinGecko projects share a ticker.
        if symbol_counts[base] > 1:
            ambiguous_symbols.add(base)
            continue

        matches = markets_by_base.get(base, [])

        if not matches:
            continue

        market = sorted(
            matches,
            key=lambda item: str(item.get("symbol") or ""),
        )[0]

        item = dict(coin)
        item["exchange_symbol"] = market["symbol"]
        tradable.append(item)

    if ambiguous_symbols:
        print(
            "Distinct CoinGecko projects skipped for ambiguous tickers: "
            f"{len(ambiguous_symbols)}"
        )

    return tradable


def get_tradable_crypto_universe(
    config: dict[str, Any],
    exchange: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Select the configured top market-cap universe first,
    then return the subset with an eligible Binance spot pair.

    universe_size means the number of eligible CoinGecko assets
    selected for research, not the number of exchange markets.
    """
    market_config = config["market"]
    target_assets = int(market_config["universe_size"])

    if exchange is None:
        exchange_name = str(
            market_config.get("exchange", "binance")
        ).lower()

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

    # Select the top 500 eligible assets from CoinGecko,
    # rather than searching thousands of assets for 500 Binance pairs.
    coins = get_top_crypto_universe(
        config,
        target_size=target_assets,
        require_target=True,
    )

    tradable = filter_binance_spot_pairs(
        coins=coins,
        exchange=exchange,
        quote_currency=market_config["quote_currency"],
    )

    tradable.sort(
        key=lambda coin: (
            coin["market_cap_rank"]
            if coin.get("market_cap_rank") is not None
            else 10**9
        )
    )

    print("\nCRYPTO UNIVERSE — TOP MARKET CAP")
    print("=" * 65)
    print(f"Eligible CoinGecko assets selected: {len(coins)}")
    print(f"Available Binance spot pairs: {len(tradable)}")
    print(f"Requested market-cap universe: {target_assets}")

    if not tradable:
        raise RuntimeError(
            "No eligible Binance spot pairs were found "
            "among the selected CoinGecko assets."
        )

    print(
        f"Research universe available on Binance: "
        f"{len(tradable)} pairs"
    )

    return tradable

if __name__ == "__main__":
    project_config = load_config()
    universe = get_tradable_crypto_universe(project_config)

    print("\nCRYPTO SCOUT AI — SELECTED TRADABLE UNIVERSE")
    print("=" * 80)

    for rank, coin in enumerate(universe, start=1):
        print(
            f"{rank:3d}. {coin['name']} ({coin['symbol']}) | "
            f"CoinGecko rank: {coin['market_cap_rank']} | "
            f"Pair: {coin['exchange_symbol']}"
        )
