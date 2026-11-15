import pandas as pd
import plotly.graph_objects as go


def _filter_regular_hours(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """정규장 시간대만 필터링 (타임존 변환 후 비교)."""
    if df.empty:
        return df
    idx = df.index
    if idx.tz is not None:
        tz = 'Asia/Seoul' if market.startswith('KR') else 'America/New_York'
        idx = idx.tz_convert(tz)
    t = idx.time
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
    stock_name: str = '',
) -> go.Figure:
    """찐반등 날 기준 5일치 누적수익률 오버레이 차트 (정규장만)."""
    fig = go.Figure()

    stock_5m = _filter_regular_hours(stock_5m, market)
    index_5m = _filter_regular_hours(index_5m, market)

    if stock_5m.empty or index_5m.empty:
        # 여기 오는 건 받은 봉에 정규장 시간대가 없을 때다 — '60일 초과'가 아니다 (#39 M14)
        fig.update_layout(title='5분봉 데이터 없음 (정규장 시간대 봉 없음)', template='plotly_dark', height=350)
        return fig

    # UTC → 현지 시간으로 변환 (x축 시간 표시 정확하게)
    tz = 'Asia/Seoul' if market.startswith('KR') else 'America/New_York'
    if stock_5m.index.tz is not None:
        stock_5m = stock_5m.copy()
        stock_5m.index = stock_5m.index.tz_convert(tz)
        index_5m = index_5m.copy()
        index_5m.index = index_5m.index.tz_convert(tz)

    idx_open   = float(index_5m['Close'].iloc[0])
    stk_open   = float(stock_5m['Close'].iloc[0])
    idx_cumret = (index_5m['Close'] / idx_open - 1) * 100
    stk_cumret = (stock_5m['Close'] / stk_open - 1) * 100

    # 찐반등일 하이라이트
    if jjin_date is not None:
        jjin_ts = pd.Timestamp(jjin_date)
        jjin_data = index_5m[index_5m.index.normalize() == jjin_ts.normalize().tz_localize(tz)]
        if not jjin_data.empty:
            fig.add_vrect(
                x0=jjin_data.index[0], x1=jjin_data.index[-1],
                fillcolor='rgba(255, 214, 0, 0.07)',
                line_width=0,
                annotation_text='찐반등일',
                annotation_position='top left',
                annotation_font=dict(size=10, color='#ffd600'),
            )

    # 날짜 경계 — 정규장 시작 시각에 수직선. 데이터와 같은 tz 붙은 Timestamp를 그대로 넘긴다:
    # plotly.js는 날짜의 벽시계 시각으로 그리는데 epoch ms(UTC)를 넘기면 KR은 9시간(접힌 장외 구간에 숨음),
    # US는 5시간(14:30에 표시) 어긋났다 (#44 L2)
    open_time = '09:00' if market.startswith('KR') else '09:30'
    dates = index_5m.index.normalize().unique()
    for d in dates:
        open_ts = pd.Timestamp(f'{d.date()} {open_time}', tz=tz)
        fig.add_vline(
            x=open_ts,
            line_width=0.8, line_dash='dot', line_color='#3a3a3a',
        )

    fig.add_hline(y=0, line_width=0.8, line_color='#333')

    # 지수
    fig.add_trace(go.Scatter(
        x=index_5m.index, y=idx_cumret,
        name=index_name,
        line=dict(color='#546e7a', width=1.5, dash='dot'),
        hovertemplate='%{y:.2f}%<extra>' + index_name + '</extra>',
    ))

    # 종목
    last_val = float(stk_cumret.iloc[-1])
    stock_color = '#26c6da' if last_val >= 0 else '#ef5350'
    label = f'{ticker} {stock_name}' if stock_name and stock_name != ticker else ticker
    fig.add_trace(go.Scatter(
        x=stock_5m.index, y=stk_cumret,
        name=label,
        line=dict(color=stock_color, width=2),
        fill='tozeroy',
        fillcolor=f'rgba({34 if last_val >= 0 else 239},{198 if last_val >= 0 else 83},{218 if last_val >= 0 else 80},0.06)',
        hovertemplate='%{y:.2f}%<extra>' + label + '</extra>',
    ))

    title_date = pd.Timestamp(jjin_date).strftime('%Y-%m-%d') if jjin_date else ''
    open_time_str  = '09:00' if market.startswith('KR') else '09:30'
    close_time_str = '15:30' if market.startswith('KR') else '16:00'

    # x축 시작을 첫 데이터보다 30분 앞으로 설정해 첫 날 09:00 tick 표시
    x_start = index_5m.index[0] - pd.Timedelta(minutes=30)
    x_end   = index_5m.index[-1] + pd.Timedelta(minutes=5)

    # 날짜별 장마감~다음날 장시작 구간을 직접 rangebreak으로 지정
    overnight_breaks = []
    for i in range(len(dates) - 1):
        day_close = pd.Timestamp(f'{dates[i].date()} {close_time_str}', tz=tz)
        next_open = pd.Timestamp(f'{dates[i+1].date()} {open_time_str}', tz=tz)
        overnight_breaks.append(dict(bounds=[day_close, next_open]))

    fig.add_annotation(
        text=f'<b>{label}</b> vs {index_name} &nbsp;|&nbsp; 찐반등일 {title_date} 기준 5일',
        xref='x domain', yref='paper',
        x=0, y=1.06,
        xanchor='left', yanchor='bottom',
        showarrow=False,
        font=dict(size=13, color='#000000'),
    )

    fig.update_layout(
        title=dict(text=''),
        yaxis=dict(
            title='누적수익률 (%)',
            title_font=dict(size=13, color='#000000'),
            tickfont=dict(size=12, color='#000000'),
            gridcolor='#1e1e1e',
            zeroline=False,
        ),
        xaxis=dict(
            tickfont=dict(size=12, color='#000000'),
            gridcolor='#1e1e1e',
            tickformat='%H:%M\n%m/%d',
            tickvals=[pd.Timestamp(f'{d.date()} {open_time_str}', tz=tz) for d in dates],
            range=[x_start, x_end],
            rangebreaks=[dict(bounds=['sat', 'mon'])] + overnight_breaks,
        ),
        template='plotly_dark',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='#0e1117',
        height=360,
        margin=dict(l=40, r=20, t=55, b=20),
        legend=dict(
            orientation='v', y=1.06, x=1, xanchor='right', yanchor='bottom',
            font=dict(size=11),
            bgcolor='rgba(0,0,0,0)',
        ),
        hovermode='x unified',
    )
    return fig
