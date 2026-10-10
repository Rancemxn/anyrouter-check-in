import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import checkin
from utils import browser
from utils.config import AccountConfig, AppConfig, ProviderConfig
from utils.newapi import revoke_newapi_session


@pytest.fixture
def login_browser(monkeypatch):
	page = AsyncMock()
	page.url = 'https://custom.test/sign-in'
	context = AsyncMock()
	context.on = MagicMock()
	context.remove_listener = MagicMock()
	context.new_page.return_value = page
	context.cookies.return_value = []
	launch = AsyncMock(return_value=context)
	monkeypatch.setattr(checkin, 'launch_login_context', launch)
	for name in ('prepare_browser_page', 'navigate_login_page', 'save_login_screenshot'):
		monkeypatch.setattr(checkin, name, AsyncMock())
	for name in ('is_logged_in', 'has_session_cookie'):
		monkeypatch.setattr(checkin, name, AsyncMock(return_value=False))
	return page, context, launch


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['AUTH_SESSION_LIMIT', 'AUTH_SESSION_ISSUANCE_LIMIT'])
async def test_login_limit_stops_before_verification_and_proxy_retries(monkeypatch, capsys, login_browser, code):
	page, context, launch = login_browser
	provider = ProviderConfig('custom', 'https://custom.test', use_proxy=True)
	account = AccountConfig(None, provider='custom', email='test@example.com', password='test-password')
	monkeypatch.setenv('CHECKIN_PROXY_CONTROLLER', 'http://127.0.0.1:9090')
	monkeypatch.setattr(checkin, 'proxy_candidates', AsyncMock(return_value=['A', 'B', 'C']))
	activate = AsyncMock(return_value=True)
	monkeypatch.setattr(checkin, 'activate_proxy', activate)
	verify = AsyncMock()
	monkeypatch.setattr(checkin, 'verify_browser_login', verify)

	async def submit(*args, **kwargs):
		await context.on.call_args.args[1](
			SimpleNamespace(
				url='https://custom.test/api/user/login?turnstile=',
				status=409,
				json=AsyncMock(return_value={'success': False, 'code': code, 'message': 'private-response-body'}),
			)
		)
		assert kwargs['login_response_event'].is_set()

	monkeypatch.setattr(checkin, 'login_with_email_form', submit)
	success, _, after = await checkin.check_in_account(account, 0, AppConfig({'custom': provider}))
	assert not success and code in after['error']
	assert activate.await_count == launch.await_count == 1
	verify.assert_not_awaited()
	context.close.assert_awaited_once()
	assert 'private-response-body' not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['login', 'refresh', 'other_origin'])
async def test_failed_verification_releases_only_a_session_created_by_this_login(monkeypatch, login_browser, source):
	page, context, launch = login_browser
	provider = ProviderConfig('custom', 'https://custom.test')
	monkeypatch.setattr(checkin, 'verify_browser_login', AsyncMock(return_value=None))
	revoke = MagicMock(return_value=True)
	monkeypatch.setattr(checkin, 'revoke_newapi_session', revoke)

	async def submit(*args, **kwargs):
		url = {
			'login': 'https://custom.test/api/user/login',
			'refresh': 'https://custom.test/api/user/auth/refresh',
			'other_origin': 'https://other.test/api/user/login',
		}[source]
		await context.on.call_args.args[1](
			SimpleNamespace(
				url=url,
				status=200,
				json=AsyncMock(
					return_value={
						'success': True,
						'data': {'access_token': 'private-token', 'session': {'sid': 'owned-session'}},
					}
				),
			)
		)

	monkeypatch.setattr(checkin, 'login_with_email_form', submit)
	assert await checkin.login_with_credentials('test', provider, 'custom', 'email', 'password') is None
	if source == 'login':
		revoke.assert_called_once_with('https://custom.test', 'private-token', 'owned-session', proxy_url=None)
	else:
		revoke.assert_not_called()
	context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_finishes_on_login_response_without_console_navigation(monkeypatch):
	page = AsyncMock()
	button = AsyncMock()
	response_event = asyncio.Event()
	button.click.side_effect = lambda **kwargs: response_event.set()
	monkeypatch.setattr(browser, '_first_visible_locator', AsyncMock(return_value=button))
	monkeypatch.setattr(browser, '_wait_for_optional_load_state', AsyncMock(return_value=True))
	wait_logged_in = AsyncMock()
	monkeypatch.setattr(browser, 'wait_for_logged_in', wait_logged_in)
	await asyncio.wait_for(browser.submit_login_form(page, 15000, login_response_event=response_event), timeout=1)
	wait_logged_in.assert_not_awaited()


def test_checkin_network_failure_still_releases_owned_session(monkeypatch):
	provider = ProviderConfig('custom', 'https://custom.test', sign_in_path='/api/user/checkin')
	account = AccountConfig(None, provider='custom')
	monkeypatch.setattr(checkin, 'get_user_info', MagicMock(side_effect=RuntimeError('network failure')))
	revoke = MagicMock(return_value=True)
	monkeypatch.setattr(checkin, 'revoke_newapi_session', revoke)
	result = checkin.run_check_in_requests(
		{}, account, 'test', provider, access_token='private-token', created_session_id='owned-session'
	)
	assert result == (False, None, None)
	revoke.assert_called_once_with('https://custom.test', 'private-token', 'owned-session', proxy_url=None)


def test_session_cleanup_does_not_follow_redirects_or_expose_credentials(monkeypatch, capsys):
	requests = []

	def handle(request):
		requests.append(request)
		assert request.url.host == 'custom.test'
		assert request.url.path == '/api/user/sessions/owned-session'
		return httpx.Response(302, headers={'Location': 'https://other.test/collect'})

	client = httpx.Client
	monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handle)))
	assert not revoke_newapi_session('https://custom.test', 'private-token', 'owned-session')
	assert len(requests) == 1
	assert 'private-token' not in capsys.readouterr().out
