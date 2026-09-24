from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from checkin import get_user_info
from utils.browser import verify_browser_login


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
	}


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
