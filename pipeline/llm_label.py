"""BONUS — an LLM inside the pipeline (slide "LLM là một bước transform").

The support team wants an LLM pre-triage label on every live ticket
(gold_ticket_labels), to compare with the human `category` and to triage new
tickets faster. An LLM step is a transform like any other — except it is
expensive, slow and NOT deterministic, so the slide's four rules apply:

  1. key = hash(input) + model + prompt version  -> a re-run makes 0 LLM calls;
     changing the prompt re-labels everything ON PURPOSE
  2. force a structured output, validate it; invalid -> quarantine, never Gold
  3. estimate the cost BEFORE running (rows x tokens x price)
  4. LLM labels are versioned data (model + prompt_version stored on every row)

The shipped `label_tickets` is the NAIVE version: it calls the model for every
ticket on every run and writes whatever comes back. Your bonus task is to make
`python -m scripts.bonus_llm` print BONUS PASS. Zero-key: `FakeLLM` stands in for a
real model (swap in any provider via .env if you like — the pipeline is the same).
"""
from __future__ import annotations

import hashlib
import json
import re

import duckdb

MODEL = "fake-llm-2026-09"
PROMPT_VERSION = "triage-v2"
ALLOWED_LABELS = ("bug", "billing", "other")
PRICE_PER_1K_TOKENS_USD = 0.002          # pretend price, for the cost estimate


PROMPT_TEMPLATE = """You triage customer-support tickets.
Answer ONLY with JSON: {{"label": "bug" | "billing" | "other"}}.
Ticket ID: {ticket_id}
{text}"""


class FakeLLM:
    """Deterministic stand-in for a chat model. Counts calls and tokens."""

    def __init__(self, model: str = MODEL) -> None:
        self.model = model
        self.calls = 0
        self.tokens = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        self.tokens += len(prompt.split()) + 8
        text = prompt.lower()
        if "xuất" in text:
            return 'Sure! Here is the label: {"label": "export"}'   # off-schema answer
        if re.search(r"crash|lỗi|sso|đăng nhập|chatbot", text):
            return '{"label": "bug"}'
        if re.search(r"tiền|hoá đơn|thanh toán|gói|vat", text):
            return '{"label": "billing"}'
        return '{"label": "other"}'


def build_prompt(ticket_id: str, text: str) -> str:
    return PROMPT_TEMPLATE.format(ticket_id=ticket_id, text=text)


def estimate_tokens(tickets: list[tuple[str, str]]) -> int:
    return sum(len(build_prompt(ticket_id, text).split()) + 8
               for ticket_id, text in tickets)


def parse_label(raw: str) -> str | None:
    """Accept only the exact structured response required by the prompt."""
    try:
        obj = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or set(obj) != {"label"}:
        return None
    label = obj["label"]
    return label if isinstance(label, str) and label in ALLOWED_LABELS else None


def live_tickets(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    return con.execute("""
        SELECT ticket_id, subject || '. ' || body AS text
        FROM silver_tickets
        WHERE NOT is_deleted
        ORDER BY ticket_id
    """).fetchall()


def label_tickets(con: duckdb.DuckDBPyConnection, llm: FakeLLM) -> dict:
    """Label live tickets, caching validated and invalid responses by full version key.

    Invalid model responses are cached too, so retries do not repeatedly spend on
    the same bad answer. Only schema-valid labels are published to Gold.
    """
    con.execute("""CREATE TABLE IF NOT EXISTS llm_label_cache (
        cache_key VARCHAR PRIMARY KEY, input_hash VARCHAR, model VARCHAR,
        prompt_version VARCHAR, label VARCHAR, raw_response VARCHAR,
        is_valid BOOLEAN)""")
    con.execute("""CREATE TABLE IF NOT EXISTS llm_label_quarantine (
        cache_key VARCHAR PRIMARY KEY, ticket_id VARCHAR, input_hash VARCHAR,
        model VARCHAR, prompt_version VARCHAR, raw_response VARCHAR, reason VARCHAR)""")

    rows = []
    quarantined = []
    calls_before = llm.calls
    model = llm.model
    for ticket_id, text in live_tickets(con):
        prompt = build_prompt(ticket_id, text)
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        version_material = f"{input_hash}\0{model}\0{PROMPT_VERSION}"
        cache_key = hashlib.sha256(version_material.encode("utf-8")).hexdigest()
        cached = con.execute(
            "SELECT label, raw_response, is_valid FROM llm_label_cache WHERE cache_key = ?",
            [cache_key],
        ).fetchone()
        if cached is None:
            raw = llm.complete(prompt)
            label = parse_label(raw)
            is_valid = label is not None
            con.execute("""INSERT INTO llm_label_cache VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        [cache_key, input_hash, model, PROMPT_VERSION, label, raw, is_valid])
        else:
            label, raw, is_valid = cached

        if is_valid:
            rows.append((ticket_id, label, model, PROMPT_VERSION))
            con.execute("DELETE FROM llm_label_quarantine WHERE cache_key = ?", [cache_key])
        else:
            quarantined.append((cache_key, ticket_id, input_hash, model, PROMPT_VERSION,
                                raw, "response does not match allowed label schema"))

    # Gold is a versioned snapshot of valid outputs for the current prompt.
    con.execute("""CREATE OR REPLACE TABLE gold_ticket_labels (
        ticket_id VARCHAR, label VARCHAR, model VARCHAR, prompt_version VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO gold_ticket_labels VALUES (?, ?, ?, ?)", rows)
    if quarantined:
        con.executemany("""INSERT OR REPLACE INTO llm_label_quarantine
            VALUES (?, ?, ?, ?, ?, ?, ?)""", quarantined)
    return {"labeled": len(rows), "quarantined": len(quarantined),
            "calls": llm.calls - calls_before}
