"""D13 §19 11 - `tools/vat_check.py` flags a changed rate and passes today's.

`tests/fixtures/tedb/electricity-2026-09-24.xml` is TEDB's own answer, captured
for the 27 member states and the `SUPPLY_ELECTRICITY` category - the
query §9.1 was read from. No test touches the network (D9).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from tools import vat_check

CAPTURED = Path(__file__).resolve().parents[1] / "fixtures" / "tedb" / "electricity-2026-09-24.xml"
READ = date(2026, 9, 24)


def test_11_the_captured_answer_is_every_member_states_rate() -> None:
    """27 states; Belgium's reduced 6 %, not its 21 %; Germany's PV exemption ignored."""
    rates = vat_check.parse(CAPTURED.read_bytes())
    assert len(rates) == 27
    assert rates["BE"] == Decimal("0.06")
    assert rates["DE"] == Decimal("0.19")
    assert rates["EL"] == Decimal("0.06")


def test_11_todays_answer_passes() -> None:
    """Every module agrees with TEDB, IE, CY and MT as §9.1 documents them."""
    assert vat_check.differences(vat_check.parse(CAPTURED.read_bytes()), READ) == []


def test_11_a_changed_rate_is_flagged() -> None:
    """Finland back at 24 % in TEDB: flagged, with the module's 25.5 % beside it."""
    body = CAPTURED.read_bytes().replace(b"<value>25.5</value>", b"<value>24.0</value>")
    rows = vat_check.differences(vat_check.parse(body), READ)
    assert rows == [("FI", Decimal("0.24"), Decimal("0.255"))]
    assert "| FI | 24 % | 25.5 % |" in vat_check.report(rows, READ)


def test_11_a_known_difference_that_moves_is_flagged() -> None:
    """Ireland's row is expected at TEDB's 23 %; at anything else it is read again."""
    body = CAPTURED.read_bytes().replace(
        b"<memberState>IE</memberState><type>STANDARD</type><rate><type>DEFAULT</type><value>23.0",
        b"<memberState>IE</memberState><type>STANDARD</type><rate><type>DEFAULT</type><value>13.5",
    )
    assert [row[0] for row in vat_check.differences(vat_check.parse(body), READ)] == ["IE"]


def test_11_the_request_asks_for_electricity_on_the_day() -> None:
    """One call for every member state, the day and the category (§9.1)."""
    body = vat_check.request(READ, ["AT", "EL"]).decode()
    assert "<t:situationOn>2026-09-24</t:situationOn>" in body
    assert "<t:isoCode>EL</t:isoCode>" in body
    assert "SUPPLY_ELECTRICITY" in body


def test_11_the_report_warns_and_never_fails(capsys: object) -> None:
    """Exit 0 from a saved answer."""
    assert vat_check.main(["--at", "2026-09-24", "--response", str(CAPTURED)]) == 0
