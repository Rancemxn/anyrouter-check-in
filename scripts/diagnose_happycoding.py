"""One-off HappyCoding login diagnosis; output metadata only."""

import asyncio
import json
import os
import re
from dataclasses import replace
from urllib.parse import urlsplit

import checkin
from utils.config import AppConfig, load_accounts_config
from utils.proxy import activate_proxy, proxy_candidates


def report(label, **data):
	print('[DIAG] ' + label + ' ' + json.dumps(data, ensure_ascii=False), flush=True)


def error_category(message):
	message = str(message).lower()
	for category, pattern in (
		('credentials', r'password|credential|密码|用户名'),
		('verification', r'captcha|turnstile|verif|验证'),
		('rate_limit', r'too many|rate limit|频繁'),
		('disabled', r'disabled|locked|禁用|封禁'),
	):
		if re.search(pattern, message):
			return category
	return 'other' if message else 'none'


async def main():
	os.environ['DEBUG_MODE'] = 'false'
	app = AppConfig.load_from_env()
	accounts = load_accounts_config() or []
	account = next(a for a in accounts if a.provider.lower() == 'happycoding')
	provider = app.get_provider(account.provider)
	assert provider and urlsplit(provider.domain).hostname == 'happycoding.xyz'
	provider = replace(provider, persist_profile=False)
	assert account.email and account.password

	if provider.use_proxy and os.getenv('CHECKIN_PROXY_CONTROLLER'):
		urls = [provider.domain + provider.login_path]
		for node in (await proxy_candidates(urls))[:3]:
			if await activate_proxy(node, urls):
				break
		else:
			raise RuntimeError('No usable proxy')

	original_launch = checkin.launch_login_context
	original_verify = checkin.verify_browser_login

	async def launch(*args, **kwargs):
		context = await original_launch(*args, **kwargs)

		async def response_received(response):
			url = urlsplit(response.url)
			if url.hostname != 'happycoding.xyz' or url.path not in {
				'/api/user/login',
				'/api/user/login/encryption-key',
				'/api/user/login/verify',
				'/api/user/auth/refresh',
				'/api/user/self',
			}:
				return
			try:
				payload = await response.json()
				payload = payload if isinstance(payload, dict) else {}
				data = payload.get('data')
				data = data if isinstance(data, dict) else {}
				code = payload.get('code')
				report(
					'response',
					path=url.path,
					status=response.status,
					success=payload.get('success') is True,
					code=code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}', code) else None,
					error_category=error_category(payload.get('message', '')),
					has_token=bool(data.get('access_token')),
					has_user=bool(data.get('user')),
					has_flow=bool(data.get('flow_token')),
					has_session=bool(data.get('session')),
				)
				if url.path == '/api/user/login':
					body = response.request.post_data_json or {}
					report(
						'submitted',
						username_matches=body.get('username') == account.email,
						password_matches=body.get('password') == account.password,
						encrypted_password=bool(body.get('password_encrypted')),
					)
			except Exception as exc:
				report('response_parse', path=url.path, status=response.status, error=type(exc).__name__)

		context.on('response', response_received)
		return context

	async def verify(page, *args, **kwargs):
		report('before_verify', path=urlsplit(page.url).path, captured_token=bool(kwargs.get('access_token')))
		result = await original_verify(page, *args, **kwargs)
		report('after_verify', path=urlsplit(page.url).path, verified=bool(result))
		return result

	checkin.launch_login_context = launch
	checkin.verify_browser_login = verify
	result = await checkin.login_with_credentials(
		'HappyCoding', provider, account.provider, account.email, account.password
	)
	report('result', verified=bool(result))


if __name__ == '__main__':
	asyncio.run(main())
