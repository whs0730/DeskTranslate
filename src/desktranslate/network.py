from __future__ import annotations

import ssl
import time
from urllib import error, parse, request


NETWORK_MODES = {"auto", "system", "direct"}


class NetworkClient:
    def __init__(self, mode: str = "auto") -> None:
        self.mode = mode

    def read(self, api_request: request.Request, timeout: float, *, allow_retry: bool = True) -> bytes:
        mode = self.mode
        deadline = time.monotonic() + timeout
        attempts = 2 if mode == "auto" and allow_retry else 1
        scheme = parse.urlsplit(api_request.full_url).scheme
        had_proxy = False
        for attempt in range(attempts):
            # urlopen 的全局 opener 会保留旧代理；VPN 切换后必须重新读取配置。
            proxies = {} if mode == "direct" or (attempt and had_proxy) else request.getproxies()
            had_proxy = bool(proxies.get(scheme))
            remaining = max(deadline - time.monotonic(), 0.01)
            attempt_timeout = min(timeout / 2, remaining) if attempts == 2 and attempt == 0 else remaining
            opener = request.build_opener(request.ProxyHandler(proxies))
            # ProxyHandler 会修改 Request 的代理目标，直连重试必须从原始 URL 重建。
            outgoing = request.Request(
                api_request.full_url,
                data=api_request.data,
                headers=dict(api_request.header_items()),
                method=api_request.get_method(),
            )
            try:
                with opener.open(outgoing, timeout=attempt_timeout) as response:
                    return response.read()
            except error.HTTPError as exc:
                # 限流、鉴权和服务端拒绝不是连接故障，不能通过换线路规避。
                exc.close()
                raise
            except (OSError, error.URLError) as exc:
                reason = exc.reason if isinstance(exc, error.URLError) else exc
                if isinstance(reason, ssl.SSLError) or attempt + 1 == attempts or time.monotonic() >= deadline:
                    raise
