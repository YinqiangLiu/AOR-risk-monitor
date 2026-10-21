-- STEP 1: Calculate actual AOR returns for selected periods

CREATE VIEW AOR_RISK.ANALYTICS.V_HISTORICAL_STRESS AS
WITH scenarios AS (
    SELECT
        COLUMN1 AS SCENARIO,
        TO_DATE(COLUMN2) AS BASELINE_DATE,
        TO_DATE(COLUMN3) AS END_DATE
    FROM VALUES
        ('2020 selloff window', '2020-02-19', '2020-03-23'),
        ('2022 full year', '2021-12-31', '2022-12-31')
),
price_dates AS (
    SELECT
        s.SCENARIO,
        s.BASELINE_DATE,
        s.END_DATE,

        MAX(
            CASE
                WHEN p.TRADE_DATE <= s.BASELINE_DATE
                THEN p.TRADE_DATE
            END
        ) AS START_PRICE_DATE,

        MAX(p.TRADE_DATE) AS END_PRICE_DATE

    FROM scenarios s
    LEFT JOIN AOR_RISK.RAW.PRICES p
        ON p.TICKER = 'AOR'
       AND p.TRADE_DATE <= s.END_DATE
    GROUP BY
        s.SCENARIO,
        s.BASELINE_DATE,
        s.END_DATE
)
SELECT
    d.SCENARIO,
    d.START_PRICE_DATE,
    d.END_PRICE_DATE,
    sp.ADJUSTED_CLOSE AS START_PRICE,
    ep.ADJUSTED_CLOSE AS END_PRICE,

    ep.ADJUSTED_CLOSE / NULLIF(sp.ADJUSTED_CLOSE, 0) - 1
        AS PERIOD_RETURN,

    'Actual AOR historical fund prices' AS BASIS

FROM price_dates d
LEFT JOIN AOR_RISK.RAW.PRICES sp
    ON sp.TICKER = 'AOR'
   AND sp.TRADE_DATE = d.START_PRICE_DATE
LEFT JOIN AOR_RISK.RAW.PRICES ep
    ON ep.TICKER = 'AOR'
   AND ep.TRADE_DATE = d.END_PRICE_DATE;


-- Display cumulative historical returns
SELECT
    SCENARIO,
    START_PRICE_DATE,
    END_PRICE_DATE,
    ROUND(PERIOD_RETURN * 100, 2) AS PERIOD_RETURN_PERCENT,
    BASIS
FROM AOR_RISK.ANALYTICS.V_HISTORICAL_STRESS
ORDER BY START_PRICE_DATE;