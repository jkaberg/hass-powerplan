"""D-0231: the engine's `Section` and the store's `Section` are the same ten names.

`core/engine.py` may not import `storage.py` (INV-2), so the vocabulary is spelled
twice and this test is what keeps the two spellings one.
"""

from __future__ import annotations

from custom_components.powerplan.core.engine import Section as EngineSection
from custom_components.powerplan.storage import Section as StoreSection


def test_the_engine_and_the_store_name_the_same_sections() -> None:
    """One vocabulary, two files, zero drift (D7 §2, §7)."""
    assert {s.value for s in EngineSection} == {s.value for s in StoreSection}
