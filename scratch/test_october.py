from src.auto_engine import compute_position_size, usd_to_qar, qar_to_usd

equity_usd = 20000.0 / 3.64
print(f"Starting Equity: ${equity_usd:.2f} (20,000 QAR)")
cash_usd = equity_usd

signals = [
    {"ticker": "ANET", "price": 205.73, "stop": 192.83, "target": 226.93, "live": 207.35},
    {"ticker": "DELL", "price": 535.66, "stop": 486.37, "target": 621.62, "live": 562.52},
    {"ticker": "AAPL", "price": 329.85, "stop": 318.97, "target": 351.60, "live": 333.69},
    {"ticker": "ETN",  "price": 432.66, "stop": 410.93, "target": 472.23, "live": 436.11},
]

deployed = 0.0
floating = 0.0
for s in signals:
    sizing = compute_position_size(equity_usd, cash_usd, s["price"], s["stop"], s["target"])
    sh = sizing["shares"]
    cost = sh * s["price"]
    cash_usd -= cost
    deployed += cost
    pnl = sh * (s["live"] - s["price"])
    floating += pnl
    sym = s["ticker"]
    pr = s["price"]
    lv = s["live"]
    print(f"{sym}: {sh} sh @ ${pr:.2f} | Cost: ${cost:.2f} | Live: ${lv:.2f} | PnL: +${pnl:.2f}")

print(f"Total Deployed: ${deployed:.2f} ({usd_to_qar(deployed):.0f} QAR)")
print(f"Cash Left: ${cash_usd:.2f} ({usd_to_qar(cash_usd):.0f} QAR)")
print(f"Floating PnL: +${floating:.2f} (+{usd_to_qar(floating):.0f} QAR)")
print(f"Total Equity: ${(equity_usd + floating):.2f} ({usd_to_qar(equity_usd + floating):.0f} QAR)")
print(f"ROI: +{(floating / equity_usd)*100:.2f}%")
