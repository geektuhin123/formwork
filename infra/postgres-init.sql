-- Runs once when the compose Postgres volume is created. Local development only: change the password elsewhere.
CREATE ROLE formwork_app LOGIN PASSWORD 'formwork_app';
