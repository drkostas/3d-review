"""Who may post to a bench reached over Tailscale (`review3d.bench.access.tailnet_users`)."""
from review3d.bench import access

# What `tailscale whois` would say about each address in these tests.
WHO = {"100.64.0.5": ("alice@example.com", "laptop", []),
       "100.64.0.6": ("alice@example.com", "server", []),
       "100.64.0.7": ("bob@example.com", "phone", []),
       "100.64.0.8": ("tagged-devices", "printer", ["tag:iot"])}


def check(devices=None):
    return access.tailnet_users(["alice@example.com"], devices, ask=lambda ip: WHO.get(ip, (None, None, [])))


def served(login, xff):
    h = {"Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers", "X-Forwarded-For": xff}
    if login:
        h["Tailscale-User-Login"] = login
    return h


def test_a_program_on_this_machine_may_post():
    assert check()("127.0.0.1", {}) is None
    assert check()("::1", {}) is None
    assert check()("::ffff:127.0.0.1", {}) is None


def test_through_serve_the_login_serve_sets_decides():
    assert check()("127.0.0.1", served("alice@example.com", "100.64.0.5")) is None
    assert "bob@example.com may not post" in check()("127.0.0.1", served("bob@example.com", "100.64.0.7"))
    assert "no person behind it" in check()("127.0.0.1", served(None, "100.64.0.8"))


def test_through_serve_the_device_is_the_last_forwarded_address():
    ok = check(["laptop"])
    assert ok("127.0.0.1", served("alice@example.com", "100.64.0.5")) is None
    assert "not from server" in ok("127.0.0.1", served("alice@example.com", "100.64.0.6"))
    # an address the caller put in front of the one Serve adds changes nothing
    assert ok("127.0.0.1", served("alice@example.com", "100.64.0.6, 100.64.0.5")) is None


def test_a_direct_tailnet_caller_is_asked_of_whois_and_its_headers_are_ignored():
    forged = {"Tailscale-User-Login": "alice@example.com"}
    assert check()("100.64.0.5", {}) is None
    assert "bob@example.com may not post" in check()("100.64.0.7", forged)
    assert "tagged device" in check()("100.64.0.8", forged)
    assert "does not know" in check()("100.64.0.9", forged)
    assert "not from server" in check(["laptop"])("100.64.0.6", {})


def test_any_other_address_is_refused():
    assert "neither this machine nor a Tailscale address" in check()("192.0.2.4", {})


def test_whois_answers_are_kept_for_a_while():
    asked = []
    may = access.tailnet_users(["alice@example.com"], ask=lambda ip: asked.append(ip) or WHO[ip])
    for _ in range(3):
        assert may("100.64.0.5", {}) is None
    assert asked == ["100.64.0.5"]
