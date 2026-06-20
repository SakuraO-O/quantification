"""Serialization helpers shared by outputs, dashboard data, and tests."""

import numpy as np
import pandas as pd


def clean_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, np.generic):
        return value.item()
    return value


def round_output(frame):
    data = frame.copy()
    for column in ["close", "MA20", "MA60", "MA120", "MA200", "last_year_dividend", "dividend_yield", "pe", "pe_percentile"]:
        if column in data:
            data[column] = data[column].round(2)
    for column in [
        "daily_return",
        "return_ytd",
        "return_1w",
        "return_1m",
        "return_1y",
        "return_3y",
        "ma20_slope_5d",
        "ma60_slope_10d",
        "ma120_slope_20d",
        "ma200_slope_40d",
    ]:
        if column in data:
            data[column] = (data[column] * 100).round(2)
    return data

