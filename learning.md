# Learning Log

## 2026-02-04: IB Gateway API Connection via Socat Proxy

### Problem

Local Docker setup couldn't connect to IB Gateway API. TCP connection succeeded but gateway returned 0 bytes and closed the connection immediately.

### Root Cause

IB Gateway only accepts API connections from `localhost` (127.0.0.1) by default. The `TrustedIPs=127.0.0.1` setting in `jts.ini` restricts API access.

The gnzsnz/ib-gateway Docker image uses **socat** as a proxy to work around this:

```
Container internal:
  socat TCP-LISTEN:4003,fork TCP:127.0.0.1:4001  (live)
  socat TCP-LISTEN:4004,fork TCP:127.0.0.1:4002  (paper)
```

External connections to ports 4003/4004 are forwarded to localhost:4001/4002 inside the container, which the gateway trusts.

### The Mistake

Local `.env` had `TWS_DOCKER_PORT=4002`, connecting directly to the gateway port instead of the socat proxy port (4004 for paper trading).

### Solution

1. Set `TWS_DOCKER_PORT=4004` in `.env` for paper trading (4003 for live)
2. Expose ports 4003 and 4004 in `docker-compose.yml`:

```yaml
ports:
  - "4001:4001"  # Live API (direct - not recommended)
  - "4002:4002"  # Paper API (direct - not recommended)
  - "4003:4003"  # Live API via socat proxy
  - "4004:4004"  # Paper API via socat proxy
```

### Port Reference

| Mode  | Direct Port | Socat Proxy Port | Use This |
|-------|-------------|------------------|----------|
| Live  | 4001        | 4003             | 4003     |
| Paper | 4002        | 4004             | 4004     |

### Key Insight

When debugging "connection accepted but immediately closed" issues with IB Gateway, check:
1. Is the client connecting through socat proxy or directly?
2. Is the connecting IP in `TrustedIPs`?
3. Is `AcceptIncomingConnectionAction` set? (only helps for popup dialogs, not the TrustedIPs restriction)
