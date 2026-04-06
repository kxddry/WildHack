-- Add unique constraint and updated_at to transport_requests for existing databases
-- Safe to run multiple times (IF NOT EXISTS guards)

ALTER TABLE transport_requests
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- Remove duplicate rows keeping only the latest per (warehouse_id, time_slot_start, time_slot_end)
DELETE FROM transport_requests
WHERE id NOT IN (
    SELECT MAX(id)
    FROM transport_requests
    GROUP BY warehouse_id, time_slot_start, time_slot_end
);

-- Now add the unique constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_transport_requests_slot'
    ) THEN
        ALTER TABLE transport_requests
            ADD CONSTRAINT uq_transport_requests_slot
            UNIQUE (warehouse_id, time_slot_start, time_slot_end);
    END IF;
END
$$;
