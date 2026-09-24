"""用手动导出的 GitHub 会话完成新的 AgentRouter OAuth 登录。"""

from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from utils.browser import (
	BrowserLoginResult,
	launch_login_context,
	load_browser_login_settings,
	navigate_login_page,
	prepare_browser_page,
)
from utils.config import ProviderConfig, validate_github_cookies


class GitHubLoginRequired(Exception):
	"""需手动更新凭证，继续换代理不会修复此错误。"""


async def read_github_login_response(response, domain: str) -> dict | None:
	"""从当前站点真实 OAuth 回调读取账号，适用于原页面和授权新窗口。"""
	target, source = urlsplit(domain), urlsplit(response.url)
	if (source.scheme, source.netloc, source.path) != (target.scheme, target.netloc, '/api/oauth/github'):
		return None
	if response.status != 200:
		return None
	try:
		body = await response.json()
	except Exception:
		return None
	if not isinstance(body, dict) or body.get('success') is not True:
		return None
	profile = body.get('data')
	return profile if isinstance(profile, dict) and profile.get('id') else None


async def login_with_github(
	account_name: str, provider: ProviderConfig, cookies: list[dict], expected_user: str
) -> BrowserLoginResult:
	settings = load_browser_login_settings(account_name, provider.name, persist_profile=False)
	try:
		cookies = validate_github_cookies(cookies)
	except ValueError as exc:
		raise GitHubLoginRequired(str(exc)) from None
	# 只恢复 GitHub Cookie；临时上下文没有 AgentRouter 会话，每次都重新 OAuth。
	context = await launch_login_context(settings, use_proxy=provider.use_proxy)
	try:
		await context.add_cookies(cast('Any', cookies))
		page = await context.new_page()
		await prepare_browser_page(page)
		await navigate_login_page(page, f'{provider.domain}{provider.login_path}', settings.wait_timeout_ms)
		# 与站点前端一致：同一浏览器会话取得新 state，随后由站点回调兑换 code。
		payload = await page.evaluate("""async () => {
			const status = await (await fetch('/api/status')).json();
			const state = await (await fetch('/api/oauth/state?mode=login')).json();
			localStorage.setItem('oauth_mode', 'login');
			return {status, state};
		}""")
		status, state = payload['status'], payload['state']
		if not status.get('success') or not state.get('success'):
			raise RuntimeError('Could not start AgentRouter OAuth')
		client_id = status.get('data', {}).get('github_client_id')
		if not client_id or not isinstance(state.get('data'), str) or not state['data']:
			raise RuntimeError('AgentRouter did not provide GitHub OAuth parameters')
		query = urlencode({'client_id': client_id, 'state': state['data'], 'scope': 'user:email'})
		target = urlsplit(provider.domain)
		try:
			async with page.expect_response(
				lambda response: (
					urlsplit(response.url).scheme == target.scheme
					and urlsplit(response.url).netloc == target.netloc
					and urlsplit(response.url).path == '/api/oauth/github'
				),
				timeout=settings.wait_timeout_ms,
			) as callback:
				await page.goto(
					f'https://github.com/login/oauth/authorize?{query}',
					wait_until='domcontentloaded',
					timeout=settings.wait_timeout_ms,
				)
			response = await callback.value
		except Exception:
			location = urlsplit(page.url)
			if location.hostname == 'github.com' and (
				location.path == '/login' or location.path.startswith(('/session', '/two-factor', '/sudo'))
			):
				raise GitHubLoginRequired('GitHub needs login or verification; export the session again') from None
			raise
		profile = await read_github_login_response(response, provider.domain)
		if profile is None:
			raise RuntimeError('AgentRouter GitHub OAuth callback did not confirm login')
		if str(profile.get('id')) != expected_user:
			raise GitHubLoginRequired('GitHub signed into a different AgentRouter account; check api_user and session')
		checked_in = profile.get('checked_in')
		return BrowserLoginResult(
			cookies={cookie['name']: cookie['value'] for cookie in await context.cookies(provider.domain)},
			api_user=expected_user,
			checked_in=checked_in if isinstance(checked_in, bool) else None,
		)
	finally:
		await context.close()
