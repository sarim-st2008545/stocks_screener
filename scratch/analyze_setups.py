import psycopg2

conn_str = 'postgresql://neondb_owner:npg_SkMPE1iI4xHd@ep-proud-dust-b24lkcj7-pooler.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require'
conn = psycopg2.connect(conn_str)
cur = conn.cursor()
cur.execute('SELECT id, system, ticker, signal_date, price, stop_loss, target_price, status, outcome_pnl_pct, outcome_date FROM signals ORDER BY id;')
signals = cur.fetchall()

# Latest market prices as of Friday close (Oct 2, 2026):
current_prices = {
    'AAPL': 333.69,
    'ETN': 436.11,
    'DELL': 562.52,
    'CCJ': 85.18,
    'ZETA': 32.63,
    'BOX': 34.49,
    'SOUN': 5.84,
    'CRDO': 25.10, # Target Hit outcome in DB
    'NVDA': 233.95,
    'ANET': 207.35,
    'MU': 1074.89
}

total_invested = 0.0
total_current_val = 0.0
wins = 0
losses = 0
ties = 0

win_amounts = []
loss_amounts = []

rows = []

for s in signals:
    sid, system, ticker, sdate, entry_p, sl, tp, status, outcome_pct, odate = s
    cur_p = current_prices.get(ticker, entry_p)
    
    if status == 'HIT_TARGET' and outcome_pct is not None:
        pnl_pct = float(outcome_pct)
        eval_price = tp
    else:
        pnl_pct = ((cur_p - entry_p) / entry_p) * 100.0
        eval_price = cur_p
        
    invested = 200.0
    shares = invested / entry_p
    current_val = invested * (1.0 + pnl_pct / 100.0)
    pnl_dollar = current_val - invested
    
    total_invested += invested
    total_current_val += current_val
    
    if pnl_dollar > 0.001:
        wins += 1
        win_amounts.append(pnl_dollar)
        res_tag = "WIN"
    elif pnl_dollar < -0.001:
        losses += 1
        loss_amounts.append(abs(pnl_dollar))
        res_tag = "LOSS"
    else:
        ties += 1
        res_tag = "EVEN"
        
    rows.append({
        'id': sid,
        'system': system,
        'ticker': ticker,
        'date': str(sdate),
        'entry': entry_p,
        'current': eval_price,
        'stop': sl,
        'target': tp,
        'pnl_pct': pnl_pct,
        'pnl_dollar': pnl_dollar,
        'current_val': current_val,
        'status': status,
        'res': res_tag
    })

total_pnl_dollar = total_current_val - total_invested
total_pnl_pct = (total_pnl_dollar / total_invested) * 100.0
gross_wins = sum(win_amounts)
gross_losses = sum(loss_amounts)
pf = (gross_wins / gross_losses) if gross_losses > 0 else 999.0
wr = (wins / (wins + losses)) * 100.0 if (wins + losses) > 0 else 0.0

avg_win_dollar = gross_wins / len(win_amounts) if win_amounts else 0.0
avg_loss_dollar = gross_losses / len(loss_amounts) if loss_amounts else 0.0
avg_win_pct = (avg_win_dollar / 200.0) * 100.0
avg_loss_pct = (avg_loss_dollar / 200.0) * 100.0
payoff_ratio = (avg_win_dollar / avg_loss_dollar) if avg_loss_dollar > 0 else 999.0
ev_per_trade_pct = ((wr / 100.0) * avg_win_pct) - (((100.0 - wr) / 100.0) * avg_loss_pct)

print("=== SIGNAL BY SIGNAL BREAKDOWN ===")
for r in rows:
    print(f"ID #{r['id']:02d} | {r['system']:8s} | {r['ticker']:5s} | {r['date']} | In: ${r['entry']:7.2f} | Out/Cur: ${r['current']:7.2f} | PnL: {r['pnl_pct']:+6.2f}% | PnL $: ${r['pnl_dollar']:+6.2f} | End Val: ${r['current_val']:6.2f} | {r['res']}")

print("\n=== SUMMARY METRICS ===")
print(f"Total Signals: {len(signals)}")
print(f"Total Capital Allocated: ${total_invested:,.2f} ({total_invested * 3.64:,.2f} QAR)")
print(f"Current Total Value:     ${total_current_val:,.2f} ({total_current_val * 3.64:,.2f} QAR)")
print(f"Net Profit Earned:       ${total_pnl_dollar:+,.2f} ({total_pnl_dollar * 3.64:+,.2f} QAR)")
print(f"Total Return on Capital: {total_pnl_pct:+.2f}%")
print(f"Wins: {wins} | Losses: {losses} | Even: {ties}")
print(f"Win Rate (excluding even): {wr:.1f}%")
print(f"Win Rate (of all 22):      {(wins / len(signals)) * 100.0:.1f}%")
print(f"Gross Profit from Wins:    ${gross_wins:,.2f}")
print(f"Gross Loss from Losses:    ${gross_losses:,.2f}")
print(f"Profit Factor (PF):        {pf:.2f}")
print(f"Payoff Ratio (Win/Loss):   {payoff_ratio:.2f} : 1")
print(f"Average Win:               +${avg_win_dollar:.2f} (+{avg_win_pct:.2f}%)")
print(f"Average Loss:              -${avg_loss_dollar:.2f} (-{avg_loss_pct:.2f}%)")
print(f"Expectancy Per Trade:      +{ev_per_trade_pct:.2f}% (${(ev_per_trade_pct/100)*200:+.2f})")

conn.close()
