CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT        UNIQUE NOT NULL,
    password_hash       TEXT        NOT NULL,
    full_name           TEXT        NOT NULL,
    firm_name           TEXT,
    bar_number          TEXT,
    state               TEXT,
    phone               TEXT,
    stripe_customer_id  TEXT,
    credit_cents        INTEGER     DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recipients (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT    UNIQUE NOT NULL,
    password_hash   TEXT,
    full_name       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deliveries (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_id                   UUID        NOT NULL REFERENCES users(id),
    recipient_email             TEXT        NOT NULL,
    recipient_name              TEXT,
    recipient_id                UUID        REFERENCES recipients(id),
    require_account             BOOLEAN     DEFAULT FALSE,
    s3_key                      TEXT        NOT NULL,
    filename                    TEXT        NOT NULL,
    file_size_bytes             BIGINT,
    content_type                TEXT,
    subject                     TEXT        NOT NULL,
    message                     TEXT,
    matter_ref                  TEXT,
    access_token                TEXT        UNIQUE NOT NULL,
    expires_at                  TIMESTAMPTZ NOT NULL,
    allow_download              BOOLEAN     DEFAULT TRUE,
    tier                        TEXT        NOT NULL DEFAULT 'basic',
    price_cents                 INTEGER     NOT NULL,
    sha256                      TEXT,
    certificate_id              TEXT,
    cert_url                    TEXT,
    evidentix_verified          BOOLEAN     DEFAULT FALSE,
    custody_record_id           TEXT,
    custody_record_url          TEXT,
    status                      TEXT        DEFAULT 'sent',
    sent_at                     TIMESTAMPTZ DEFAULT NOW(),
    opened_at                   TIMESTAMPTZ,
    acknowledged_at             TIMESTAMPTZ,
    opened_ip                   TEXT,
    stripe_payment_intent_id    TEXT,
    paid                        BOOLEAN     DEFAULT FALSE,
    paid_at                     TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_sender    ON deliveries(sender_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_token     ON deliveries(access_token);
CREATE INDEX IF NOT EXISTS idx_deliveries_recipient ON deliveries(recipient_email);
CREATE INDEX IF NOT EXISTS idx_deliveries_status    ON deliveries(status);

CREATE TABLE IF NOT EXISTS delivery_events (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_id     UUID        NOT NULL REFERENCES deliveries(id),
    event_type      TEXT        NOT NULL,
    ip_address      TEXT,
    user_agent      TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_delivery ON delivery_events(delivery_id);

CREATE TABLE IF NOT EXISTS payments (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_id                 UUID        REFERENCES deliveries(id),
    user_id                     UUID        NOT NULL REFERENCES users(id),
    amount_cents                INTEGER     NOT NULL,
    stripe_payment_intent_id    TEXT,
    stripe_charge_id            TEXT,
    status                      TEXT        DEFAULT 'pending',
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    completed_at                TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_payments_user     ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_delivery ON payments(delivery_id);
CREATE INDEX IF NOT EXISTS idx_payments_pi       ON payments(stripe_payment_intent_id);