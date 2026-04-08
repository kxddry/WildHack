-- Add actual fulfilment columns to transport_requests so the dispatcher can
-- compute the business KPIs required by PRD §9.2:
--   * order_accuracy        — share of slots where |predicted - actual| <= 2
--   * avg_truck_utilization — mean of actual_units / (vehicles * capacity)
--
-- Both columns are nullable: only completed slots will have actuals; the
-- metrics endpoint reports n_slots_evaluated so the UI can hide the card
-- when nothing has been backfilled yet.

ALTER TABLE transport_requests
    ADD COLUMN IF NOT EXISTS actual_vehicles INTEGER,
    ADD COLUMN IF NOT EXISTS actual_units    DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_requests_actuals
    ON transport_requests (warehouse_id, time_slot_start)
    WHERE actual_vehicles IS NOT NULL;
