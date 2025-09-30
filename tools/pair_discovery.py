#!/usr/bin/env python3
"""Discover Binance USDT-M linear perpetuals for Freqtrade whitelists."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterable, List, Tuple

import ccxt


@dataclass
class PairFilterOptions:
    """Filter configuration for pair discovery."""

    min_quote_vol: float
    min_oi: float
    top: int
    out: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-quote-vol", type=float, default=2_000_000.0)
    parser.add_argument(
        "--min-oi",
        type=float,
        default=0.0,
        help="Minimum open interest notionals; set to 0 to disable the filter.",
    )
    parser.add_argument("--top", type=int, default=100, help="Keep top N pairs after filtering.")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Optional path to write the resulting whitelist. Defaults to stdout only.",
    )
    return parser


def get_exchange() -> ccxt.binanceusdm:
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    exchange.load_markets()
    return exchange


def fetch_perpetual_markets(exchange: ccxt.binanceusdm) -> List[dict]:
    markets = exchange.load_markets()
    return [
        market
        for market in markets.values()
        if market.get("contract")
        and market.get("linear")
        and market.get("quote") == "USDT"
        and market.get("settle") == "USDT"
    ]


def fetch_tickers(exchange: ccxt.binanceusdm, symbols: Iterable[str]) -> dict:
    try:
        return exchange.fetch_tickers(list(symbols))
    except Exception as exc:  # pragma: no cover - network failure fallback
        print(f"Error fetching tickers: {exc}", file=sys.stderr)
        return {}


def fetch_recent_oi(exchange: ccxt.binanceusdm, market: dict) -> float:
    symbol_id = market.get("id", market["symbol"].replace("/", ""))
    try:
        history = exchange.fetch_open_interest_history(
            symbol_id, timeframe="5m", limit=5
        )
        if history:
            return float(history[-1].get("openInterest", 0.0) or 0.0)
    except Exception:
        return 0.0
    return 0.0


def filter_pairs(exchange: ccxt.binanceusdm, options: PairFilterOptions) -> List[Tuple[str, float]]:
    markets = fetch_perpetual_markets(exchange)
    tickers = fetch_tickers(exchange, (m["symbol"] for m in markets))

    qualified: List[Tuple[str, float]] = []
    for market in markets:
        symbol = market["symbol"]
        ticker = tickers.get(symbol, {})
        quote_vol = float(ticker.get("quoteVolume", 0.0) or 0.0)
        if quote_vol < options.min_quote_vol:
            continue
        if options.min_oi > 0:
            oi_value = fetch_recent_oi(exchange, market)
            if oi_value < options.min_oi:
                continue
        qualified.append((symbol, quote_vol))

    qualified.sort(key=lambda item: item[1], reverse=True)
    return qualified[: options.top]


def output_pairs(pairs: Iterable[str], destination: str) -> None:
    lines = "\n".join(pairs)
    if destination:
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write(lines + "\n")
    print(lines)


def main() -> None:
    args = build_parser().parse_args()
    options = PairFilterOptions(
        min_quote_vol=args.min_quote_vol,
        min_oi=args.min_oi,
        top=max(1, args.top),
        out=args.out,
    )
    exchange = get_exchange()
    pairs = filter_pairs(exchange, options)
    output_pairs((symbol for symbol, _ in pairs), options.out)


if __name__ == "__main__":
    main()
