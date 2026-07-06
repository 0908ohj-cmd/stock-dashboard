import pandas as pd
import plotly.graph_objects as go


def calc_intraday_strength(
    stock_5m: pd.DataFrame,
    index_5m: pd.DataFrame,
) -> dict:
    """찐반등 날 5분봉 인트라데이 강도 지표."""
    if stock_5m.empty or index_5m.empty:
        return {}

    idx_peak_time = index_5m['High'].idxmax()

    idx_after = index_5m[index_5m.index >= idx_peak_time]
    stk_after = stock_5m[stock_5m.index >= idx_peak_time]

    if len(idx_after) >= 2 and len(stk_after) >= 2:
        idx_peak_ret = (float(idx_after['Close'].iloc[-1]) / float(idx_after['Close'].iloc[0]) - 1) * 100
        stk_peak_ret = (float(stk_after['Close'].iloc[-1]) / float(stk_after['Close'].iloc[0]) - 1) * 100
        excess_after_peak = round(stk_peak_ret - idx_peak_ret, 2)
    else:
        excess_after_peak = 0.0

    def count_low_breaks(df: pd.DataFrame) -> int:
        return sum(
            1 for i in range(1, len(df))
            if float(df['Low'].iloc[i]) < float(df['Low'].iloc[i - 1])
        )

    def count_high_updates(df: pd.DataFrame) -> int:
        updates, running = 0, float(df['High'].iloc[0])
        for i in range(1, len(df)):
            h = float(df['High'].iloc[i])
            if h > running:
                updates += 1
                running = h
        return updates

    idx_day_high = float(index_5m['High'].max())
    stk_day_high = float(stock_5m['High'].max())
    idx_close    = float(index_5m['Close'].iloc[-1])
    stk_close    = float(stock_5m['Close'].iloc[-1])

    return {
        'index_peak_time':       idx_peak_time,
        'excess_after_peak_pct': excess_after_peak,
        'stock_low_breaks':      count_low_breaks(stock_5m),
        'index_low_breaks':      count_low_breaks(index_5m),
        'stock_high_updates':    count_high_updates(stock_5m),
        'index_high_updates':    count_high_updates(index_5m),
        'stock_close_ratio':     round(stk_close / stk_day_high * 100, 1) if stk_day_high else 0,
        'index_close_ratio':     round(idx_close / idx_day_high * 100, 1) if idx_day_high else 0,
    }


def _filter_regular_hours(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """정규장 시간대만 필터링."""
    if df.empty:
        return df
    t = df.index.time
    if market.startswith('KR'):
        mask = (t >= pd.Timestamp('09:00').time()) & (t <= pd.Timestamp('15:30').time())
    else:
        mask = (t >= pd.Timestamp('09:30').time()) & (t <= pd.Timestamp('16:00').time())
    return df[mask]


def intraday_overlay_chart(
    stock_5m: pd.DataFrame,
    index_5m: pd.DataFrame,
    ticker: str,
    index_name: str,
    jjin_date=None,
    market: str = 'US',
) -> go.Figure:
    """찐반등 날 기준 5일치 누적수익률 오버레이 차트 (정규장만)."""
    fig = go.Figure()

    stock_5m = _filter_regular_hours(stock_5m, market)
    index_5m = _filter_regular_hours(index_5m, market)

    if stock_5m.empty or index_5m.empty:
        fig.update_layout(title='5분봉 데이터 없음 (60일 초과)', template='plotly_dark', height=350)
        return fig

    idx_open   = float(index_5m['Close'].iloc[0])
    stk_open   = float(stock_5m['Close'].iloc[0])
    idx_cumret = (index_5m['Close'] / idx_open - 1) * 100
    stk_cumret = (stock_5m['Close'] / stk_open - 1) * 100

    # 찐반등일 하이라이트
    if jjin_date is not None:
        jjin_ts = pd.Timestamp(jjin_date)
        jjin_data = index_5m[index_5m.index.normalize() == jjin_ts.normalize()]
        if not jjin_data.empty:
            fig.add_vrect(
                x0=jjin_data.index[0], x1=jjin_data.index[-1],
                fillcolor='rgba(255, 214, 0, 0.07)',
                line_width=0,
                annotation_text='찐반등일',
                annotation_position='top left',
                annotation_font=dict(size=10, color='#ffd600'),
            )

    # 날짜 경계 수직선
    dates = index_5m.index.normalize().unique()
    for d in dates[1:]:
        fig.add_vline(
            x=d.timestamp() * 1000,
            line_width=0.6, line_dash='solid', line_color='#2a2a2a',
        )

    # 0선
    fig.add_hline(y=0, line_width=0.8, line_color='#333')

    # 지수 (배경)
    fig.add_trace(go.Scatter(
        x=index_5m.index, y=idx_cumret,
        name=index_name,
        line=dict(color='#546e7a', width=1.5, dash='dot'),
        hovertemplate='%{y:.2f}%<extra>' + index_name + '</extra>',
    ))

    # 종목 (전면)
    last_val = float(stk_cumret.iloc[-1])
    stock_color = '#26c6da' if last_val >= 0 else '#ef5350'
    fig.add_trace(go.Scatter(
        x=stock_5m.index, y=stk_cumret,
        name=ticker,
        line=dict(color=stock_color, width=2),
        fill='tozeroy',
        fillcolor=f'rgba({34 if last_val >= 0 else 239},{198 if last_val >= 0 else 83},{218 if last_val >= 0 else 80},0.06)',
        hovertemplate='%{y:.2f}%<extra>' + ticker + '</extra>',
    ))

    title_date = pd.Timestamp(jjin_date).strftime('%Y-%m-%d') if jjin_date else ''
    fig.update_layout(
        title=dict(
            text=f'<b>{ticker}</b> vs {index_name} &nbsp;·&nbsp; 찐반등일 {title_date} 기준 5일',
            font=dict(size=13),
            x=0,
        ),
        yaxis=dict(
            title='누적수익률 (%)',
            title_font=dict(size=11),
            tickfont=dict(size=10),
            gridcolor='#1e1e1e',
            zeroline=False,
        ),
        xaxis=dict(
            tickfont=dict(size=10),
            gridcolor='#1e1e1e',
        ),
        template='plotly_dark',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='#0e1117',
        height=360,
        margin=dict(l=40, r=20, t=45, b=20),
        legend=dict(
            orientation='h', y=1.08, x=1, xanchor='right',
            font=dict(size=11),
            bgcolor='rgba(0,0,0,0)',
        ),
        hovermode='x unified',
    )
    return fig
