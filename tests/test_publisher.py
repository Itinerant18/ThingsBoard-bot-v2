from app.ingest.publisher import routing_key_for


def test_routing_key_per_customer() -> None:
    assert routing_key_for("BOI") == "customer.BOI"
    assert routing_key_for(None) == "customer._unknown"
    assert routing_key_for("") == "customer._unknown"


def test_routing_key_sanitizes_hostile_values() -> None:
    # Dots/#/* are AMQP routing syntax; a hostile customer_id must not inject segments.
    assert routing_key_for("BOI.evil.#") == "customer.BOI_evil__"
    assert routing_key_for("a b*c") == "customer.a_b_c"
