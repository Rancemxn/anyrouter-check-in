import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import checkin
from checkin import execute_check_in, generate_balance_hash
from utils.config import AccountConfig, AppConfig, ProviderConfig


@pytest.mark.parametrize(
	'path, status, payload, expected, fallback',
	[
		('/api/user/sign_in', 404, {'success': True}, True, True),
		('/api/user/sign_in', 404, {'success': False, 'message': '今日已签到'}, True, True),
		('/api/user/sign_in', 404, {'success': False, 'message': '签到功能未启用'}, False, True),
		('/api/user/sign_in', 200, {'success': True}, True, False),
		('/api/user/sign_in', 401, {'success': False}, False, False),
		('/api/custom-checkin', 404, {'success': True}, False, False),
	],
)
def test_checkin_uses_newapi_endpoint_only_when_legacy_endpoint_is_missing(path, status, payload, expected, fallback):
	requests = []

	def handle(request):
		requests.append(request)
		assert request.method == 'POST'
		assert request.headers['Authorization'] == 'Bearer test-token'
		assert request.headers['new-api-user'] == '123'
		return httpx.Response(status if len(requests) == 1 else 200, json=payload)

	provider = ProviderConfig('custom', 'https://custom.test', sign_in_path=path)
	with httpx.Client(transport=httpx.MockTransport(handle)) as client:
		assert (
			execute_check_in(client, 'custom', provider, {'Authorization': 'Bearer test-token', 'new-api-user': '123'})
			is expected
		)
	assert [str(request.url) for request in requests] == [provider.domain + path] + (
		[provider.domain + '/api/user/checkin'] if fallback else []
	)


def test_balance_hash_changes_when_quota_changes():
	before = {'account_1': {'quota': 100.0, 'used': 20.0}}
	after = {'account_1': {'quota': 125.0, 'used': 20.0}}

	assert generate_balance_hash(before) != generate_balance_hash(after)


def test_balance_hash_changes_when_used_quota_changes():
	before = {'account_1': {'quota': 100.0, 'used': 20.0}}
	after = {'account_1': {'quota': 100.0, 'used': 21.0}}

	assert generate_balance_hash(before) != generate_balance_hash(after)


def test_balance_hash_is_stable_for_equivalent_balances():
	left = {
		'account_2': {'quota': 50.0, 'used': 1.0},
		'account_1': {'quota': 100.0, 'used': 20.0},
	}
	right = {
		'account_1': {'used': 20.0, 'quota': 100.0},
		'account_2': {'used': 1.0, 'quota': 50.0},
	}

	assert generate_balance_hash(left) == generate_balance_hash(right)


@pytest.mark.parametrize('failure_only', [True, False])
@pytest.mark.parametrize('result', ['success', 'partial_failure', 'all_failed', 'exception', 'invalid_config'])
async def test_failure_notification_policy(monkeypatch, tmp_path, failure_only, result):
	monkeypatch.setenv('NOTIFY_ON_FAILURE_ONLY', str(failure_only).lower())
	accounts = [AccountConfig(None, name='Account 1'), AccountConfig(None, name='Account 2')]
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [] if result == 'invalid_config' else accounts)
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: AppConfig({}))
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(tmp_path / 'balance_hash.txt'))
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	before = {'success': True, 'quota': 10, 'used_quota': 0, 'display': 'Before'}
	after = {'success': True, 'quota': 35, 'used_quota': 0, 'display': 'After'}
	failed = (False, None, {'success': False, 'error': 'Login failed'})
	first = failed if result == 'all_failed' else (True, before, after)
	second = RuntimeError('Login error') if result == 'exception' else failed
	if result == 'success':
		second = (True, None, {**after, 'check_in_status': 'rewarded'})
	monkeypatch.setattr(checkin, 'check_in_account', AsyncMock(side_effect=[first, second]))
	push = MagicMock()
	monkeypatch.setattr(checkin.notify, 'push_message', push)
	with pytest.raises(SystemExit) as exit_info:
		await checkin.main()
	assert exit_info.value.code == (1 if result in {'all_failed', 'invalid_config'} else 0)
	assert push.call_count == int(not failure_only or result != 'success')
	if push.called:
		assert ('[FAIL' in push.call_args.args[1]) if result != 'success' else ('[CHECK-IN]' in push.call_args.args[1])


def test_unhandled_program_error_sends_notification(monkeypatch):
	monkeypatch.setattr(checkin, 'main', AsyncMock(side_effect=RuntimeError('Startup failed')))
	push = MagicMock()
	monkeypatch.setattr(checkin.notify, 'push_message', push)
	with pytest.raises(SystemExit) as exit_info:
		checkin.run_main()
	assert exit_info.value.code == 1
	push.assert_called_once_with(
		'AnyRouter Check-in Alert', '[FAILED] Error occurred during program execution: Startup failed', msg_type='text'
	)
