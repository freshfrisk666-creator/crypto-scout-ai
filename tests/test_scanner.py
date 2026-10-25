from agents.scanner import (
    get_top_crypto_universe,
    is_excluded_asset,
)


def make_config():
    """Return a test configuration for asset-exclusion rules."""
    return {
        "market": {
            "stablecoin_exclusions": [
                "USDT",
                "USDC",
                "RLUSD",
                "U",
            ],
            "stablecoin_name_keywords": [
                "stablecoin",
                "stable coin",
                "stables",
                "usd coin",
            ],
            "tokenized_commodity_exclusions": [
                "XAUT",
                "PAXG",
                "KAU",
                "KAG",
            ],
            "tokenized_commodity_name_keywords": [
                "tether gold",
                "pax gold",
                "tokenized gold",
                "tokenized silver",
                "gold token",
                "silver token",
            ],
            "non_crypto_asset_symbols": [
                "BFUSD",
                "CRCLB",
                "SPCXB",
                "NVDAB",
            ],
            "non_crypto_name_keywords": [
                "bstocks",
                "tokenized stock",
                "tokenized stocks",
                "tokenized etf",
                "tokenized equity",
                "tokenized security",
                "tokenized securities",
            ],
        }
    }


def test_known_stablecoin_symbols_are_excluded():
    config = make_config()

    excluded, reason = is_excluded_asset(
        {
            "symbol": "RLUSD",
            "name": "Ripple USD",
        },
        config,
    )

    assert excluded is True
    assert reason == "stablecoin_symbol"


def test_united_stables_is_excluded_by_symbol():
    config = make_config()

    excluded, reason = is_excluded_asset(
        {
            "symbol": "U",
            "name": "United Stables",
        },
        config,
    )

    assert excluded is True
    assert reason == "stablecoin_symbol"


def test_tokenized_gold_is_excluded():
    config = make_config()

    excluded, reason = is_excluded_asset(
        {
            "symbol": "XAUT",
            "name": "Tether Gold",
        },
        config,
    )

    assert excluded is True
    assert reason == "tokenized_commodity_symbol"


def test_normal_crypto_asset_is_allowed():
    config = make_config()

    excluded, reason = is_excluded_asset(
        {
            "symbol": "BNB",
            "name": "BNB",
        },
        config,
    )

    assert excluded is False
    assert reason is None


def test_bstocks_tokenized_stock_is_excluded():
    config = make_config()

    excluded, reason = is_excluded_asset(
        {
            "symbol": "SPCXB",
            "name": "SpaceX (bStocks Tokenized Stock)",
        },
        config,
    )

    assert excluded is True
    assert reason == "non_crypto_symbol"


def test_bfusd_is_excluded_from_directional_universe():
    config = make_config()

    excluded, reason = is_excluded_asset(
        {
            "symbol": "BFUSD",
            "name": "BFUSD",
        },
        config,
    )

    assert excluded is True
    assert reason == "non_crypto_symbol"


def test_universe_paginates_until_target_size(monkeypatch):
    """
    Verify that the scanner continues to subsequent pages when
    exclusions prevent the target universe size from being reached.
    """
    pages = {
        1: [
            {
                "id": "tether",
                "symbol": "usdt",
                "name": "Tether",
                "market_cap_rank": 1,
                "market_cap": 1000,
                "current_price": 1,
            },
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap_rank": 2,
                "market_cap": 900,
                "current_price": 60000,
            },
        ],
        2: [
            {
                "id": "ethereum",
                "symbol": "eth",
                "name": "Ethereum",
                "market_cap_rank": 3,
                "market_cap": 800,
                "current_price": 3000,
            },
            {
                "id": "united-stables",
                "symbol": "u",
                "name": "United Stables",
                "market_cap_rank": 4,
                "market_cap": 700,
                "current_price": 1,
            },
        ],
        3: [
            {
                "id": "solana",
                "symbol": "sol",
                "name": "Solana",
                "market_cap_rank": 5,
                "market_cap": 600,
                "current_price": 150,
            },
            {
                "id": "xrp",
                "symbol": "xrp",
                "name": "XRP",
                "market_cap_rank": 6,
                "market_cap": 500,
                "current_price": 0.5,
            },
        ],
    }

    requested_pages = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params, headers, timeout):
        page = params["page"]
        requested_pages.append(page)
        return FakeResponse(pages[page])

    monkeypatch.setattr(
        "agents.scanner.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        "agents.scanner.time.sleep",
        lambda _: None,
    )

    config = {
        "market": {
            "universe_size": 3,
            "stablecoin_exclusions": ["USDT"],
            "stablecoin_name_keywords": [
                "stablecoin",
                "stable coin",
                "stables",
                "usd coin",
            ],
            "tokenized_commodity_exclusions": [
                "XAUT",
                "PAXG",
            ],
            "tokenized_commodity_name_keywords": [
                "tether gold",
                "pax gold",
            ],
            "non_crypto_asset_symbols": [
                "BFUSD",
                "CRCLB",
                "SPCXB",
                "NVDAB",
            ],
            "non_crypto_name_keywords": [
                "bstocks",
                "tokenized stock",
                "tokenized stocks",
                "tokenized etf",
                "tokenized equity",
                "tokenized security",
                "tokenized securities",
            ],
        },
        "data": {
            "coingecko_url": "https://example.test/coins/markets",
            "timeout_seconds": 5,
            "coingecko_page_size": 2,
            "coingecko_max_pages": 3,
            "coingecko_page_delay_seconds": 0,
        },
    }

    result = get_top_crypto_universe(config)

    assert [coin["id"] for coin in result] == [
        "bitcoin",
        "ethereum",
        "solana",
    ]

    assert requested_pages == [1, 2, 3]
