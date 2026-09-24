from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from utils import proxy
from utils.config import AccountConfig, AppConfig, ProviderConfig
from utils.github_oauth import GitHubLoginRequired


@pytest.mark.asyncio
async def test_screen_nodes_for_both_sites_and_verify_selected_proxy(monkeypatch):
	monkeypatch.setenv('CHECKIN_PROXY_CONTROLLER', 'http://127.0.0.1:9090')
	monkeypatch.setenv('CHECKIN_PROXY_SECRET', 'local-controller-secret')
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	monkeypatch.setattr(proxy, '_candidate_cache', {})
	targets = ['https://github.com/login', 'https://agentrouter.test/login']
	probed = []
	selected = []

	def handle(request):
		if request.url.host == '127.0.0.1':
			assert request.headers['authorization'] == 'Bearer local-controller-secret'
			if request.method == 'PUT':
				selected.append(request.content)
				return httpx.Response(204)
			if request.url.path == '/proxies/CHECKIN':
				return httpx.Response(200, json={'all': ['DIRECT', 'slow', 'fast', 'github_only', 'dead']})
			node = request.url.path.split('/')[2]
			target = request.url.params['url']
			probed.append((node, target))
			if node == 'dead' or (node == 'github_only' and 'agentrouter' in target):
				return httpx.Response(504)
			return httpx.Response(200, json={'delay': 5 if node == 'fast' else 100})
		assert 'authorization' not in request.headers
		return httpx.Response(403 if request.url.host == 'agentrouter.test' else 200)

	client = httpx.AsyncClient
	monkeypatch.setattr(
		proxy.httpx,
		'AsyncClient',
		lambda **kwargs: client(
			**{k: v for k, v in kwargs.items() if k != 'proxy'}, transport=httpx.MockTransport(handle)
		),
	)
	assert await proxy.proxy_candidates(targets) == ['fast', 'slow']
	assert all((node, url) in probed for node in ['fast', 'slow', 'github_only'] for url in targets)
	assert not await proxy.activate_proxy('fast', targets)
	assert selected == [b'{"name":"fast"}']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['network', 'expired', 'no_nodes', 'startup_failed'])
async def test_retry_changes_nodes_but_stops_for_expired_credentials(monkeypatch, failure):
	monkeypatch.setenv('CHECKIN_PROXY_CONFIGURED', 'true')
	monkeypatch.setenv('CHECKIN_PROXY_URL', '' if failure == 'startup_failed' else 'http://127.0.0.1:7890')
	monkeypatch.setenv('CHECKIN_PROXY_CONTROLLER', 'http://127.0.0.1:9090')
	monkeypatch.setenv('CHECKIN_PROXY_ATTEMPTS', '3')
	provider = ProviderConfig('agentrouter', 'https://agentrouter.test', use_proxy=True)
	account = AccountConfig(
		None,
		provider='agentrouter',
		github_cookies=[
			{
				'name': 'user_session',
				'value': 'fake-session',
				'domain': 'github.com',
				'path': '/',
			}
		],
	)
	monkeypatch.setattr(
		checkin, 'proxy_candidates', AsyncMock(return_value=[] if failure == 'no_nodes' else ['A', 'B', 'C'])
	)
	activate = AsyncMock(return_value=True)
	monkeypatch.setattr(checkin, 'activate_proxy', activate)
	once = AsyncMock(
		side_effect=GitHubLoginRequired('refresh session')
		if failure == 'expired'
		else [
			(False, None, None),
			(True, None, {'success': True}),
		]
	)
	monkeypatch.setattr(checkin, '_check_in_account_once', once)
	result = await checkin.check_in_account(account, 0, AppConfig({'agentrouter': provider}))
	assert result[0] is (failure == 'network')
	if failure == 'network':
		assert [call.args[0] for call in activate.call_args_list] == ['A', 'B']
		assert once.await_count == 2
	elif failure == 'expired':
		assert once.await_count == 1
	else:
		once.assert_not_awaited()
		activate.assert_not_awaited()
