from datetime import datetime, timedelta

from utils.email_report import BEIJING, format_credential_expiry, render_checkin_email


def test_report_has_account_status_expiry_and_escaped_untrusted_content():
	now = datetime(2026, 11, 15, 16, tzinfo=BEIJING)
	accounts = [
		{
			'name': 'Agent <one>',
			'success': True,
			'before': None,
			'after': {'success': True, 'quota': 759.82, 'used_quota': 1750.73, 'check_in_status': 'rewarded'},
			'expiry': ((now + timedelta(days=6)).timestamp(), False),
			'github': True,
			'uses_cookies': True,
		},
		{
			'name': 'Failed account',
			'success': False,
			'before': None,
			'after': {'success': False, 'error': '<img src=x onerror=alert(1)>'},
			'expiry': None,
			'github': False,
			'uses_cookies': True,
		},
	]
	title, html = render_checkin_email(accounts, now, 'https://github.com/example/repo/actions/runs/123')
	assert '1 个失败' in title and '1 个凭证' in title
	assert html.index('Failed account') < html.index('Agent &lt;one&gt;')
	assert '奖励已确认' in html and '$759.82' in html
	assert '<img' not in html and '&lt;img' in html
	assert '2026-11-21 16:00' in html and '剩余 6 天' in html
	assert '重新导出 GitHub 会话' in html and '到期时间未知' in html
	assert 'href="https://github.com/example/repo/actions/runs/123"' in html


def test_estimated_expiry_and_expired_credentials_are_explicit():
	now = datetime(2026, 11, 15, 16, tzinfo=BEIJING)
	assert format_credential_expiry((now.timestamp(), True), now) == '预计到期：2026-11-15 16:00（北京时间） · 已到期'
	assert '剩余 1 天' in format_credential_expiry((now.timestamp() + 1, False), now)
