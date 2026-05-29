import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from strategy.indicators import calc_ema, calc_sma, calc_adr, calc_rs_line


def daily_chart(df: pd.DataFrame, ticker: str, index_df: pd.DataFrame | None = None) -> go.Figure:
    """일봉 캔들 차트 + EMA21 + SMA200 + 거래량."""
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title=f'{ticker} — 데이터 없음', template='plotly_dark', height=600)
        return fig

    ema21 = calc_ema(df, 21)
    sma200 = calc_sma(df, 200)
    adr = calc_adr(df)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03
    )

    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name=ticker,
        increasing_line_color='#ef5350',
        decreasing_line_color='#26a69a',
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=ema21, name='EMA21',
        line=dict(color='orange', width=1.5)
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=sma200, name='SMA200',
        line=dict(color='#bb86fc', width=1.5)
    ), row=1, col=1)

    colors = [
        '#ef5350' if float(c) >= float(o) else '#26a69a'
        for c, o in zip(df['Close'], df['Open'])
    ]
    fig.add_trace(go.Bar(
        x=df.index, y=df['Volume'],
        name='Volume', marker_color=colors, showlegend=False
    ), row=2, col=1)

    # RS Line 추가 (지수 데이터 있을 때)
    if index_df is not None and not index_df.empty:
        rs_data = calc_rs_line(df, index_df)
        rs_line = rs_data.get('rs_line')
        if rs_line is not None and len(rs_line) > 0:
            # RS Line을 별도 y축(row3)에 표시
            fig.add_trace(go.Scatter(
                x=rs_line.index, y=rs_line.values,
                name='RS Line',
                line=dict(color='#00bcd4', width=1.5),
            ), row=2, col=1)

    fig.update_layout(
        title=f'{ticker} 일봉  |  ADR: {adr:.2f}%',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        height=600,
        margin=dict(l=40, r=40, t=60, b=20),
        legend=dict(orientation='h', y=1.02),
    )
    return fig


def intraday_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """5분봉 캔들 차트 + 거래량."""
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title=f'{ticker} 5분봉 — 데이터 없음', template='plotly_dark', height=500)
        return fig

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03
    )

    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name=ticker,
        increasing_line_color='#ef5350',
        decreasing_line_color='#26a69a',
    ), row=1, col=1)

    colors = [
        '#ef5350' if float(c) >= float(o) else '#26a69a'
        for c, o in zip(df['Close'], df['Open'])
    ]
    fig.add_trace(go.Bar(
        x=df.index, y=df['Volume'],
        name='Volume', marker_color=colors, showlegend=False
    ), row=2, col=1)

    fig.update_layout(
        title=f'{ticker} 5분봉',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        height=500,
        margin=dict(l=40, r=40, t=60, b=20),
    )
    return fig
