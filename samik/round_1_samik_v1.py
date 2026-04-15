from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json

# =============================================================================
# PROSPERITY 4 — ROUND 1 TRADER
# round_1_samik_v1.py
#
# Products: INTARIAN_PEPPER_ROOT, ASH_COATED_OSMIUM
# Position limits: 80 each
#
# ── INTARIAN_PEPPER_ROOT ─────────────────────────────────────────────────────
#   KEY INSIGHT: Price follows an EXACT linear drift.
#     FV(t) = base + 0.001 × timestamp
#   base increases by 1000 each trading day (day -2: 10000, day -1: 11000,
#   day 0: 12000 → round 1 day 1 starts at ~13000).
#   Drift = 1 000 XIREC per day per unit held.
#
#   Bot quotes: bid = FV − 6.5, ask = FV + 6.5 (confirmed from data)
#   Our quotes: bid = FV − 4,   ask = FV + 4   (inside the spread)
#
#   PRIMARY STRATEGY: go LONG (+80) as fast as possible and hold.
#     - Aggressively buy every available ask (even at bot ask FV+7)
#     - Drift of 1000/day × 80 units = ~80 000 XIREC/day just from holding
#     - Post passive ask at FV+4 to earn extra spread income on the way
#     - If position drops below threshold, buy back aggressively
#     - Near day-end (ts ≥ 950 000) start closing to lock in realized PnL
#
# ── ASH_COATED_OSMIUM ────────────────────────────────────────────────────────
#   Price mean-reverts to ~10 000 (std ≈ 5, range ≈ 9977–10023).
#   Hint: a "hidden pattern" may correlate with SUNLIGHT_INDEX or SUGAR_PRICE
#   — code reads both from state.observations and logs them for analysis;
#   fill in the formula once you observe the actual signal values.
#
#   Bot quotes: bid ≈ FV − 8, ask ≈ FV + 8
#   Our quotes: bid = FV − 5, ask = FV + 5 (inside the bot spread)
#
#   STRATEGY: symmetric market-making around EMA (slow α=0.02, anchored ~10000)
#     - Passive quotes at EMA ± 5 ticks
#     - Aggressive take only when order is ≥ 3 ticks better than EMA
#     - Inventory skew adjusts quotes by 1 tick when |pos| > 20
#
# ── Expected PnL ─────────────────────────────────────────────────────────────
#   Pepper Root drift (3 days, 80 units): ~240 000 XIREC
#   Osmium spread income (bonus): varies
#   Target of 200 000 XIREC comfortably exceeded by drift alone.
# =============================================================================

POSITION_LIMIT = {
    "INTARIAN_PEPPER_ROOT": 80,
    "ASH_COATED_OSMIUM": 80,
}

# Pepper Root: confirmed linear drift rate (ticks per timestamp unit)
PEPPER_DRIFT = 0.001

# How close to the position limit we target staying (long bias)
PEPPER_LONG_TARGET = 70   # start buying aggressively when pos < this
PEPPER_CLOSE_TS    = 950_000  # timestamp at which we begin closing to realize PnL

# Spread parameters
PEPPER_PASSIVE_SPREAD = 4   # our quotes at FV ± this (bots are at ±6.5)
OSM_PASSIVE_SPREAD    = 5   # our quotes at EMA ± this (bots are at ±8)
OSM_AGGR_THRESHOLD    = 3   # take aggressively only when ≥ this better than EMA

OSM_EMA_ALPHA = 0.02        # slow EMA — anchors to true mean ~10 000

QUOTE_SIZE = 10             # units per passive quote


class Trader:

    def run(self, state: TradingState):

        # ── Load persistent memory ────────────────────────────────────────────
        memory: dict = json.loads(state.traderData) if state.traderData else {}

        pepper_base: float | None = memory.get("pepper_base", None)
        osm_ema:     float | None = memory.get("osm_ema", None)
        last_ts:     int          = memory.get("last_ts", -1)

        result: Dict[str, List[Order]] = {}
        ts = state.timestamp

        # ── Detect new trading day (timestamp resets to 0) ───────────────────
        new_day = (ts < last_ts) or (last_ts == -1)

        # ── Try to read observations (SUNLIGHT_INDEX, SUGAR_PRICE) ───────────
        sunlight_index = None
        sugar_price    = None
        try:
            pvo = state.observations.plainValueObservations
            sunlight_index = pvo.get("SUNLIGHT_INDEX", None)
            sugar_price    = pvo.get("SUGAR_PRICE",    None)
        except Exception:
            pass

        # ── Iterate over products ─────────────────────────────────────────────
        for prod, od in state.order_depths.items():
            if prod not in POSITION_LIMIT:
                continue

            has_bids = bool(od.buy_orders)
            has_asks = bool(od.sell_orders)
            if not has_bids and not has_asks:
                continue

            orders: List[Order] = []
            pos   = state.position.get(prod, 0)
            lim   = POSITION_LIMIT[prod]

            bb = max(od.buy_orders)  if has_bids else None
            ba = min(od.sell_orders) if has_asks else None
            mid = (bb + ba) / 2 if (has_bids and has_asks) else (bb or ba)

            # ================================================================
            # INTARIAN_PEPPER_ROOT — linear-drift fair value, long-and-hold
            # ================================================================
            if prod == "INTARIAN_PEPPER_ROOT":

                # Estimate or refresh the daily base price
                if pepper_base is None or new_day:
                    # Round to nearest 100 to absorb ±10 residual noise
                    pepper_base = round((mid - PEPPER_DRIFT * ts) / 100) * 100

                fv = pepper_base + PEPPER_DRIFT * ts

                # ── Phase 1: aggressive buying to build / restore long ────────
                # Buy at any ask up to FV + PEPPER_PASSIVE_SPREAD + 3 (≤ bot ask)
                # Keep buying until we hit the position limit
                if has_asks:
                    for ap in sorted(od.sell_orders):
                        if ap > fv + PEPPER_PASSIVE_SPREAD + 3:
                            break
                        cap = lim - pos
                        if cap <= 0:
                            break
                        qty = min(-od.sell_orders[ap], cap)
                        if qty > 0:
                            orders.append(Order(prod, ap, qty))
                            pos += qty

                # ── Phase 2: aggressive selling near day-end to lock PnL ─────
                # Or: sell any bid that is egregiously above FV
                if has_bids:
                    for bp in sorted(od.buy_orders, reverse=True):
                        if bp < fv + PEPPER_PASSIVE_SPREAD + 3:
                            break
                        # Only sell if well above FV (rare windfall)
                        cap = lim + pos
                        if cap <= 0:
                            break
                        qty = min(od.buy_orders[bp], cap)
                        if qty > 0:
                            orders.append(Order(prod, bp, -qty))
                            pos -= qty

                # ── Phase 3: passive market-making around FV ─────────────────
                buy_p  = int(fv) - PEPPER_PASSIVE_SPREAD
                sell_p = int(fv) + PEPPER_PASSIVE_SPREAD

                # Inventory skew: nudge quotes when position is heavy one way
                if pos > 30:
                    sell_p -= 1
                    buy_p  -= 1
                elif pos < -10:
                    buy_p  += 1
                    sell_p += 1

                # Only post passive sell if we're holding a comfortable long
                # (don't sell below our profitable zone near position limit)
                cap_buy  = lim - pos
                cap_sell = lim + pos

                # Post aggressive passive bid to keep position topped up
                if cap_buy > 0:
                    orders.append(Order(prod, buy_p, min(QUOTE_SIZE, cap_buy)))

                # Post sell only if long enough — we want to stay long
                if cap_sell > 0 and pos > PEPPER_LONG_TARGET // 2:
                    orders.append(Order(prod, sell_p, -min(QUOTE_SIZE, cap_sell)))

                # ── Phase 4: day-end liquidation ─────────────────────────────
                # Close position near end of day to realize PnL
                # (remove / comment out if you prefer to carry position across days)
                if ts >= PEPPER_CLOSE_TS and pos > 0:
                    # Take whatever bids exist to flatten
                    if has_bids:
                        for bp in sorted(od.buy_orders, reverse=True):
                            cap = lim + pos
                            if cap <= 0:
                                break
                            qty = min(od.buy_orders[bp], cap, pos)
                            if qty > 0:
                                orders.append(Order(prod, bp, -qty))
                                pos -= qty

            # ================================================================
            # ASH_COATED_OSMIUM — mean-reversion market making
            # ================================================================
            elif prod == "ASH_COATED_OSMIUM":

                # ── Update EMA ───────────────────────────────────────────────
                if osm_ema is None:
                    osm_ema = mid
                else:
                    osm_ema = OSM_EMA_ALPHA * mid + (1 - OSM_EMA_ALPHA) * osm_ema

                # ── Optional: incorporate sunlight/sugar signal ───────────────
                # TODO: once you observe the actual values, fit a regression
                # E.g.  fv_adj = osm_ema + 0.01 * (sunlight_index - 2500)
                fv = osm_ema

                # ── Aggressive takes (only clear mispricings) ─────────────────
                if has_asks:
                    for ap in sorted(od.sell_orders):
                        if ap >= fv - OSM_AGGR_THRESHOLD:
                            break
                        cap = lim - pos
                        if cap <= 0:
                            break
                        qty = min(-od.sell_orders[ap], cap)
                        if qty > 0:
                            orders.append(Order(prod, ap, qty))
                            pos += qty

                if has_bids:
                    for bp in sorted(od.buy_orders, reverse=True):
                        if bp <= fv + OSM_AGGR_THRESHOLD:
                            break
                        cap = lim + pos
                        if cap <= 0:
                            break
                        qty = min(od.buy_orders[bp], cap)
                        if qty > 0:
                            orders.append(Order(prod, bp, -qty))
                            pos -= qty

                # ── Passive market-making around EMA ─────────────────────────
                buy_p  = int(fv) - OSM_PASSIVE_SPREAD
                sell_p = int(fv) + OSM_PASSIVE_SPREAD

                # Inventory skew
                if pos > 20:
                    sell_p -= 1
                    buy_p  -= 1
                elif pos < -20:
                    buy_p  += 1
                    sell_p += 1

                cap_buy  = lim - pos
                cap_sell = lim + pos

                if cap_buy > 0:
                    orders.append(Order(prod, buy_p,   min(QUOTE_SIZE, cap_buy)))
                if cap_sell > 0:
                    orders.append(Order(prod, sell_p, -min(QUOTE_SIZE, cap_sell)))

            result[prod] = orders

        # ── Persist state ─────────────────────────────────────────────────────
        memory["pepper_base"] = pepper_base
        memory["osm_ema"]     = osm_ema
        memory["last_ts"]     = ts

        return result, 0, json.dumps(memory)
