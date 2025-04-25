CREATE TABLE reminders (
    id SERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    remind_at TIMESTAMP NOT NULL
);


SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'reminders';

SELECT * FROM reminders;
