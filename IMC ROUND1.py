"""
IMC Prosperity Round 1 — Optimized Trader
Target PnL: ~170-200k

Key findings from data analysis:
  INTARIAN_PEPPER_ROOT rises EXACTLY 0.1 per timestamp unit (100ms).
    fair_value = 10_000 + 0.1 * ((day + 2) * 10_000 + timestamp / 100)
    Strategy: get to max long (+50) instantly, HOLD all day, never sell.
    Theoretical max ≈ 50 × 3,000 rise over 3 days = 150,000.

  ASH_COATED_OSMIUM is pure mean reversion around 10,000 (observed std ≈ 5).
    Market spread is ~16 wide. We quote tight at ±3 inside the spread.
    Inventory skew keeps us balanced so we flip both ways and capture spread.
    Expected: ~30-50k from market making.
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json
import math

POSITION_LIMIT = 50

# INTARIAN_PEPPER_ROOT exact linear model (R² ≈ 1.0 from regression)
PEPPER_SLOPE = 0.1        # price units per timestamp unit
PEPPER_BASE  = 10_000.0   # price at global_tick 0  (start of day -2)

ASH_FAIR = 10_000.0       # mean-reversion anchor for ASH_COATED_OSMIUM


def pepper_fair(day: int, timestamp: int) -> float:
    """Exact fair value for INTARIAN_PEPPER_ROOT."""
    global_tick = (day + 2) * 10_000 + timestamp / 100
    return PEPPER_BASE + PEPPER_SLOPE * global_tick


class Trader:

    # ── Book helpers ──────────────────────────────────────────────────────────

    def _best_bid(self, depth: OrderDepth):
        return max(depth.buy_orders) if depth.buy_orders else None

    def _best_ask(self, depth: OrderDepth):
        return min(depth.sell_orders) if depth.sell_orders else None

    # ── INTARIAN_PEPPER_ROOT ──────────────────────────────────────────────────

    def _trade_pepper(
        self,
        depth: OrderDepth,
        position: int,
        day: int,
        timestamp: int,
    ) -> List[Order]:
        """
        Always be max long.
        1. Sweep every ask level — price always goes up, any ask is cheap.
        2. Post a passive resting bid to catch sellers between ticks.
        Never post asks; selling would destroy trend PnL.
        """
        orders: List[Order] = []
        pos = position

        # Aggressive: lift all available ask levels
        for ask_px in sorted(depth.sell_orders):
            qty = min(-depth.sell_orders[ask_px], POSITION_LIMIT - pos)
            if qty <= 0:
                break
            orders.append(Order("INTARIAN_PEPPER_ROOT", ask_px, qty))
            pos += qty

        # Passive: resting bid to catch any incoming sellers
        if pos < POSITION_LIMIT:
            fair = pepper_fair(day, timestamp)
            best_ask = self._best_ask(depth)
            # Quote just below best ask (or fair - 2 if no ask visible)
            passive_bid = math.floor(fair - 2)
            if best_ask is not None:
                passive_bid = min(passive_bid, best_ask - 1)
            qty = POSITION_LIMIT - pos
            orders.append(Order("INTARIAN_PEPPER_ROOT", passive_bid, qty))

        return orders

    # ── ASH_COATED_OSMIUM ─────────────────────────────────────────────────────

    def _trade_ash(
        self,
        depth: OrderDepth,
        position: int,
    ) -> List[Order]:
        """
        Mean reversion + tight market making around 10,000.
        - Take mispriced orders (ask < fair or bid > fair) aggressively.
        - Post passive bid/ask inside the wide (~16 tick) spread.
        - Skew quotes to stay inventory-neutral and flip both sides.
        """
        fair = ASH_FAIR
        orders: List[Order] = []
        pos = position

        # ── Aggressive takes ──────────────────────────────────────────────────
        # Buy cheap: lift asks priced below fair - 1
        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair - 1:
                break
            qty = min(-depth.sell_orders[ask_px], POSITION_LIMIT - pos)
            if qty <= 0:
                break
            orders.append(Order("ASH_COATED_OSMIUM", ask_px, qty))
            pos += qty

        # Sell dear: hit bids priced above fair + 1
        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px <= fair + 1:
                break
            qty = min(depth.buy_orders[bid_px], POSITION_LIMIT + pos)
            if qty <= 0:
                break
            orders.append(Order("ASH_COATED_OSMIUM", bid_px, -qty))
            pos -= qty

        # ── Passive quoting with inventory skew ───────────────────────────────
        # Skew: lean quotes against current inventory to stay balanced
        skew = -(position / POSITION_LIMIT) * 3   # ±3 ticks max

        my_bid = math.floor(fair - 3 + skew)
        my_ask = math.ceil(fair + 3 + skew)

        best_bid = self._best_bid(depth)
        best_ask = self._best_ask(depth)

        # Don't cross existing book
        if best_ask is not None and my_bid >= best_ask:
            my_bid = best_ask - 1
        if best_bid is not None and my_ask <= best_bid:
            my_ask = best_bid + 1

        # Ensure bid < ask
        if my_bid >= my_ask:
            my_bid = math.floor(fair) - 1
            my_ask = math.ceil(fair) + 1

        # Quote size: more aggressive when inventory is balanced
        quote_qty = max(5, 15 - abs(position) // 4)

        buy_cap  = POSITION_LIMIT - pos
        sell_cap = POSITION_LIMIT + pos

        if buy_cap > 0:
            orders.append(Order("ASH_COATED_OSMIUM", my_bid,  min(quote_qty, buy_cap)))
        if sell_cap > 0:
            orders.append(Order("ASH_COATED_OSMIUM", my_ask, -min(quote_qty, sell_cap)))

        return orders

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # Recover persisted day (state.day is available in Prosperity)
        saved: dict = {}
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
            except Exception:
                pass

        day = getattr(state, 'day', saved.get('day', 0))
        saved['day'] = day

        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            pos = state.position.get(product, 0)

            if product == "INTARIAN_PEPPER_ROOT":
                result[product] = self._trade_pepper(
                    depth, pos, day, state.timestamp
                )
            elif product == "ASH_COATED_OSMIUM":
                result[product] = self._trade_ash(depth, pos)

        conversions = 0
        return result, conversions, json.dumps(saved)