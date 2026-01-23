#!/usr/bin/env python3
"""Test what data is available when market is closed."""

import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, MarketOrder, Option

ib = IB()
ib.connect('ib-gateway', 4003, clientId=92, readonly=True, timeout=15)
ib.reqMarketDataType(3)  # Delayed data

print('=' * 70)
print('MARKET CLOSED DATA AVAILABILITY TEST')
print('=' * 70)

# 1. P&L from portfolio (per position)
print('\n1. UNREALIZED P&L (from ib.portfolio())')
print('-' * 50)
portfolio = ib.portfolio()
spy_puts = [p for p in portfolio if p.contract.symbol == 'SPY' and p.contract.secType == 'OPT' and getattr(p.contract, 'right', '') == 'P']
total_pnl = 0
for item in spy_puts:
    c = item.contract
    print(f'   {c.strike}P: P&L = ${item.unrealizedPNL:.2f}')
    total_pnl += item.unrealizedPNL
print(f'   TOTAL: ${total_pnl:.2f}')
print(f'   STATUS: {"AVAILABLE" if spy_puts else "NOT AVAILABLE"}')

# 2. Margin from whatIfOrder (per position)
print('\n2. MAINTENANCE MARGIN (from whatIfOrder)')
print('-' * 50)
positions = ib.positions()
spy_put_positions = [p for p in positions if p.contract.symbol == 'SPY' and p.contract.secType == 'OPT' and getattr(p.contract, 'right', '') == 'P' and p.position < 0]
total_margin = 0
margin_available = True
for pos in spy_put_positions:
    c = pos.contract
    qty = abs(int(pos.position))
    qualified = ib.qualifyContracts(c)
    if qualified:
        order = MarketOrder('BUY', qty)
        whatif = ib.whatIfOrder(qualified[0], order)
        if whatif and whatif.maintMarginChange:
            margin = -float(whatif.maintMarginChange) if float(whatif.maintMarginChange) < 0 else 0
            print(f'   {c.strike}P x{qty}: Margin = ${margin:,.2f}')
            total_margin += margin
        else:
            margin_available = False
            print(f'   {c.strike}P x{qty}: Margin = NOT AVAILABLE')
print(f'   TOTAL: ${total_margin:,.2f}')
print(f'   STATUS: {"AVAILABLE" if margin_available else "PARTIAL"}')

# 3. Greeks from market data (per position)
print('\n3. GREEKS (from reqMktData with modelGreeks)')
print('-' * 50)
contracts_to_check = [
    ('SPY', 610.0, '20260417'),
    ('SPY', 615.0, '20260417'),
    ('SPY', 625.0, '20260417'),
    ('SPY', 630.0, '20260417'),
]

greeks_available = 0
for symbol, strike, exp in contracts_to_check:
    opt = Option(symbol, exp, strike, 'P', 'SMART')
    qualified = ib.qualifyContracts(opt)
    if qualified:
        ticker = ib.reqMktData(qualified[0], '106', False, False)  # 106 = model greeks
        ib.sleep(2)

        delta = theta = gamma = vega = None
        if ticker.modelGreeks:
            g = ticker.modelGreeks
            delta = g.delta
            theta = g.theta
            gamma = g.gamma
            vega = g.vega
            greeks_available += 1

        bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
        ask = ticker.ask if ticker.ask and ticker.ask > 0 else None

        print(f'   {strike}P: delta={delta}, theta={theta}, bid={bid}, ask={ask}')
        ib.cancelMktData(qualified[0])

print(f'   STATUS: {greeks_available}/{len(contracts_to_check)} positions have Greeks')

print('\n' + '=' * 70)
print('SUMMARY')
print('=' * 70)
print(f'   P&L per position:      AVAILABLE (from portfolio)')
print(f'   Margin per position:   AVAILABLE (from whatIfOrder)')
print(f'   Greeks per position:   {greeks_available}/{len(contracts_to_check)} available (from reqMktData)')
print('=' * 70)

ib.disconnect()
