from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

# =============================================================================
# PROSPERITY 4 — TUTORIAL ROUND TRADER
# round_0_samik_v3.py
#
# Key findings from backtesting:
#   EMERALDS: limit=80 helps, keep quotes at 9993/10007, size=8
#   TOMATOES: limit=50 is actually better (limit=80 leads to bad inventory buildup
#             as price drifts), NO aggressive takes (EMA lag makes them costly),
#             pure passive spread=4 around EMA, alpha=0.05, size=6
#
# Total backtest: ~23,442 vs original v1 baseline of ~22,928
# =============================================================================

POSITION_LIMIT = {"EMERALDS": 80, "TOMATOES": 50}

class Trader:

    def run(self, state: TradingState):

        # Load EMA from last tick
        memory = json.loads(state.traderData) if state.traderData else {}
        ema = memory.get("ema", None)
        result = {}

        for prod in state.order_depths:
            od = state.order_depths[prod]
            if not od.buy_orders or not od.sell_orders:
                continue

            orders: List[Order] = []
            p = state.position.get(prod, 0)
            lim = POSITION_LIMIT[prod]
            bb = max(od.buy_orders)
            ba = min(od.sell_orders)
            mid = (bb + ba) / 2

            # =================================================================
            # EMERALDS — fixed fair value at 10,000
            # =================================================================
            if prod == "EMERALDS":

                # Aggressive: take anything strictly better than fair value
                for ap in sorted(od.sell_orders):
                    if ap >= 10000:
                        break
                    cb = lim - p
                    if cb <= 0:
                        break
                    q = min(-od.sell_orders[ap], cb)
                    orders.append(Order(prod, ap, q))
                    p += q

                for bp in sorted(od.buy_orders, reverse=True):
                    if bp <= 10000:
                        break
                    cs = lim + p
                    if cs <= 0:
                        break
                    q = min(od.buy_orders[bp], cs)
                    orders.append(Order(prod, bp, -q))
                    p -= q

                # Passive: quote 1 tick inside bot walls (9992 bid / 10008 ask)
                buy_p  = 9993
                sell_p = 10007

                # Inventory skew — unwind if position gets one-sided
                if p > 20:
                    sell_p -= 1   # sell slightly cheaper to move inventory
                elif p < -20:
                    buy_p += 1    # buy slightly more expensive to cover short

                cb = lim - p
                cs = lim + p
                if cb > 0:
                    orders.append(Order(prod, buy_p,  min(8, cb)))
                if cs > 0:
                    orders.append(Order(prod, sell_p, -min(8, cs)))

            # =================================================================
            # TOMATOES — drifting asset, EMA tracks fair value
            # =================================================================
            else:
                # Update EMA — alpha=0.05 means ~20-tick memory
                ema = 0.05 * mid + 0.95 * ema if ema else mid
                fv  = ema

                # Pure passive market making — NO aggressive takes
                # (EMA lags on a drifting asset, so "cheap vs EMA" often isn't
                # actually cheap — aggressive buys into the drift lose money)
                buy_p  = int(fv) - 4
                sell_p = int(fv) + 4

                # Inventory skew
                if p > 20:
                    sell_p -= 1
                elif p < -20:
                    buy_p += 1

                cb = lim - p
                cs = lim + p
                if cb > 0:
                    orders.append(Order(prod, buy_p,  min(6, cb)))
                if cs > 0:
                    orders.append(Order(prod, sell_p, -min(6, cs)))

            result[prod] = orders

        memory["ema"] = ema
        return result, 0, json.dumps(memory)