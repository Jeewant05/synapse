-- Replace <catalog> and <schema> with your Unity Catalog destination.
-- The service principal/user needs USE CATALOG, USE SCHEMA, SELECT, and INSERT.
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.synapse_agent_traces (
  trace_id STRING NOT NULL,
  source STRING NOT NULL,
  event_type STRING NOT NULL,
  event_timestamp TIMESTAMP NOT NULL,
  run_id STRING,
  objective_id STRING,
  workstream_id STRING,
  agent_id STRING,
  payload_json STRING
)
USING DELTA;
