import pandas as pd
from strategy.indicators import calc_all_rs, get_breakdown_slice


def score_volume_asymmetry(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> tuple[int, dict]:
    """
    10EMA 이탈일부터 오늘까지 상승일/하락일 평균 거래량 비율.
    비율 >= 1.2 이면 강함.
    """
    df = get_breakdown_slice(stock_df, index_df)

    if len(df) < 4:
        return 0, {'up_vol': 0, 'down_vol': 0, 'ratio': 0}

    df = df.copy()
    df['is_up'] = df['Close'] >= df['Open']
    up_vol = df[df['is_up']]['Volume'].mean()
    down_vol = df[~df['is_up']]['Volume'].mean()

    if pd.isna(up_vol) or pd.isna(down_vol) or down_vol == 0:
        return 0, {'up_vol': 0, 'down_vol': 0, 'ratio': 0}

    ratio = up_vol / down_vol
    return (1 if ratio >= 1.2 else 0), {
        'up_vol': int(up_vol),
        'down_vol': int(down_vol),
        'ratio': round(ratio, 2),
    }


def score_candle_ratio(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> tuple[int, dict]:
    """
    10EMA 이탈일부터 오늘까지 누적 양봉 바디 합 / 음봉 바디 합.
    비율 >= 1.0 이면 강함.
    """
    df = get_breakdown_slice(stock_df, index_df)

    if len(df) < 2:
        return 0, {'bull_sum': 0, 'bear_sum': 0, 'ratio': 0}

    bull_sum = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open'])) if float(r['Close']) > float(r['Open']) else 0, axis=1
    ).sum()
    bear_sum = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open'])) if float(r['Close']) < float(r['Open']) else 0, axis=1
    ).sum()

    ratio = bull_sum / bear_sum if bear_sum > 0 else (2.0 if bull_sum > 0 else 1.0)
    return (1 if ratio >= 1.0 else 0), {
        'bull_sum': round(bull_sum, 2),
        'bear_sum': round(bear_sum, 2),
        'ratio': round(ratio, 2),
    }


def total_score_with_detail(stock_df: pd.DataFrame, index_df: pd.DataFrame,
                             all_stocks_returns: list | None = None) -> dict:
    rs = calc_all_rs(stock_df, index_df, all_stocks_returns)

    rs_votes = sum([
        1 if rs['excess_pct'] > 0 else 0,
        1 if rs['rs_line_uptrend'] else 0,
        1 if rs['ibd_rating'] >= 70 else 0,
    ])
    rs_score = 1 if rs_votes >= 2 else 0

    s2, vol_data = score_volume_asymmetry(stock_df, index_df)
    s3, candle_data = score_candle_ratio(stock_df, index_df)

    summary_parts = [
        f"📈 RSLine {rs['rs_line_pct']:+.1f}% ({'우상향' if rs['rs_line_uptrend'] else '하향'})",
        f"🔊 거래량비 {vol_data['ratio']:.2f}x",
        f"🕯️ 양봉/음봉 {candle_data['ratio']:.2f}x",
        f"📅 기준: {rs['breakdown_date']} ({rs['days']}일)",
    ]

    return {
        'score': rs_score + s2 + s3,
        'rs_score': rs_score,
        'vol_score': s2,
        'candle_score': s3,
        'excess_pct': rs['excess_pct'],
        'rs_line_pct': rs['rs_line_pct'],
        'rs_line_uptrend': rs['rs_line_uptrend'],
        'rs_line_new_high': rs['rs_line_new_high'],
        'ibd_rating': rs['ibd_rating'],
        'rs_votes': rs_votes,
        'stock_pct': rs['stock_pct'],
        'index_pct': rs['index_pct'],
        'breakdown_date': rs['breakdown_date'],
        'days': rs['days'],
        'vol_ratio': vol_data['ratio'],
        'candle_ratio': candle_data['ratio'],
        'rs_line_series': rs.get('rs_line_series'),
        'summary': ' / '.join(summary_parts),
    }
