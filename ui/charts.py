import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from strategy.indicators import calc_ema, calc_sma, calc_adr


def daily_chart(df: pd.DataFrame, ticker: str, index_df: pd.DataFrame | None = None) -> go.Figure:
    """일봉 캔들 차트 + EMA10/21 + SMA50/150/200 + 거래량."""
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title=f'{ticker} — 데이터 없음', template='plotly_dark', height=600)
        return fig

    ema10  = calc_ema(df, 10)
    ema21  = calc_ema(df, 21)
    sma50  = calc_sma(df, 50)
    sma150 = calc_sma(df, 150)
    sma200 = calc_sma(df, 200)
    adr    = calc_adr(df)

    bull_color = '#ef5350'
    bear_color = '#42a5f5'

    # 문자열 날짜 → 카테고리 축: 주말·휴일 갭 완전 제거
    x_dates = df.index.strftime('%Y-%m-%d').tolist()

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03
    )

    fig.add_trace(go.Candlestick(
        x=x_dates,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name=ticker,
        increasing_line_color=bull_color,
        increasing_fillcolor=bull_color,
        decreasing_line_color=bear_color,
        decreasing_fillcolor=bear_color,
    ), row=1, col=1)

    mas = [
        (sma50,  'SMA50',  '#8B1A1A', 1.2),
        (sma150, 'SMA150', '#1565C0', 1.2),
        (sma200, 'SMA200', '#7B1FA2', 1.2),
        (ema10,  'EMA10',  '#FF4444', 1.5),
        (ema21,  'EMA21',  '#FF9800', 1.5),
    ]
    for series, name, color, width in mas:
        fig.add_trace(go.Scatter(
            x=x_dates, y=series.values, name=name,
            line=dict(color=color, width=width)
        ), row=1, col=1)

    colors = [
        bull_color if float(c) >= float(o) else bear_color
        for c, o in zip(df['Close'], df['Open'])
    ]
    fig.add_trace(go.Bar(
        x=x_dates, y=df['Volume'].values,
        name='Volume', marker_color=colors, showlegend=False
    ), row=2, col=1)

    fig.update_layout(
        title=f'{ticker} 일봉  |  ADR: {adr:.2f}%',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        height=600,
        margin=dict(l=40, r=40, t=60, b=20),
        legend=dict(orientation='h', y=1.02),
    )
    fig.update_xaxes(type='category')
    return fig


def intraday_chart(df: pd.DataFrame, ticker: str, market: str = 'US') -> go.Figure:
    """5분봉 캔들 차트 + 거래량."""
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title=f'{ticker} 5분봉 — 데이터 없음', template='plotly_dark', height=500)
        return fig

    plot_df = df.copy()

    # 타임존 제거 (UTC이든 KST이든 naive로 통일)
    if hasattr(plot_df.index, 'tz') and plot_df.index.tz is not None:
        tz = 'Asia/Seoul' if market.startswith('KR') else 'America/New_York'
        plot_df.index = plot_df.index.tz_convert(tz).tz_localize(None)

    # 문자열 타임스탬프 → 카테고리 축: 장외시간·주말·휴일 갭 완전 제거
    x_ts = plot_df.index.strftime('%m/%d %H:%M').tolist()

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03
    )

    bull_color = '#ef5350'
    bear_color = '#42a5f5'

    fig.add_trace(go.Candlestick(
        x=x_ts,
        open=plot_df['Open'], high=plot_df['High'],
        low=plot_df['Low'], close=plot_df['Close'],
        name=ticker,
        increasing_line_color=bull_color,
        increasing_fillcolor=bull_color,
        decreasing_line_color=bear_color,
        decreasing_fillcolor=bear_color,
    ), row=1, col=1)

    colors = [
        bull_color if float(c) >= float(o) else bear_color
        for c, o in zip(plot_df['Close'], plot_df['Open'])
    ]
    fig.add_trace(go.Bar(
        x=x_ts, y=plot_df['Volume'].values,
        name='Volume', marker_color=colors, showlegend=False
    ), row=2, col=1)

    fig.update_layout(
        title=f'{ticker} 5분봉',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        height=500,
        margin=dict(l=40, r=40, t=60, b=20),
    )
    fig.update_xaxes(type='category')
    return fig
