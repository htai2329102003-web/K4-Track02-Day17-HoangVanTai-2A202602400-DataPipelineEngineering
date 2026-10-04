"""BONUS checker — the LLM labelling step (see pipeline/llm_label.py).

    python -m scripts.bonus_llm

Runs the labelling step on a fresh warehouse twice, then once more with a new
prompt version, and checks the slide's rules:
  * cost is estimated before the first run
  * re-run with the same model + prompt  -> 0 LLM calls (cache by hash)
  * new prompt version                    -> every ticket re-labelled on purpose
  * every Gold label is one of bug / billing / other; bad answers quarantined
"""
from __future__ import annotations

import sys

from main import fresh_build
from pipeline import llm_label
from pipeline.run import connect


def main() -> int:
    fresh_build(quiet=True)
    con = connect()
    try:
        tickets = llm_label.live_tickets(con)
        est = llm_label.estimate_tokens(tickets)
        print(f"=== bonus: LLM labelling of {len(tickets)} live tickets ===")
        print(f"  cost estimate before running: ~{est} tokens "
              f"= ${est / 1000 * llm_label.PRICE_PER_1K_TOKENS_USD:.4f} per full run")

        llm = llm_label.FakeLLM()
        llm_label.label_tickets(con, llm)
        first = llm.calls
        measured = llm.tokens
        llm_label.label_tickets(con, llm)
        second = llm.calls - first

        bad = con.execute(f"""SELECT count(*) FROM gold_ticket_labels
                              WHERE label NOT IN {llm_label.ALLOWED_LABELS}""").fetchone()[0]
        has_q = con.execute("""SELECT count(*) FROM information_schema.tables
                               WHERE table_name = 'llm_label_quarantine'""").fetchone()[0]
        n_q = con.execute("SELECT count(*) FROM llm_label_quarantine").fetchone()[0] if has_q else 0

        old_version = llm_label.PROMPT_VERSION
        llm_label.PROMPT_VERSION = old_version + "-bonus-check"
        before = llm.calls
        llm_label.label_tickets(con, llm)
        third = llm.calls - before
        llm_label.PROMPT_VERSION = old_version
        versions = con.execute("SELECT DISTINCT prompt_version FROM gold_ticket_labels").fetchall()

        checks = [
            ("first run labels every live ticket", first == len(tickets), f"{first} calls"),
            ("cost estimate matches FakeLLM counted tokens", est == measured,
             f"estimated {est}, counted {measured}"),
            ("re-run with same model + prompt makes 0 LLM calls", second == 0, f"{second} calls"),
            ("every Gold label is bug / billing / other", bad == 0, f"{bad} invalid row(s)"),
            ("off-schema answers go to llm_label_quarantine", n_q >= 1, f"{n_q} quarantined"),
            ("new prompt version re-labels on purpose", third == len(tickets), f"{third} calls"),
            ("labels carry their prompt version", versions == [(old_version + "-bonus-check",)],
             f"{versions}"),
        ]
        for label, ok, detail in checks:
            print(f"  [{'OK ' if ok else 'XX '}] {label}" + ("" if ok else f"  ({detail})"))
        ok = all(c[1] for c in checks)
        print("BONUS " + ("PASS" if ok else "NOT YET"))
        return 0 if ok else 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
