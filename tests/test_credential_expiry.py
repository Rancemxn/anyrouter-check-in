import base64

import pytest

from utils.config import AccountConfig


def test_github_expiry_uses_login_cookie_not_analytics_or_device_cookie():
	account = AccountConfig(
		None,
		provider='agentrouter',
		github_cookies=[
			{'name': '_dd_s_v2', 'expires': 1},
			{'name': '_device_id', 'expires': 2000000000},
			{'name': 'user_session', 'domain': 'github.com', 'path': '/', 'expires': 1800000000},
		],
	)
	assert account.get_credential_expiry() == (1800000000, False)
	assert account.github_cookies is not None
	assert account.github_cookies[0]['expires'] == 1


@pytest.mark.parametrize('expires', [-1, None, True, float('nan'), float('inf'), 253402300800])
def test_unknown_or_invalid_github_expiry_is_not_guessed(expires):
	account = AccountConfig(
		None,
		provider='agentrouter',
		github_cookies=[{'name': 'user_session', 'domain': 'github.com', 'path': '/', 'expires': expires}],
	)
	assert account.get_credential_expiry() is None


@pytest.mark.parametrize('as_header', [True, False])
def test_anyrouter_expiry_is_estimated_from_original_issuance(monkeypatch, as_header):
	monkeypatch.setattr('utils.config.time.time', lambda: 1800000000)
	issued = 1790000000
	session = base64.urlsafe_b64encode(f'{issued}|payload|'.encode() + b'|' * 32).decode().rstrip('=')
	account = AccountConfig(f'session={session}; waf=other' if as_header else {'session': session})
	assert account.get_credential_expiry() == (issued + 30 * 86400, True)
	account.email, account.password = 'user@example.test', 'password'
	assert account.get_credential_expiry() is None
	account.email = None
	account.provider = 'custom'
	assert account.get_credential_expiry() is None


@pytest.mark.parametrize('session', ['', 'invalid!', '\u4e0d\u662fCookie', 'MTIzfHx4', None, 123])
def test_unrecognized_session_has_unknown_expiry(session):
	assert AccountConfig({'session': session}).get_credential_expiry() is None


def test_future_issuance_is_not_treated_as_a_valid_expiry(monkeypatch):
	monkeypatch.setattr('utils.config.time.time', lambda: 1800000000)
	session = base64.urlsafe_b64encode(b'1800000001|payload|' + b'x' * 32).decode()
	assert AccountConfig({'session': session}).get_credential_expiry() is None
