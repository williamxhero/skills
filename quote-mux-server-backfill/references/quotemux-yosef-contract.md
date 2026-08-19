# QuoteMux yosef-server contract

Re-check these facts before use; they describe the known layout rather than an immutable schema.

## Live layout

- Connect through `ssh yosef-server`.
- The normal application release link is `/data/MarketHub2/current`.
- Runtime data and environment are under `/data/markethub`; the environment file is usually `/data/markethub/env/markethub.env`.
- The API service is normally `markethub-api.service`; health is normally `http://127.0.0.1:8803/api/health`.
- Audit/remediation artifacts belong in a dedicated `/data/markethub/audit/<remediation-id>/` folder, not in a release tree.

## Common facts

- `fact.stock_daily_1d`: stock daily OHLCV/amount.
- `fact.stock_price_band_daily`: daily upper/lower limit prices.
- `fact.stock_money_flow_daily`: stock money-flow facts.
- `fact.concept_daily_1d`: concept daily OHLCV/amount.
- `fact.stock_bar_1m`: stock minute bars; inspect its actual primary key and timestamp type before import.
- `ref.concept_stock_membership`: historical concept-member intervals. An effective member has `valid_from <= trade_date` and no `valid_to` before the trade date.

## Common traps

- A concept-member dependency count may repeat one stock for multiple concepts. Export both rows and distinct stock keys.
- An audit that joins dependencies from existing concept-daily rows can understate dependency gaps when concept rows themselves are missing. Audit the full effective membership scope separately before declaring coverage.
- Old annual 1m archives may contain only 239 bars and omit amount. They cannot meet a strict 240-bar / amount contract.
- `turnover` commonly maps to QuoteMux `amount`, but verify the source unit and currency before mapping.
- Tushare stock-money-flow scripts may select only positive-amount active stocks. Verify exact missing members are included, particularly suspended stocks.
- Keep `1m` behavior independent from any `30m` optimization or fallback logic.
