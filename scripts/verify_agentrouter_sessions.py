"""Verify the two restored AgentRouter sessions without sending notifications."""

import asyncio
import json

from checkin import check_in_account
from utils.config import AppConfig, load_accounts_config


async def main():
	config = AppConfig.load_from_env()
	accounts = [account for account in (load_accounts_config() or []) if account.provider == 'agentrouter']
	assert len(accounts) == 2
	succeeded = 0
	for i, account in enumerate(accounts):
		success, _, info = await check_in_account(account, i, config)
		info = info or {}
		print(
			'RECOVERY_RESULT '
			+ json.dumps(
				{
					'name': account.get_display_name(i),
					'login_verified': success,
					'reward_status': info.get('check_in_status'),
					'balance': info.get('quota') if info.get('success') else None,
				},
				ensure_ascii=False,
			),
			flush=True,
		)
		succeeded += bool(success)
	print(f'RECOVERY_SUCCESS={succeeded}/{len(accounts)}')
	if succeeded != len(accounts):
		raise SystemExit(1)


if __name__ == '__main__':
	asyncio.run(main())
