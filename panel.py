import asyncio
import json
import time
from base64 import b64encode
from urllib.parse import quote

import httpx

_CACHE_TTL = 30  # seconds


class PanelClient:
    def __init__(self, url: str, username: str, password: str, name: str = "", proxy: str = ""):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.name = name
        # Normalize socks:// to socks5:// for httpx
        if proxy and proxy.lower().startswith("socks://"):
            proxy = "socks5://" + proxy[8:]
        self._client = httpx.AsyncClient(verify=False, timeout=30, proxy=proxy or None)
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        self._inbounds_cache: list[dict] | None = None
        self._inbounds_ts: float = 0

    async def _request(self, method: str, path: str, **kwargs):
        """Make a request with auto-login and re-login on 404 / transport error."""
        if not self._logged_in:
            await self._do_login()
        retry = False
        try:
            resp = await self._client.request(method, self.url + path, **kwargs)
            if resp.status_code == 404:
                retry = True
        except httpx.TransportError:
            retry = True
        if retry:
            await self._do_login()
            try:
                resp = await self._client.request(method, self.url + path, **kwargs)
            except httpx.TransportError as e:
                raise RuntimeError(f"Panel unreachable: {e}")
        return resp.json()

    async def _do_login(self):
        """Login with a lock to prevent concurrent login storms."""
        async with self._login_lock:
            await self.login()

    async def login(self):
        try:
            resp = await self._client.post(
                self.url + "/login",
                json={"username": self.username, "password": self.password},
            )
        except httpx.TransportError as e:
            self._logged_in = False
            raise RuntimeError(f"Panel unreachable: {e}")
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Login failed: {data.get('msg')}")
        self._logged_in = True

    async def list_inbounds(self) -> list[dict]:
        now = time.monotonic()
        if self._inbounds_cache is not None and now - self._inbounds_ts < _CACHE_TTL:
            return self._inbounds_cache
        data = await self._request("GET", "/panel/api/inbounds/list")
        result = data.get("obj") or []
        self._inbounds_cache = result
        self._inbounds_ts = time.monotonic()
        return result

    def invalidate_cache(self):
        """Clear cached inbounds so the next read fetches fresh data."""
        self._inbounds_cache = None

    async def get_online_clients(self) -> list[str]:
        data = await self._request("POST", "/panel/api/inbounds/onlines")
        return data.get("obj") or []

    async def add_client(self, inbound_id: int, client_dict: dict):
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client_dict]}),
        }
        data = await self._request("POST", "/panel/api/inbounds/addClient", json=payload)
        if not data.get("success"):
            raise RuntimeError(f"addClient failed: {data.get('msg')}")
        self.invalidate_cache()
        return data

    async def update_client(self, client_id: str, inbound_id: int, client_dict: dict):
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client_dict]}),
        }
        data = await self._request(
            "POST", f"/panel/api/inbounds/updateClient/{client_id}", json=payload
        )
        if not data.get("success"):
            raise RuntimeError(f"updateClient failed: {data.get('msg')}")
        self.invalidate_cache()
        return data

    async def reset_client_traffic(self, inbound_id: int, email: str):
        data = await self._request(
            "POST", f"/panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}"
        )
        if not data.get("success"):
            raise RuntimeError(f"resetClientTraffic failed: {data.get('msg')}")
        self.invalidate_cache()
        return data

    async def delete_client(self, inbound_id: int, client_id: str):
        data = await self._request(
            "POST", f"/panel/api/inbounds/{inbound_id}/delClient/{client_id}"
        )
        if not data.get("success"):
            raise RuntimeError(f"delClient failed: {data.get('msg')}")
        self.invalidate_cache()
        return data

    async def reset_all_client_traffics(self, inbound_id: int):
        data = await self._request(
            "POST", f"/panel/api/inbounds/resetAllClientTraffics/{inbound_id}"
        )
        if not data.get("success"):
            raise RuntimeError(f"resetAllClientTraffics failed: {data.get('msg')}")
        self.invalidate_cache()
        return data

    async def delete_depleted_clients(self, inbound_id: int):
        data = await self._request(
            "POST", f"/panel/api/inbounds/delDepletedClients/{inbound_id}"
        )
        if not data.get("success"):
            raise RuntimeError(f"delDepletedClients failed: {data.get('msg')}")
        self.invalidate_cache()
        return data

    async def find_client_by_email(self, email: str):
        """Search all inbounds for a client with matching email.

        Returns (client_dict, inbound_dict, traffic_dict) or (None, None, None).
        """
        inbounds = await self.list_inbounds()
        for inbound in inbounds:
            settings = json.loads(inbound.get("settings", "{}"))
            for client in settings.get("clients", []):
                if client.get("email", "").lower() == email.lower():
                    actual_email = client["email"]
                    traffic = None
                    for cs in inbound.get("clientStats") or []:
                        if cs.get("email") == actual_email:
                            traffic = cs
                            break
                    return client, inbound, traffic
        return None, None, None

    @staticmethod
    def get_client_id(client: dict, protocol: str) -> str:
        if protocol in ("vmess", "vless"):
            return client["id"]
        if protocol == "trojan":
            return client["password"]
        return client["email"]

    async def close(self):
        await self._client.aclose()


# ── Proxy link generation ────────────────────────────────────────────────────

def _stream_params(stream: dict) -> dict:
    """Build query parameters from streamSettings."""
    network = stream.get("network", "tcp")
    security = stream.get("security", "none")
    params: dict[str, str] = {"type": network, "security": security}

    # Network-specific
    if network == "tcp":
        header = stream.get("tcpSettings", {}).get("header", {})
        params["headerType"] = header.get("type", "none")
        if header.get("type") == "http":
            req = header.get("request", {})
            paths = req.get("path", ["/"])
            params["path"] = paths[0] if isinstance(paths, list) else paths
            hosts = req.get("headers", {}).get("Host", [])
            if hosts:
                params["host"] = hosts[0] if isinstance(hosts, list) else hosts
    elif network == "ws":
        ws = stream.get("wsSettings", {})
        params["path"] = ws.get("path", "/")
        params["host"] = ws.get("host", "") or ws.get("headers", {}).get("Host", "")
    elif network == "grpc":
        grpc = stream.get("grpcSettings", {})
        params["serviceName"] = grpc.get("serviceName", "")
        params["mode"] = grpc.get("mode", "gun")
    elif network == "httpupgrade":
        hu = stream.get("httpupgradeSettings", {})
        params["path"] = hu.get("path", "/")
        params["host"] = hu.get("host", "")
    elif network == "xhttp":
        xh = stream.get("xhttpSettings", {})
        params["path"] = xh.get("path", "/")
        params["host"] = xh.get("host", "")
    elif network == "kcp":
        kcp = stream.get("kcpSettings", {})
        params["headerType"] = kcp.get("header", {}).get("type", "none")
        if kcp.get("seed"):
            params["seed"] = kcp["seed"]

    # Security-specific
    if security == "tls":
        tls = stream.get("tlsSettings", {})
        params["sni"] = tls.get("serverName", "")
        tls_s = tls.get("settings", {})
        params["fp"] = tls_s.get("fingerprint", "")
        alpn = tls.get("alpn", [])
        if alpn:
            params["alpn"] = ",".join(alpn)
    elif security == "reality":
        reality = stream.get("realitySettings", {})
        names = reality.get("serverNames", [])
        params["sni"] = names[0] if names else ""
        rs = reality.get("settings", {})
        params["fp"] = rs.get("fingerprint", "")
        params["pbk"] = rs.get("publicKey", "")
        sids = reality.get("shortIds", [])
        params["sid"] = sids[0] if sids else ""
        spx = rs.get("spiderX", "")
        if spx:
            params["spx"] = spx

    return {k: v for k, v in params.items() if v}


def _encode_query(params: dict) -> str:
    return "&".join(f"{k}={quote(str(v), safe=',')}" for k, v in params.items())


def build_client_link(client: dict, inbound: dict, address: str) -> str:
    """Build a proxy URI (vless://, vmess://, trojan://, ss://) for a client."""
    protocol = inbound["protocol"]
    port = inbound["port"]
    remark = inbound.get("remark", "")
    settings = json.loads(inbound.get("settings", "{}"))
    stream = json.loads(inbound.get("streamSettings", "{}"))

    # externalProxy overrides address/port
    ext = stream.get("externalProxy", [])
    if ext:
        address = ext[0].get("dest", address)
        port = ext[0].get("port", port)

    tag = f"{remark}-{client.get('email', '')}"

    if protocol == "vless":
        params = _stream_params(stream)
        flow = client.get("flow", "")
        if flow:
            params["flow"] = flow
        return f"vless://{client['id']}@{address}:{port}?{_encode_query(params)}#{quote(tag)}"

    if protocol == "vmess":
        tls = stream.get("tlsSettings", {})
        tls_s = tls.get("settings", {})
        network = stream.get("network", "tcp")
        security = stream.get("security", "none")
        host, path, header_type = "", "", "none"
        if network == "ws":
            ws = stream.get("wsSettings", {})
            path = ws.get("path", "/")
            host = ws.get("host", "") or ws.get("headers", {}).get("Host", "")
        elif network == "grpc":
            path = stream.get("grpcSettings", {}).get("serviceName", "")
        elif network == "httpupgrade":
            hu = stream.get("httpupgradeSettings", {})
            path = hu.get("path", "/")
            host = hu.get("host", "")
        elif network == "tcp":
            header = stream.get("tcpSettings", {}).get("header", {})
            header_type = header.get("type", "none")
            if header_type == "http":
                req = header.get("request", {})
                p = req.get("path", ["/"])
                path = p[0] if isinstance(p, list) else p
                h = req.get("headers", {}).get("Host", [])
                host = (h[0] if isinstance(h, list) else h) if h else ""
        cfg = {
            "v": "2", "ps": tag, "add": address, "port": str(port),
            "id": client["id"], "aid": "0",
            "scy": client.get("security", "auto"),
            "net": network, "type": header_type,
            "host": host, "path": path,
            "tls": security if security == "tls" else "",
            "sni": tls.get("serverName", ""),
            "alpn": ",".join(tls.get("alpn", [])),
            "fp": tls_s.get("fingerprint", ""),
        }
        raw = json.dumps(cfg, separators=(",", ":"))
        return f"vmess://{b64encode(raw.encode()).decode()}"

    if protocol == "trojan":
        params = _stream_params(stream)
        return f"trojan://{client['password']}@{address}:{port}?{_encode_query(params)}#{quote(tag)}"

    if protocol == "shadowsocks":
        method = settings.get("method", "")
        server_pw = settings.get("password", "")
        client_pw = client.get("password", "")
        if "2022" in method:
            user_info = f"{method}:{server_pw}:{client_pw}"
        else:
            user_info = f"{method}:{client_pw}"
        encoded = b64encode(user_info.encode()).decode()
        return f"ss://{encoded}@{address}:{port}#{quote(tag)}"

    return ""
