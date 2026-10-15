"""Who may change things on a bench: checks to pass as `Bench(may_post=...)`.

The bench has no login, and what the reviewer submits, comments and publishes reaches the assistant
as the reviewer's own words. Anyone who can post to the bench can therefore speak in the reviewer's
name. When the bench is reached over Tailscale, Tailscale already knows who is asking, and
`tailnet_users` lets only the named Tailscale users (and, if given, only their named devices)
submit, comment, publish or rebuild. Reading the model and the threads stays open.

How each request is judged:

- From this machine (a loopback address) with no proxy headers: a program on this machine, allowed.
- From this machine through Tailscale Serve: Serve sets `Tailscale-User-Login` and removes any copy
  the caller sent, so that header names the person. A tagged device has no login and is refused.
  The device is the last address in `X-Forwarded-For`, which Serve adds.
- From a Tailscale address directly (the bench listening on its own Tailscale address): the address
  itself is asked of `tailscale whois`, which nothing in the request can change. Headers are not
  trusted on this path, because a direct caller can send any header it likes.
- From anywhere else: refused.

A check returns None to allow a post, or the reason it was refused, which the caller is shown.
"""
import ipaddress
import json
import subprocess
import time

TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def _ip(text):
    ip = ipaddress.ip_address(str(text).strip())
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def whois(ip, cli="tailscale"):
    """(login, device name, tags) for a Tailscale address, from `tailscale whois --json`, or
    (None, None, []) when Tailscale does not know it."""
    try:
        r = subprocess.run([cli, "whois", "--json", str(ip)], capture_output=True, text=True,
                           timeout=10)
        if r.returncode:
            return None, None, []
        d = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None, []
    node = d.get("Node") or {}
    name = str(node.get("Name") or "").split(".")[0] or None
    return (d.get("UserProfile") or {}).get("LoginName"), name, list(node.get("Tags") or [])


def tailnet_users(logins, devices=None, cli="tailscale", ask=None, ttl_s=300.0):
    """A `may_post` check that lets only these Tailscale users post, and only from these devices
    when `devices` is given (device names as Tailscale shows them, for example "laptop").

    `ask(ip)` answers like `whois`; it is there for tests and defaults to the `tailscale` CLI.
    Answers are kept for `ttl_s` seconds.
    """
    allowed = {str(x).lower() for x in logins}
    named = {str(x).lower() for x in devices} if devices else None
    cache = {}

    def lookup(ip):
        hit = cache.get(ip)
        if hit and time.monotonic() - hit[0] < ttl_s:
            return hit[1]
        got = ask(ip) if ask else whois(ip, cli)
        cache[ip] = (time.monotonic(), got)
        return got

    def judge(login, device, tags, where):
        if tags:
            return f"{where} is a tagged device ({', '.join(tags)}), not a person"
        if not login:
            return f"Tailscale does not know who {where} is"
        if login.lower() not in allowed:
            return f"{login} may not post to this bench"
        if named is not None and (device or "").lower() not in named:
            return f"{login} may post to this bench, but not from {device or where}"
        return None

    def may_post(client_ip, headers):
        try:
            ip = _ip(client_ip)
        except ValueError:
            return f"{client_ip} is not an address"
        if ip.is_loopback:
            served = headers.get("Tailscale-Headers-Info") or headers.get("X-Forwarded-For")
            if not served:
                return None                                  # a program on this machine
            login = headers.get("Tailscale-User-Login") or ""
            if not login:
                return "came through Tailscale Serve from a device with no person behind it"
            device = None
            chain = [p.strip() for p in str(headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
            if named is not None:
                if not chain:
                    return "came through Tailscale Serve without the caller's address"
                _l, device, _t = lookup(chain[-1])
            return judge(login, device, [], chain[-1] if chain else "the caller")
        if any(ip in net for net in TAILNET):
            login, device, tags = lookup(str(ip))
            return judge(login, device, tags, str(ip))
        return f"{ip} is neither this machine nor a Tailscale address"

    return may_post
