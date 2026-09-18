#!/usr/bin/env python3
"""
cf-tunnel-setup.py — provision the careconnect Cloudflare tunnel + DNS, idempotently.

Reproducible setup for the haizel.online internet exposure. Reads the API token +
zone from an env file (default ~/.config/careconnect/cloudflare.env) which must
provide CF_API_TOKEN, CF_ZONE and CF_ACCOUNT_ID. The token needs:
    Zone:DNS:Edit + Zone:Read   on the zone
    Account:Cloudflare Tunnel:Edit

Subcommands:
    show              print current zone DNS + tunnels
    wipe-dns          DELETE every deletable DNS record on the zone (keeps NS/SOA)
    setup             delete old tunnels named like --old-name, create the tunnel,
                      push ingress config, create proxied CNAMEs, and write the
                      connector token to --token-out (0600)

Hostname → origin map (edit HOSTS below to change the topology):
    haizel.online, www, app, api   -> https://caddy:443  (dashboard + /api + /ws)
    ota.haizel.online              -> http://xiaozhi-server:8003  (device OTA)
    ws.haizel.online               -> http://xiaozhi-server:8000  (device voice WS)

The connector itself runs as the `cloudflared` service in deploy/docker-compose.yml
on the Jetson, using the token this script emits.
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

CF_API = "https://api.cloudflare.com/client/v4"
ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")  # set in the env file (CF_ACCOUNT_ID)

# hostname (label or "@" for apex) -> (origin service, extra originRequest)
# Caddy serves `tls internal` certs only for the haizel hostnames, so the
# connector must present SNI=haizel.online (originServerName) or the TLS
# handshake fails with "tls: internal error". noTLSVerify ignores the mismatch.
_CADDY = {"noTLSVerify": True, "originServerName": "haizel.online"}
HOSTS = [
    ("@",   "https://caddy:443",          _CADDY),
    ("www", "https://caddy:443",          _CADDY),
    ("app", "https://caddy:443",          _CADDY),
    ("api", "https://caddy:443",          _CADDY),
    ("ota", "http://xiaozhi-server:8003", {}),
    ("ws",  "http://xiaozhi-server:8000", {}),
]


def load_env(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)
    tok = os.environ.get("CF_API_TOKEN")
    zone = os.environ.get("CF_ZONE")
    global ACCOUNT_ID
    ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
    if not tok or not zone or not ACCOUNT_ID:
        sys.exit("CF_API_TOKEN, CF_ZONE and CF_ACCOUNT_ID must be set (env file or environment)")
    return tok, zone


def api(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CF_API + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {body}")


def must(res, what):
    if not res.get("success"):
        raise SystemExit(f"{what} failed: {res.get('errors')}")
    return res


def zone_id(token, zone):
    r = must(api("GET", f"/zones?name={zone}", token), "zone lookup")
    if not r["result"]:
        raise SystemExit(f"zone not found: {zone}")
    return r["result"][0]["id"]


def fqdn(label, zone):
    return zone if label in ("@", "", zone) else f"{label}.{zone}"


def cmd_show(token, zone):
    zid = zone_id(token, zone)
    print(f"# zone {zone} ({zid})")
    r = must(api("GET", f"/zones/{zid}/dns_records?per_page=100", token), "dns list")
    for rec in r["result"]:
        print(f"  DNS {rec['type']:6} {rec['name']:32} -> {rec['content']:45} proxied={rec.get('proxied')}")
    t = must(api("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel?is_deleted=false", token), "tunnel list")
    for tn in t["result"]:
        print(f"  TUNNEL {tn['name']:24} {tn['id']} status={tn.get('status')}")


def cmd_wipe_dns(token, zone):
    zid = zone_id(token, zone)
    r = must(api("GET", f"/zones/{zid}/dns_records?per_page=100", token), "dns list")
    for rec in r["result"]:
        api("DELETE", f"/zones/{zid}/dns_records/{rec['id']}", token)
        print(f"  deleted {rec['type']} {rec['name']} -> {rec['content']}")
    print(f"wiped {len(r['result'])} record(s)")


def cmd_setup(token, zone, old_name, token_out):
    zid = zone_id(token, zone)

    # 1) delete old tunnels matching old_name (must have 0 active conns to delete;
    #    use cleanup to evict stale connections first).
    t = must(api("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel?is_deleted=false", token), "tunnel list")
    for tn in t["result"]:
        if tn["name"] == old_name or tn["name"] == "careconnect-jetson":
            tid = tn["id"]
            api("DELETE", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/connections", token)  # evict conns
            res = api("DELETE", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}", token)
            print(f"  deleted old tunnel {tn['name']} ({tid}): success={res.get('success')}")

    # 2) create the new tunnel (remotely-managed: config_src=cloudflare)
    secret = base64.b64encode(os.urandom(32)).decode()
    body = {"name": "careconnect-jetson", "tunnel_secret": secret, "config_src": "cloudflare"}
    r = must(api("POST", f"/accounts/{ACCOUNT_ID}/cfd_tunnel", token, body), "tunnel create")
    tid = r["result"]["id"]
    print(f"  created tunnel careconnect-jetson ({tid})")

    # 3) connector token
    tok = must(api("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/token", token), "tunnel token")
    connector_token = tok["result"]
    with open(token_out, "w") as f:
        os.chmod(token_out, 0o600)
        f.write(connector_token + "\n")
    print(f"  connector token written to {token_out} (0600)")

    # 4) ingress config
    ingress = []
    for label, service, extra in HOSTS:
        rule = {"hostname": fqdn(label, zone), "service": service}
        if extra:
            rule["originRequest"] = extra
        ingress.append(rule)
    ingress.append({"service": "http_status:404"})
    cfg = {"config": {"ingress": ingress}}
    must(api("PUT", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/configurations", token, cfg), "ingress")
    print(f"  ingress set ({len(ingress)} rules)")

    # 5) DNS CNAMEs -> <tid>.cfargotunnel.com (proxied)
    target = f"{tid}.cfargotunnel.com"
    for label, _, _ in HOSTS:
        name = fqdn(label, zone)
        body = {"type": "CNAME", "name": name, "content": target, "proxied": True, "ttl": 1}
        api("POST", f"/zones/{zid}/dns_records", token, body)
        print(f"  DNS CNAME {name} -> {target} (proxied)")

    print(f"\nDONE. Tunnel id: {tid}")
    print(f"Run the connector with this token (in deploy/docker-compose.yml cloudflared svc).")


def cmd_update_ingress(token, zone):
    """Re-PUT the ingress config (HOSTS) onto the existing careconnect-jetson tunnel."""
    t = must(api("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel?is_deleted=false", token), "tunnel list")
    tid = next((x["id"] for x in t["result"] if x["name"] == "careconnect-jetson"), None)
    if not tid:
        raise SystemExit("careconnect-jetson tunnel not found")
    ingress = []
    for label, service, extra in HOSTS:
        rule = {"hostname": fqdn(label, zone), "service": service}
        if extra:
            rule["originRequest"] = extra
        ingress.append(rule)
    ingress.append({"service": "http_status:404"})
    must(api("PUT", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/configurations", token,
             {"config": {"ingress": ingress}}), "ingress update")
    print(f"ingress updated on {tid} ({len(ingress)} rules)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["show", "wipe-dns", "setup", "update-ingress"])
    ap.add_argument("--env", default=os.path.expanduser("~/.config/careconnect/cloudflare.env"))
    ap.add_argument("--old-name", default="careconnect", help="old tunnel name to delete in setup")
    ap.add_argument("--token-out", default=os.path.expanduser("~/.config/careconnect/cf-tunnel-token"))
    args = ap.parse_args()
    token, zone = load_env(args.env)
    if args.command == "show":
        cmd_show(token, zone)
    elif args.command == "wipe-dns":
        cmd_wipe_dns(token, zone)
    elif args.command == "setup":
        cmd_setup(token, zone, args.old_name, args.token_out)
    elif args.command == "update-ingress":
        cmd_update_ingress(token, zone)


if __name__ == "__main__":
    main()
