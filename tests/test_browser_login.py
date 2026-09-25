from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import checkin
from checkin import get_user_info
from utils.browser import BrowserLoginResult, verify_browser_login
from utils.config import AccountConfig, AppConfig, ProviderConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'status, payload, valid',
	[
		(200, {'success': True, 'data': {'id': 123}}, True),
		(401, {'success': False}, False),
		(200, {'success': False, 'id': 123}, False),
		(200, None, False),
	],
)
async def test_verify_logged_in_console_without_automatic_user_request(status, payload, valid):
	# 控制台已登录，但不再自动请求 /api/user/self；旧的 response 监听会超时。
	page = AsyncMock()
	page.url = 'https://agentrouter.test/console/token'
	page.on = MagicMock()
	page.remove_listener = MagicMock()
	page.evaluate.return_value = {'status': status, 'payload': payload, 'hasUserId': True}
	profile = await verify_browser_login(page, 'https://agentrouter.test/console', 10)
	assert profile == ({'id': 123} if valid else None)
	page.goto.assert_not_awaited()
	page.evaluate.assert_awaited_once()
	assert page.evaluate.call_args.args[1]['url'] == 'https://agentrouter.test/api/user/self'


@pytest.mark.asyncio
async def test_verify_login_uses_provider_endpoint_and_header():
	page = AsyncMock()
	page.url = 'https://custom.test/login'
	page.evaluate.return_value = {'status': 200, 'payload': {'id': 456}, 'hasUserId': True}
	assert await verify_browser_login(
		page, 'https://custom.test/console', 1000, user_info_path='/api/profile', api_user_key='x-user-id'
	) == {'id': 456}
	page.goto.assert_awaited_once()
	assert page.evaluate.call_args.args[1] == {
		'url': 'https://custom.test/api/profile',
		'apiUserKey': 'x-user-id',
		'timeout': 1000,
		'accessToken': None,
	}


@pytest.mark.asyncio
@pytest.mark.parametrize('token', ['private-access-token', None])
async def test_email_login_carries_scoped_token_through_verification_and_checkin(monkeypatch, capsys, token):
	provider = ProviderConfig('custom', 'https://custom.test')
	account = AccountConfig(None, provider='custom', email='test@example.com', password='fake-password')
	page = AsyncMock()
	page.url = provider.domain + ('/dashboard' if token else '/console')
	page.evaluate.return_value = {'status': 200, 'payload': {'success': True, 'data': {'id': 123}}}
	context = AsyncMock()
	context.on = MagicMock()
	context.remove_listener = MagicMock()

	async def close():
		context.remove_listener.assert_called_once_with('response', context.on.call_args.args[1])

	context.close.side_effect = close
	context.new_page.return_value = page
	context.cookies.return_value = [] if token else [{'name': 'session', 'value': 'fake-session'}]
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	for name in ('prepare_browser_page', 'navigate_login_page', 'save_login_screenshot'):
		monkeypatch.setattr(checkin, name, AsyncMock())
	for name in ('is_logged_in', 'has_session_cookie'):
		monkeypatch.setattr(checkin, name, AsyncMock(return_value=False))

	async def login(*args, **kwargs):
		capture = context.on.call_args.args[1]
		for path in ('/api/user/login', '/api/user/auth/refresh'):
			await capture(
				SimpleNamespace(
					url=provider.domain + path,
					status=200,
					json=AsyncMock(return_value={'success': True, 'data': {'access_token': token}}),
				)
			)
		for url, status, success in (
			('https://other.test/api/user/login', 200, True),
			(provider.domain + '/api/status', 200, True),
			(provider.domain + '/api/user/login', 401, True),
			(provider.domain + '/api/user/login', 200, False),
		):
			await capture(
				SimpleNamespace(
					url=url,
					status=status,
					json=AsyncMock(return_value={'success': success, 'data': {'access_token': 'untrusted-token'}}),
				)
			)

	monkeypatch.setattr(checkin, 'login_with_email_form', login)
	requests = []

	def handle(request):
		requests.append(request)
		assert request.url.host == 'custom.test'
		assert request.headers.get('authorization') == (f'Bearer {token}' if token else None)
		assert request.headers['new-api-user'] == '123'
		return httpx.Response(200, json={'success': True, 'data': {'id': 123, 'quota': 500000}})

	client = httpx.Client
	monkeypatch.setattr(checkin.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handle)))
	success, before, after = await checkin.check_in_account(account, 0, AppConfig({'custom': provider}))
	assert success and before['success'] and after['success']
	assert [request.method for request in requests] == ['GET', 'POST', 'GET']
	assert page.evaluate.call_args.args[1]['accessToken'] == token
	page.goto.assert_not_awaited()
	context.close.assert_awaited_once()
	context.remove_listener.assert_called_once_with('response', context.on.call_args.args[1])
	assert 'private-access-token' not in capsys.readouterr().out
	assert 'private-access-token' not in repr(BrowserLoginResult({}, access_token='private-access-token'))


@pytest.mark.asyncio
async def test_waf_200_is_reported_without_exposing_response_body(capsys):
	page = AsyncMock()
	page.url = 'https://agentrouter.test/console'
	page.evaluate.return_value = {
		'status': 200,
		'payload': None,
		'hasUserId': True,
		'isHtml': True,
		'isWaf': True,
	}
	assert await verify_browser_login(page, page.url, 1000) is None
	assert 'HTML/WAF' in capsys.readouterr().out
	client = MagicMock()
	client.get.return_value = httpx.Response(200, text='<meta name="aliyun_waf_aa" content="private-challenge">')
	result = get_user_info(client, {}, 'https://agentrouter.test/api/user/self')
	assert not result['success'] and 'HTML/WAF' in result['error']
	assert 'private-challenge' not in result['error']
