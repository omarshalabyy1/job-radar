-- Airflow's own records (runs, tasks, schedules), beside the warehouse in the same Postgres.
-- docker-compose.yml runs this when the warehouse is created; on an existing one, run it once:
--   docker exec job-radar-warehouse-1 psql -U jobradar -d jobradar -c "CREATE DATABASE airflow"
CREATE DATABASE airflow;
