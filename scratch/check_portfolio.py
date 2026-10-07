import os
os.environ['DATABASE_URL'] = 'postgresql://neondb_owner:npg_SkMPE1iI4xHd@ep-proud-dust-b24lkcj7-pooler.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require'
from src.auto_engine import get_auto_portfolio_summary

data = get_auto_portfolio_summary()
summ = data['summary']
print('Portfolio Summary:')
print('Deposited:', summ['total_deposited_qar'], 'QAR ($' + str(summ['total_deposited_usd']) + ')')
print('Cash:', summ['cash_available_qar'], 'QAR ($' + str(summ['cash_available_usd']) + ')')
print('Equity:', summ['total_equity_qar'], 'QAR ($' + str(summ['total_equity_usd']) + ')')
print('Realized PnL:', summ['realized_pnl_qar'], 'QAR ($' + str(summ['realized_pnl_usd']) + ')')
print('Floating PnL:', summ['unrealized_pnl_qar'], 'QAR ($' + str(summ['unrealized_pnl_usd']) + ')')
print('Net Profit:', summ['net_profit_qar'], 'QAR ($' + str(summ['net_profit_usd']) + ')')

print('\nActive Positions (' + str(len(data['active_positions'])) + '):')
for p in data['active_positions']:
    print(p['ticker'], str(p['remaining_shares']) + ' shs', 'entry:', p['entry_date'], '@ $' + str(p['entry_price']), 'live: $' + str(p['current_price']), 'pnl: $' + str(p['unrealized_pnl_usd']), '(' + str(p['unrealized_pnl_pct']) + '%)')

print('\nClosed Positions (' + str(len(data['closed_positions'])) + '):')
for p in data['closed_positions']:
    print(p['ticker'], str(p['shares']) + ' shs', 'entry:', p['entry_date'], 'exit:', p['exit_date'], '@ $' + str(p['exit_price']), 'realized: $' + str(p['realized_pnl_usd']), 'reason:', p['exit_reason'])
