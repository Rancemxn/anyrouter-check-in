"""本地手动授权，导出可合并进 ANYROUTER_ACCOUNTS Secret 的账号配置。"""

import argparse
import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from utils.browser import (
	launch_login_context,
	load_browser_login_settings,
	prepare_browser_page,
)
from utils.config import AppConfig, validate_github_cookies
from utils.github_oauth import read_github_login_response


async def export_session(name: str) -> dict:
	load_dotenv()
	provider = AppConfig.load_from_env().providers['agentrouter']
	settings = replace(load_browser_login_settings(name, 'agentrouter', persist_profile=False), headless=False)
	context = await launch_login_context(settings, use_proxy=provider.use_proxy)
	profile: dict | None = None

	async def on_response(response):
		nonlocal profile
		confirmed = await read_github_login_response(response, provider.domain)
		if confirmed is not None:
			profile = confirmed

	# GitHub 登录会打开新窗口，因此从整个 context 监听，而不是只监听登录页。
	context.on('response', on_response)
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		await page.goto(f'{provider.domain}{provider.login_path}', wait_until='domcontentloaded')
		while True:
			await asyncio.to_thread(
				input, '请在浏览器中手动通过 GitHub 登录 AgentRouter，看到控制台后在此按回车（Ctrl+C 退出）：'
			)
			if profile is not None:
				break
			print(
				'[WARN] 尚未捕获成功的 GitHub 登录回调。浏览器保持打开，请完成授权或退出 AgentRouter 后重新通过 GitHub 登录。'
			)
		print('[INFO] GitHub OAuth login verified by server callback')
		cookies = validate_github_cookies(
			[c for c in await context.cookies() if c['domain'] in {'github.com', '.github.com'}]
		)
		return {'name': name, 'provider': 'agentrouter', 'api_user': str(profile['id']), 'github_cookies': cookies}
	finally:
		context.remove_listener('response', on_response)
		await context.close()


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--name', default='AgentRouter')
	parser.add_argument('--output', type=Path, default=Path('.secrets/agentrouter.json'))
	args = parser.parse_args()
	try:
		output = args.output
		if not output.resolve().is_relative_to(Path('.secrets').resolve()):
			raise SystemExit('导出路径必须位于 .secrets 目录内')
		account = asyncio.run(export_session(args.name))
		output.parent.mkdir(parents=True, exist_ok=True)
		# 凭证只写本地忽略目录，不打印到终端，也不上传 artifact/cache。
		with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False) as stream:
			staging = Path(stream.name)
			try:
				json.dump([account], stream, ensure_ascii=False, separators=(',', ':'))
				stream.close()
				staging.replace(output)
			finally:
				staging.unlink(missing_ok=True)
		print(f'已保存到 {output}。将数组中的账号合并到 production 的 ANYROUTER_ACCOUNTS Secret。')
	except (Exception, KeyboardInterrupt) as exc:
		# Playwright 异常可能包含 OAuth code/state，因此不输出异常内容。
		raise SystemExit(f'导出未完成（{type(exc).__name__}），请确认浏览器登录成功后重试。') from None
