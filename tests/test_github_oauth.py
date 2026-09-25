import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

import checkin
from utils import github_oauth
from utils.browser import BrowserLoginResult
from utils.config import AccountConfig, AppConfig, ProviderConfig, load_accounts_config, validate_github_cookies

COOKIE = {
	'name': 'user_session',
	'value': 'private-github-session',
	'domain': 'github.com',
	'path': '/',
	'secure': True,
}


def test_github_config_validates_session_and_identity(monkeypatch, capsys):
	account = {'provider': 'agentrouter', 'api_user': '123', 'github_cookies': [COOKIE]}
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([account]))
	accounts = load_accounts_config()
	assert accounts is not None and accounts[0].github_cookies == [COOKIE]
	for bad in (
		{**COOKIE, 'domain': 'attacker.example'},
		{**COOKIE, 'expires': 1},
		{**COOKIE, 'url': 'https://github.com'},
	):
		with pytest.raises(ValueError):
			validate_github_cookies([bad])
	account.pop('api_user')
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([account]))
	assert load_accounts_config() is None
	assert COOKIE['value'] not in capsys.readouterr().out


def test_powershell_merged_accounts_keep_credentials_and_validation(monkeypatch, capsys):
	github = {'provider': 'agentrouter', 'api_user': '123', 'github_cookies': [COOKIE]}
	data = [
		{'email': 'test@example.com', 'password': 'fake-password'},
		{'Count': 1, 'value': [github]},
		{'Count': 1, 'value': [{**github, 'api_user': '456'}]},
	]
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps(data))
	accounts = load_accounts_config()
	assert accounts is not None and len(accounts) == 3
	assert accounts[0].has_login_credentials()
	assert [account.api_user for account in accounts] == [None, '123', '456']
	assert all(account.github_cookies == [COOKIE] for account in accounts[1:])
	assert [account.name for account in accounts] == ['Account 1', 'Account 2', 'Account 3']
	for invalid in (
		{'Count': 2, 'value': [github]},
		{'Count': 1, 'value': github},
		{'Count': 1, 'value': [None]},
		{'Count': 1, 'value': [{**github, 'api_user': None}]},
		{'Count': 1, 'value': [{**github, 'github_cookies': [{**COOKIE, 'domain': 'attacker.example'}]}]},
	):
		monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([data[0], invalid]))
		assert load_accounts_config() is None
	assert COOKIE['value'] not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['rewarded', 'unconfirmed', 'wrong_account', 'expired', 'callback_failed'])
async def test_oauth_uses_fresh_state_and_scoped_cookies(monkeypatch, case):
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.test')
	response = SimpleNamespace(
		url=provider.domain + '/api/oauth/github?code=private-code&state=private-state',
		status=200,
		json=AsyncMock(
			return_value={
				'success': case != 'callback_failed',
				'data': {'id': 456 if case == 'wrong_account' else 123, 'checked_in': case == 'rewarded'},
			}
		),
	)
	value = asyncio.get_running_loop().create_future()
	value.set_result(response)
	response_context = AsyncMock()
	response_context.__aenter__.return_value = SimpleNamespace(value=value)
	if case == 'expired':
		response_context.__aexit__.side_effect = TimeoutError
	page = AsyncMock()
	page.url = 'https://github.com/login' if case == 'expired' else provider.domain + '/console'
	page.expect_response = MagicMock(return_value=response_context)
	page.evaluate.return_value = {
		'status': {'success': True, 'data': {'github_client_id': 'public-client'}},
		'state': {'success': True, 'data': 'new-state'},
	}
	context = AsyncMock()
	context.new_page.return_value = page
	context.cookies.return_value = [{'name': 'session', 'value': 'agent-session'}]
	launch = AsyncMock(return_value=context)
	monkeypatch.setattr(github_oauth, 'launch_login_context', launch)
	monkeypatch.setattr(github_oauth, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(github_oauth, 'navigate_login_page', AsyncMock())
	if case in {'wrong_account', 'expired'}:
		with pytest.raises(github_oauth.GitHubLoginRequired):
			await github_oauth.login_with_github('test', provider, [COOKIE], '123')
	elif case == 'callback_failed':
		with pytest.raises(RuntimeError):
			await github_oauth.login_with_github('test', provider, [COOKIE], '123')
	else:
		result = await github_oauth.login_with_github('test', provider, [COOKIE], '123')
		assert result.cookies == {'session': 'agent-session'}
		assert result.checked_in is (case == 'rewarded')
		context.cookies.assert_awaited_once_with(provider.domain)
	assert launch.call_args.args[0].persist_profile is False
	context.add_cookies.assert_awaited_once_with([COOKIE])
	context.close.assert_awaited_once()
	query = parse_qs(urlsplit(page.goto.call_args.args[0]).query)
	assert query == {'client_id': ['public-client'], 'state': ['new-state'], 'scope': ['user:email']}
	predicate = page.expect_response.call_args.args[0]
	assert predicate(SimpleNamespace(url=provider.domain + '/api/oauth/github?code=one-time'))
	assert not predicate(SimpleNamespace(url='https://other.test/api/oauth/github?code=one-time'))


@pytest.mark.asyncio
@pytest.mark.parametrize('checked_in, expected', [(True, 'rewarded'), (False, 'unconfirmed'), (None, 'unconfirmed')])
@pytest.mark.parametrize('balance_available', [True, False])
async def test_callback_controls_reward_status_not_balance_query(monkeypatch, checked_in, expected, balance_available):
	account = AccountConfig(cookies=None, provider='agentrouter', api_user='123', github_cookies=[COOKIE])
	config = AppConfig({'agentrouter': ProviderConfig('agentrouter', 'https://agentrouter.test', sign_in_path=None)})
	monkeypatch.setattr(checkin, 'login_with_github', AsyncMock(return_value=BrowserLoginResult({}, '123', checked_in)))
	after = (
		{'success': True, 'quota': 10, 'used_quota': 1}
		if balance_available
		else {'success': False, 'error': 'WAF HTML'}
	)
	monkeypatch.setattr(
		checkin, 'run_check_in_requests', lambda *args, **kwargs: (balance_available, after.copy(), after)
	)
	success, before, result = await checkin.check_in_account(account, 0, config)
	assert success and before is None
	assert result['check_in_status'] == expected


@pytest.mark.asyncio
async def test_export_uses_context_oauth_callback_and_keeps_browser_open_for_retry(monkeypatch, capsys):
	from scripts import export_github_session as exporter

	context = AsyncMock()
	context.on = MagicMock()
	context.remove_listener = MagicMock()
	context.cookies.return_value = [COOKIE]
	provider = ProviderConfig('agentrouter', 'https://agentrouter.test')
	monkeypatch.setattr(exporter.AppConfig, 'load_from_env', lambda: AppConfig({'agentrouter': provider}))
	monkeypatch.setattr(exporter, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(exporter, 'prepare_browser_page', AsyncMock())
	presses = 0

	async def press_enter(*args):
		nonlocal presses
		presses += 1
		assert presses <= 2
		context.close.assert_not_awaited()
		response = SimpleNamespace(
			url=('https://other.test' if presses == 1 else provider.domain) + '/api/oauth/github?code=private-code',
			status=200,
			json=AsyncMock(return_value={'success': True, 'data': {'id': 123}}),
		)
		await context.on.call_args.args[1](response)

	monkeypatch.setattr(exporter.asyncio, 'to_thread', press_enter)
	result = await exporter.export_session('AgentRouter')
	assert presses == 2 and result['api_user'] == '123' and result['github_cookies'] == [COOKIE]
	context.close.assert_awaited_once()
	context.remove_listener.assert_called_once()
	output = capsys.readouterr().out
	assert COOKIE['value'] not in output and 'private-code' not in output
	assert '浏览器保持打开' in output


@pytest.mark.asyncio
async def test_confirmed_reward_is_not_lost_when_balance_is_blocked(monkeypatch):
	account = AccountConfig(None, name='AgentRouter', provider='agentrouter')
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig({}))
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	monkeypatch.setattr(
		checkin,
		'check_in_account',
		AsyncMock(
			return_value=(
				True,
				None,
				{'success': False, 'error': 'WAF HTML: balance unavailable', 'check_in_status': 'rewarded'},
			)
		),
	)
	notify = MagicMock()
	monkeypatch.setattr(checkin.notify, 'push_message', notify)
	with pytest.raises(SystemExit) as exit_info:
		await checkin.main()
	assert exit_info.value.code == 0
	assert '签到奖励已由登录回调确认' in notify.call_args.args[1]
	assert 'WAF HTML: balance unavailable' in notify.call_args.args[1]


@pytest.mark.asyncio
async def test_expired_github_account_does_not_block_other_accounts(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps(
			[
				{'provider': 'agentrouter', 'api_user': '123', 'github_cookies': [{**COOKIE, 'expires': 1}]},
				{'provider': 'anyrouter', 'email': 'test@example.com', 'password': 'fake-password'},
			]
		),
	)
	accounts = load_accounts_config()
	assert accounts is not None and len(accounts) == 2
	once = AsyncMock(return_value=(True, None, None))
	monkeypatch.setattr(checkin, '_check_in_account_once', once)
	assert (await checkin.check_in_account(accounts[0], 0, AppConfig({})))[0] is False
	once.assert_not_awaited()
	assert (await checkin.check_in_account(accounts[1], 1, AppConfig({})))[0] is True
