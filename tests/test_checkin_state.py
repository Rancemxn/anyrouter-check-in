import sys
from pathlib import Path

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import execute_check_in, generate_balance_hash
from utils.config import ProviderConfig


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
