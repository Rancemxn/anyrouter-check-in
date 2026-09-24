"""代理配置：读取环境变量并供浏览器 / HTTP 客户端使用。"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

import httpx

_candidate_cache: dict[tuple[str, ...], list[str]] = {}


async def proxy_candidates(test_urls: list[str]) -> list[str]:
	"""按目标站点测试每个节点；mihomo delay 成功后再按总延迟排序。"""
	key = tuple(test_urls)
	if key in _candidate_cache:
		return _candidate_cache[key]
	semaphore = asyncio.Semaphore(8)
	async with httpx.AsyncClient(
		base_url=os.environ['CHECKIN_PROXY_CONTROLLER'],
		headers={'Authorization': f'Bearer {os.environ["CHECKIN_PROXY_SECRET"]}'},
		timeout=12,
		trust_env=False,
	) as client:
		response = await client.get('/proxies/CHECKIN')
		response.raise_for_status()
		nodes = [n for n in response.json()['all'] if n not in {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS'}]

		async def probe(node: str) -> tuple[int, str] | None:
			async with semaphore:
				try:
					total = 0
					for url in test_urls:
						response = await client.get(
							f'/proxies/{quote(node, safe="")}/delay', params={'url': url, 'timeout': 8000}
						)
						response.raise_for_status()
						delay = response.json().get('delay')
						if not isinstance(delay, int) or delay <= 0:
							return None
						total += delay
					return total, node
				except (httpx.HTTPError, ValueError, TypeError):
					return None

		results = await asyncio.gather(*(probe(node) for node in nodes))
		candidates = [node for _, node in sorted(result for result in results if result is not None)]
		print(f'[PROXY] {len(candidates)}/{len(nodes)} nodes passed target checks')
		_candidate_cache[key] = candidates
		return candidates


async def activate_proxy(node: str, test_urls: list[str]) -> bool:
	"""固定出口，并通过本地代理再次验证真实 HTTP 请求；不输出订阅或节点名称。"""
	try:
		async with httpx.AsyncClient(
			base_url=os.environ['CHECKIN_PROXY_CONTROLLER'],
			headers={'Authorization': f'Bearer {os.environ["CHECKIN_PROXY_SECRET"]}'},
			timeout=10,
			trust_env=False,
		) as controller:
			response = await controller.put('/proxies/CHECKIN', json={'name': node})
			response.raise_for_status()
		async with httpx.AsyncClient(
			proxy=os.environ['CHECKIN_PROXY_URL'], timeout=15, follow_redirects=True, trust_env=False
		) as client:
			for url in test_urls:
				response = await client.get(url)
				if response.status_code != 200:
					return False
		return True
	except httpx.HTTPError:
		return False


def get_proxy_server(*, use_proxy: bool = True) -> str | None:
	"""按平台配置读取 CHECKIN_PROXY_URL；use_proxy=False 时不返回代理地址。"""
	if not use_proxy:
		return None
	server = os.getenv('CHECKIN_PROXY_URL', '').strip()
	return server or None


def get_playwright_proxy(*, use_proxy: bool = True) -> dict[str, str] | None:
	server = get_proxy_server(use_proxy=use_proxy)
	if not server:
		return None
	return {'server': server}
