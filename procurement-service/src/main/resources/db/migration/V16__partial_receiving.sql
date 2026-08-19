-- V16: allow cumulative receipt batches before an order is fully received.
-- Existing rows are unchanged; only the status domain is expanded.
ALTER TABLE purchase_order
    DROP CHECK purchase_order_chk_2;

ALTER TABLE purchase_order
    ADD CONSTRAINT chk_purchase_order_status
        CHECK (status IN ('PENDING_SHIPMENT','SHIPPED','PARTIALLY_RECEIVED','RECEIVED','CLOSED'));
