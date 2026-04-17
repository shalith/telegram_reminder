-- Phase 6 manual migration for existing SQLite databases.
-- For easiest local usage, delete reminders.db and let SQLAlchemy recreate it.
ALTER TABLE reminders ADD COLUMN normalized_task TEXT;
ALTER TABLE reminders ADD COLUMN semantic_key TEXT;
ALTER TABLE reminders ADD COLUMN last_ai_confidence REAL DEFAULT 0;
ALTER TABLE reminders ADD COLUMN last_interpretation_json TEXT;
ALTER TABLE reminders ADD COLUMN last_target_selector_json TEXT;
ALTER TABLE reminders ADD COLUMN source_mode TEXT DEFAULT 'rule';

CREATE TABLE IF NOT EXISTS ai_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  message_text TEXT NOT NULL,
  system_prompt_version TEXT NOT NULL,
  model_name TEXT NOT NULL,
  raw_response_text TEXT,
  parsed_json TEXT,
  validation_ok INTEGER NOT NULL DEFAULT 0,
  checker_ok INTEGER NOT NULL DEFAULT 0,
  final_action TEXT,
  confidence REAL,
  error_code TEXT,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS target_resolution_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ai_run_id INTEGER NOT NULL,
  reminder_id INTEGER NOT NULL,
  score REAL NOT NULL,
  match_reason TEXT,
  selected INTEGER NOT NULL DEFAULT 0,
  action_name TEXT NOT NULL DEFAULT 'update_reminder'
);

CREATE TABLE IF NOT EXISTS action_audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  user_id INTEGER NOT NULL,
  reminder_id INTEGER,
  action_name TEXT NOT NULL,
  action_args_json TEXT,
  executor_result_json TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  input_text TEXT NOT NULL,
  expected_action TEXT NOT NULL,
  expected_json TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS interaction_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  chat_id INTEGER NOT NULL,
  telegram_user_id INTEGER NOT NULL,
  message_text TEXT NOT NULL,
  phase TEXT NOT NULL,
  outcome TEXT NOT NULL,
  error_code TEXT,
  details_json TEXT
);

CREATE TABLE IF NOT EXISTS correction_examples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  chat_id INTEGER NOT NULL,
  telegram_user_id INTEGER NOT NULL,
  source_text TEXT NOT NULL,
  action_name TEXT NOT NULL,
  resolved_task TEXT,
  resolved_time_phrase TEXT,
  learned_from_follow_up INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS learned_time_patterns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_phrase TEXT NOT NULL UNIQUE,
  normalized_phrase TEXT NOT NULL,
  success_count INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
