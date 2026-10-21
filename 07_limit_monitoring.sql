--  Review project demonstration limits
SELECT RULE_NAME,
    ROUND(THRESHOLD * 100, 2) AS LIMIT_PERCENT
FROM AOR_RISK.ANALYTICS.DEMO_LIMITS
ORDER BY RULE_NAME;

--Create the monitoring view
--Compare measurements with demonstration limits
CREATE VIEW AOR_RISK.ANALYTICS.V_LIMIT_CHECKS AS
WITH measured AS (
    -- Check each of the seven ETF holdings
    SELECT 'MAX_ETF_WEIGHT' AS RULE_NAME,
        TICKER AS SCOPE,
        WEIGHT AS ACTUAL,
        'Current holdings snapshot' AS BASIS
    FROM AOR_RISK.ANALYTICS.V_CURRENT_HOLDINGS
    WHERE ASSET_CLASS <> 'Cash'

    UNION ALL

    -- Check the total equity allocation
    SELECT
        'MAX_EQUITY_WEIGHT',
        'Equity sleeve',
        PORTFOLIO_WEIGHT,
        'Current holdings snapshot'
    FROM AOR_RISK.ANALYTICS.V_EXPOSURES
    WHERE ASSET_CLASS = 'Equity'

    UNION ALL

    -- Check AOR's historical one-day VaR
    SELECT
        'MAX_DAILY_VAR_95',
        'AOR fund',
        VAR_95_1D,
        'Actual AOR returns, trailing three years'
    FROM AOR_RISK.ANALYTICS.V_FUND_RISK_3Y
)
SELECT m.RULE_NAME, m.SCOPE, m.ACTUAL, l.THRESHOLD,
    GREATEST(m.ACTUAL - l.THRESHOLD, 0) AS EXCESS,

    CASE
        WHEN m.ACTUAL IS NULL OR l.THRESHOLD IS NULL
            THEN 'DATA_ISSUE'
        WHEN m.ACTUAL > l.THRESHOLD
            THEN 'BREACH'
        ELSE 'PASS'
    END AS STATUS,

    m.BASIS

FROM measured m
LEFT JOIN AOR_RISK.ANALYTICS.DEMO_LIMITS l
    ON m.RULE_NAME = l.RULE_NAME;

-- Display results, with breaches first
SELECT
    RULE_NAME,
    SCOPE,
    ROUND(ACTUAL * 100, 2) AS ACTUAL_PERCENT,
    ROUND(THRESHOLD * 100, 2) AS LIMIT_PERCENT,
    ROUND(EXCESS * 100, 2) AS EXCESS_PERCENTAGE_POINTS,
    STATUS,
    BASIS
FROM AOR_RISK.ANALYTICS.V_LIMIT_CHECKS
ORDER BY STATUS, EXCESS DESC, SCOPE;