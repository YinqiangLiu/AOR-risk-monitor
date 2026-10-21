"""AOR Investment Risk Monitor — run inside Streamlit in Snowflake.

Supports the container runtime used by Snowflake Workspaces.
Dependencies: streamlit, pandas, snowflake-snowpark-python.
Reads the views created in project steps 1–10 using Snowflake's built-in connection.
"""
from math import isclose, isfinite

import pandas as pd


QUERIES = {
    "risk": "SELECT * FROM AOR_RISK.ANALYTICS.V_FUND_RISK_3Y",
    "drawdown": "SELECT * FROM AOR_RISK.ANALYTICS.V_FUND_DRAWDOWN_3Y ORDER BY TRADE_DATE",
    "holdings": "SELECT * FROM AOR_RISK.ANALYTICS.V_CURRENT_HOLDINGS",
    "exposures": "SELECT * FROM AOR_RISK.ANALYTICS.V_EXPOSURES",
    "limits": "SELECT * FROM AOR_RISK.ANALYTICS.V_LIMIT_CHECKS",
    "stress": "SELECT * FROM AOR_RISK.ANALYTICS.V_HISTORICAL_STRESS ORDER BY START_PRICE_DATE",
    "contributions": "SELECT * FROM AOR_RISK.ANALYTICS.V_RISK_CONTRIBUTIONS",
    "backtest": """
        SELECT * FROM AOR_RISK.ANALYTICS.V_VAR_BACKTEST
        WHERE TRADE_DATE BETWEEN
            (SELECT MIN(TRADE_DATE) FROM AOR_RISK.ANALYTICS.V_FUND_RETURNS_3Y)
            AND (SELECT MAX(TRADE_DATE) FROM AOR_RISK.ANALYTICS.V_FUND_RETURNS_3Y)
        ORDER BY TRADE_DATE
    """,
    "raw_checks": """
        SELECT 'Duplicate price keys' AS CHECK_NAME, COUNT(*) AS PROBLEM_COUNT
        FROM (
            SELECT TICKER, TRADE_DATE FROM AOR_RISK.RAW.PRICES
            GROUP BY TICKER, TRADE_DATE HAVING COUNT(*) > 1
        )
        UNION ALL
        SELECT 'Invalid prices', COUNT(*) FROM AOR_RISK.RAW.PRICES
        WHERE TRADE_DATE IS NULL OR TICKER IS NULL OR TRIM(TICKER) = ''
           OR ADJUSTED_CLOSE IS NULL OR ADJUSTED_CLOSE <= 0
        UNION ALL
        SELECT 'Dates without all eight ETF prices', COUNT(*)
        FROM (
            SELECT TRADE_DATE FROM AOR_RISK.RAW.PRICES
            GROUP BY TRADE_DATE HAVING COUNT(DISTINCT TICKER) <> 8
        )
    """,
}


def normalize(frame):
    """Normalize Snowpark column names and dates without changing percentages."""
    frame = frame.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    for name in (
        "trade_date", "holdings_date", "period_start", "period_end",
        "start_price_date", "end_price_date",
    ):
        if name in frame.columns:
            frame[name] = pd.to_datetime(frame[name])
    return frame


def report_checks(data):
    """Check input availability and reconcile report outputs before display."""
    results = []

    def check(label, passed):
        results.append({"Check": label, "Status": "PASS" if passed else "REVIEW"})

    for name, frame in data.items():
        check(f"{name.replace('_', ' ').title()} data available", not frame.empty)
    if any(frame.empty for frame in data.values()) or len(data["risk"]) != 1:
        check("Exactly one fund risk summary", len(data["risk"]) == 1)
        return pd.DataFrame(results)

    for row in data["raw_checks"].itertuples():
        check(row.check_name, int(row.problem_count) == 0)

    risk = data["risk"].iloc[0]
    holdings, drivers = data["holdings"], data["contributions"]
    n = int(risk.observations)
    check("One portfolio and one holdings snapshot",
          holdings.portfolio_id.nunique() == 1 and holdings.holdings_date.nunique() == 1)
    check("Holdings tickers are unique", not holdings.ticker.duplicated().any())
    check("Holdings use USD share prices", holdings.currency.eq("USD").all())
    check("Holdings weights reconcile within published rounding",
          abs(float(holdings.weight.sum()) - 1) <= 0.001)
    check("Asset-class exposure weights reconcile to holdings",
          isclose(float(data["exposures"].portfolio_weight.sum()),
                  float(holdings.weight.sum()), abs_tol=1e-10))
    check("Each holding has one calculated risk share",
          len(drivers) == len(holdings)
          and not drivers.ticker.duplicated().any()
          and set(drivers.ticker) == set(holdings.ticker)
          and drivers.risk_share.notna().all()
          and drivers.risk_share.map(lambda v: pd.notna(v) and isfinite(float(v))).all())
    check("Risk shares sum to 100%",
          isclose(float(drivers.risk_share.sum()), 1, abs_tol=1e-6))
    check("Contributions use the complete reporting window",
          drivers.observations.notna().all() and drivers.observations.eq(n).all())
    check("Drawdown observations cover the reporting window",
          len(data["drawdown"]) == n and data["drawdown"].drawdown.notna().all()
          and not data["drawdown"].trade_date.duplicated().any()
          and data["drawdown"].trade_date.min() == risk.period_start
          and data["drawdown"].trade_date.max() == risk.period_end)
    check("Backtest observations cover the reporting window",
          len(data["backtest"]) == n
          and not data["backtest"].trade_date.duplicated().any()
          and set(data["backtest"].trade_date) == set(data["drawdown"].trade_date))
    check("Backtest uses 252 earlier training observations",
          data["backtest"].training_days.eq(252).all())
    check("Backtest outcomes and forecasts are populated",
          data["backtest"][["daily_return", "var_95_1d", "exception_flag"]]
          .notna().all().all()
          and data["backtest"].exception_flag.isin([0, 1]).all())
    expected_exceptions = (
        -data["backtest"].daily_return.astype(float)
        > data["backtest"].var_95_1d.astype(float)
    ).astype(int)
    check("Backtest exception flags match realized losses and forecasts",
          data["backtest"].exception_flag.eq(expected_exceptions).all())
    check("Demonstration limits have valid measurements and thresholds",
          not data["limits"].status.eq("DATA_ISSUE").any()
          and data["limits"][["actual", "threshold"]].notna().all().all())
    check("Historical stress endpoints and returns are available",
          data["stress"][["start_price_date", "end_price_date", "period_return"]]
          .notna().all().all())
    metrics = [risk.annualized_volatility, risk.var_95_1d,
               risk.expected_shortfall_95_1d, data["drawdown"].drawdown.min()]
    check("Headline risk metrics are finite",
          all(pd.notna(v) and isfinite(float(v)) for v in metrics))
    return pd.DataFrame(results)


def percent_table(frame, columns):
    """Format fractional values as percentages for a human-readable table."""
    result = frame.copy()
    for column in columns:
        result[column] = result[column].map(
            lambda value: f"{float(value):.2%}" if pd.notna(value) else "Missing"
        )
    return result


def main():
    import streamlit as st

    st.set_page_config(page_title="AOR Investment Risk Monitor", layout="wide")
    st.title("AOR Investment Risk Monitor")
    st.caption("Public ETF research project · Historical fund risk and current holdings analytics")

    @st.cache_data(ttl=300, show_spinner=False)
    def read_query(sql, _session):
        return normalize(_session.sql(sql).to_pandas())

    if st.button("Refresh report"):
        read_query.clear()

    try:
        session = st.connection("snowflake").session()
        data = {name: read_query(sql, session) for name, sql in QUERIES.items()}
        checks = report_checks(data)
    except Exception as error:
        st.error("The report could not load. Check that the project views exist and the app's role can read them.")
        with st.expander("Details for troubleshooting"):
            st.code(str(error))
        st.stop()

    if checks.Status.eq("REVIEW").any():
        st.error("Review the following data checks before using the report.")
        st.dataframe(checks, use_container_width=True)
        st.stop()

    risk = data["risk"].iloc[0]
    holdings = data["holdings"]
    drawdown = data["drawdown"].sort_values("trade_date")
    snapshot = holdings.holdings_date.max().date()
    st.caption(
        f"Risk window: {risk.period_start.date()} to {risk.period_end.date()} · "
        f"{int(risk.observations):,} daily returns · Holdings snapshot: {snapshot} · USD share prices"
    )

    overview, drivers_tab, limits_tab, stress_tab, backtest_tab, quality_tab = st.tabs([
        "Fund risk", "Risk contributions", "Limits", "Historical stress", "VaR backtest", "Data checks"
    ])

    with overview:
        cols = st.columns(4)
        for col, label, value in zip(cols,
                ["Annualized volatility", "95% one-day VaR", "95% one-day ES", "Maximum drawdown"],
                [risk.annualized_volatility, risk.var_95_1d,
                 risk.expected_shortfall_95_1d, drawdown.drawdown.min()]):
            col.metric(label, f"{float(value):.2%}")
        st.caption("Actual AOR adjusted-price returns. Volatility is annualized using 252 trading days; VaR and ES use a one-day horizon.")
        st.subheader("Three-year drawdown")
        st.line_chart(drawdown.set_index("trade_date")[["drawdown"]].mul(100)
                      .rename(columns={"drawdown": "Drawdown (%)"}))
        st.subheader("Capital allocation")
        exposures = data["exposures"].sort_values("portfolio_weight", ascending=False)
        st.bar_chart(exposures.set_index("asset_class")[["portfolio_weight"]].mul(100)
                     .rename(columns={"portfolio_weight": "Allocation (%)"}))
        table = percent_table(exposures[["asset_class", "portfolio_weight", "market_value_usd"]], ["portfolio_weight"])
        table["market_value_usd"] = table.market_value_usd.map(lambda v: f"${float(v):,.2f}")
        st.dataframe(table.rename(columns={"asset_class": "Asset class", "portfolio_weight": "Allocation",
                                           "market_value_usd": "Market value (USD)"}), use_container_width=True)
        st.caption("Published weights retain their original rounding; their total may differ slightly from 100%.")

    with drivers_tab:
        drivers = data["contributions"].sort_values("risk_share", ascending=False)
        st.metric("Modeled current-holdings annualized volatility",
                  f"{float(drivers.model_annualized_volatility.iloc[0]):.2%}")
        st.caption("Current snapshot weights combined with historical return relationships. Cash and money market balances use zero modeled returns; negative risk contributions are retained.")
        st.bar_chart(drivers.set_index("ticker")[["risk_share"]].mul(100)
                     .rename(columns={"risk_share": "Share of modeled volatility (%)"}))
        st.dataframe(percent_table(drivers[["ticker", "asset_class", "weight", "risk_share"]],
                                   ["weight", "risk_share"])
                     .rename(columns={"ticker": "Ticker", "asset_class": "Asset class",
                                      "weight": "Capital weight", "risk_share": "Risk share"}), use_container_width=True)
        st.caption("The model holds current weights constant over the historical sample. AOR's realized fund volatility reflects the fund's actual historical allocations.")
        st.download_button("Download risk contributions", drivers.to_csv(index=False).encode("utf-8"),
                           "aor_risk_contributions.csv", "text/csv")

    with limits_tab:
        limits = data["limits"].sort_values(["status", "excess"], ascending=[True, False])
        c1, c2 = st.columns(2)
        c1.metric("Demonstration breaches", str(int(limits.status.eq("BREACH").sum())))
        c2.metric("Checks passing", str(int(limits.status.eq("PASS").sum())))
        st.caption("Project demonstration thresholds: 30% per ETF, 65% total equity allocation, and 2% one-day 95% fund VaR. Breaches refer to these illustrative project rules.")
        visible = percent_table(limits[["rule_name", "scope", "actual", "threshold", "excess", "status", "basis"]],
                                ["actual", "threshold"])
        visible["excess"] = visible.excess.map(lambda v: f"{float(v) * 100:.2f} pp")
        visible["rule_name"] = visible.rule_name.map({"MAX_ETF_WEIGHT": "ETF allocation",
            "MAX_EQUITY_WEIGHT": "Equity allocation", "MAX_DAILY_VAR_95": "95% one-day VaR"})
        st.dataframe(visible.rename(columns={"rule_name": "Rule", "scope": "Scope", "actual": "Actual",
                     "threshold": "Limit", "excess": "Excess", "status": "Status", "basis": "Basis"}), use_container_width=True)
        st.download_button("Download limit report", limits.to_csv(index=False).encode("utf-8"),
                           "aor_limit_report.csv", "text/csv")

    with stress_tab:
        stress = data["stress"]
        st.caption("Actual AOR cumulative close-to-close returns in selected historical periods. The displayed endpoints are the available trading dates used in the calculation.")
        st.bar_chart(stress.set_index("scenario")[["period_return"]].mul(100)
                     .rename(columns={"period_return": "Period return (%)"}))
        visible = percent_table(stress[["scenario", "start_price_date", "end_price_date", "period_return"]], ["period_return"])
        for col in ["start_price_date", "end_price_date"]:
            visible[col] = visible[col].dt.date
        st.dataframe(visible.rename(columns={"scenario": "Scenario", "start_price_date": "Baseline close",
                     "end_price_date": "End close", "period_return": "Cumulative return"}), use_container_width=True)

    with backtest_tab:
        backtest = data["backtest"].sort_values("trade_date")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Backtest days", f"{len(backtest):,}")
        c2.metric("VaR exceptions", str(int(backtest.exception_flag.sum())))
        c3.metric("Exception rate", f"{float(backtest.exception_flag.mean()):.2%}")
        c4.metric("Reference exception rate", "5.00%")
        st.caption("Rolling 95% historical VaR using 252 earlier trading-day returns. Each forecast excludes the day being tested. The exception rate is a coverage diagnostic; independence requires additional testing.")
        chart = pd.DataFrame({"Realized loss (%)": -backtest.daily_return.to_numpy(dtype=float) * 100,
                              "Forecast VaR (%)": backtest.var_95_1d.to_numpy(dtype=float) * 100},
                             index=pd.DatetimeIndex(backtest.trade_date, name="Date"))
        st.line_chart(chart)
        st.caption("Positive realized values are losses; negative realized values are gains.")
        exceptions = backtest.loc[backtest.exception_flag.eq(1)].copy()
        visible = pd.DataFrame({"Date": exceptions.trade_date.dt.date,
            "Realized loss": exceptions.daily_return.map(lambda v: f"{-float(v):.2%}"),
            "Forecast VaR": exceptions.var_95_1d.map(lambda v: f"{float(v):.2%}"),
            "Training days": exceptions.training_days}).sort_values("Date", ascending=False)
        st.subheader("All exceptions in the reporting window")
        st.dataframe(visible, use_container_width=True)
        st.download_button("Download complete backtest", backtest.to_csv(index=False).encode("utf-8"),
                           "aor_var_backtest.csv", "text/csv")

    with quality_tab:
        st.success(f"{len(checks)} report checks passed.")
        st.dataframe(checks, use_container_width=True)
        with st.expander("Data and methodology"):
            st.write("Holdings: public iShares AOR direct-holdings snapshot. Prices: Yahoo Finance adjusted daily prices downloaded with yfinance.")
            st.write("Fund risk uses actual AOR adjusted-price returns. Historical VaR is the negative fifth return percentile; ES is estimated from returns at or below that percentile.")
            st.write("Current-holdings risk shares use position weights and each ETF's covariance with the constant-weight model portfolio, divided by model return variance.")
            st.write("Published weights remain unnormalized. USD cash and money market positions use zero modeled returns. All holdings and price dates are displayed explicitly.")
            st.write("Historical stress results are cumulative actual fund returns over selected past periods. The backtest evaluates a separate rolling 252-day VaR estimation window.")


if __name__ == "__main__":
    main()
