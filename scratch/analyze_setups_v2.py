import psycopg2

conn_str = 'postgresql://neondb_owner:npg_SkMPE1iI4xHd@ep-proud-dust-b24lkcj7-pooler.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require'
conn = psycopg2.connect(conn_str)
cur = conn.cursor()
cur.execute('SELECT id, system, ticker, signal_date, price, stop_loss, target_price, status, outcome_pnl_pct, outcome_date FROM signals ORDER BY id;')
signals = cur.fetchall()

# Latest market close prices (Oct 2, 2026):
market_prices = {
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

def analyze(mode="current_market"):
    total_invested = 0.0
    total_current_val = 0.0
    wins = 0
    losses = 0
    ties = 0
    win_amounts = []
    loss_amounts = []
    trade_list = []

    for s in signals:
        sid, system, ticker, sdate, entry_p, sl, tp, status, outcome_pct, odate = s
        mkt_p = market_prices.get(ticker, entry_p)
        
        if mode == "rules_target":
            # If hit target (CRDO, SOUN, CCJ, BOX all crossed TP)
            if status == 'HIT_TARGET':
                eval_p = tp
            elif system == 'galaxy' and tp is not None and mkt_p >= tp:
                eval_p = tp
            else:
                eval_p = mkt_p
        else:
            # Current open market price (except CRDO which closed at TP in DB)
            if status == 'HIT_TARGET' and outcome_pct is not None:
                eval_p = tp
            else:
                eval_p = mkt_p

        pnl_pct = ((eval_p - entry_p) / entry_p) * 100.0
        invested = 200.0
        shares = invested / entry_p
        val = invested * (1.0 + pnl_pct / 100.0)
        pnl_dlr = val - invested
        
        total_invested += invested
        total_current_val += val
        
        if pnl_dlr > 0.001:
            wins += 1
            win_amounts.append(pnl_dlr)
            res = "WIN"
        elif pnl_dlr < -0.001:
            losses += 1
            loss_amounts.append(abs(pnl_dlr))
            res = "LOSS"
        else:
            ties += 1
            res = "EVEN"
            
        trade_list.append({
            'id': sid, 'system': system, 'ticker': ticker, 'date': str(sdate),
            'entry': entry_p, 'stop': sl, 'target': tp, 'eval': eval_p,
            'pnl_pct': pnl_pct, 'pnl_dlr': pnl_dlr, 'val': val, 'res': res
        })

    gross_win = sum(win_amounts)
    gross_loss = sum(loss_amounts)
    net_pnl = total_current_val - total_invested
    ret_pct = (net_pnl / total_invested) * 100.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    wr = (wins / (wins + losses)) * 100.0 if (wins + losses) > 0 else 0.0
    payoff = (gross_win / len(win_amounts)) / (gross_loss / len(loss_amounts)) if loss_amounts and win_amounts else 999.0
    ev_pct = ((wr / 100.0) * (gross_win / len(win_amounts) / 200.0 * 100.0)) - (((100.0 - wr) / 100.0) * (gross_loss / len(loss_amounts) / 200.0 * 100.0)) if loss_amounts and win_amounts else 0.0

    return {
        'total_invested': total_invested,
        'total_val': total_current_val,
        'net_pnl': net_pnl,
        'ret_pct': ret_pct,
        'wins': wins, 'losses': losses, 'ties': ties,
        'wr': wr, 'pf': pf, 'payoff': payoff,
        'gross_win': gross_win, 'gross_loss': gross_loss,
        'avg_win_dlr': gross_win / len(win_amounts) if win_amounts else 0,
        'avg_loss_dlr': gross_loss / len(loss_amounts) if loss_amounts else 0,
        'ev_pct': ev_pct,
        'trades': trade_list
    }

res_mkt = analyze("current_market")
print(f"=== CURRENT MARKET EVALUATION ===")
print(f"Total Invested: ${res_mkt['total_invested']:,.2f} ({res_mkt['total_invested']*3.64:,.2f} QAR)")
print(f"Current Value:  ${res_mkt['total_val']:,.2f} ({res_mkt['total_val']*3.64:,.2f} QAR)")
print(f"Net Profit:     ${res_mkt['net_pnl']:+,.2f} ({res_mkt['net_pnl']*3.64:+,.2f} QAR)")
print(f"Win Rate:       {res_mkt['wr']:.1f}% ({res_mkt['wins']} Wins / {res_mkt['losses']} Loss / {res_mkt['ties']} Even)")
print(f"Profit Factor:  {res_mkt['pf']:.2f}")
print(f"Payoff Ratio:   {res_mkt['payoff']:.2f} : 1")
print(f"Avg Win:        +${res_mkt['avg_win_dlr']:.2f} (+{(res_mkt['avg_win_dlr']/200)*100:.2f}%)")
print(f"Avg Loss:       -${res_mkt['avg_loss_dlr']:.2f} (-{(res_mkt['avg_loss_dlr']/200)*100:.2f}%)")

conn.close()
