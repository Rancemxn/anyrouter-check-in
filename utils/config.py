#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Literal


def validate_github_cookies(cookies: object, *, check_expiry: bool = True) -> list[dict]:
	"""只接受 GitHub 域的浏览器 Cookie；错误信息不包含凭证。"""
	if not isinstance(cookies, list) or not cookies:
		raise ValueError('github_cookies must be a non-empty browser cookie array')
	allowed = {'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'}
	result = []
	for cookie in cookies:
		if not isinstance(cookie, dict) or cookie.get('domain') not in {'github.com', '.github.com'}:
			raise ValueError('github_cookies must contain only github.com cookies')
		if any(not isinstance(cookie.get(key), str) or not cookie[key] for key in ('name', 'value', 'path')):
			raise ValueError('github_cookies contains an invalid cookie')
		if not cookie['path'].startswith('/') or 'url' in cookie:
			raise ValueError('github_cookies contains an invalid cookie scope')
		expires = cookie.get('expires', -1)
		if not isinstance(expires, (int, float)) or not math.isfinite(expires):
			raise ValueError('github_cookies contains an invalid expiry')
		if check_expiry and expires != -1 and expires <= time.time():
			continue
		result.append({key: value for key, value in cookie.items() if key in allowed})
	if not any(cookie['name'] == 'user_session' for cookie in result):
		raise ValueError('GitHub session missing or expired; export the session again')
	return result


@dataclass
class ProviderConfig:
	"""Provider 配置"""

	name: str
	domain: str
	login_path: str = '/login'
	sign_in_path: str | None = '/api/user/sign_in'
	user_info_path: str = '/api/user/self'
	api_user_key: str = 'new-api-user'
	bypass_method: Literal['waf_cookies'] | None = None
	waf_cookie_names: List[str] | None = None
	use_proxy: bool = False
	persist_profile: bool = False

	def __post_init__(self):
		required_waf_cookies = set()
		if self.waf_cookie_names and isinstance(self.waf_cookie_names, List):
			for item in self.waf_cookie_names:
				name = '' if not item or not isinstance(item, str) else item.strip()
				if not name:
					print(f'[WARNING] Found invalid WAF cookie name: {item}')
					continue

				required_waf_cookies.add(name)

		if not required_waf_cookies:
			self.bypass_method = None

		self.waf_cookie_names = list(required_waf_cookies)

	@classmethod
	def from_dict(cls, name: str, data: dict, *, defaults: 'ProviderConfig | None' = None) -> 'ProviderConfig':
		"""从字典创建 ProviderConfig

		配置格式:
		- 基础: {"domain": "https://example.com"}
		- 完整: {"domain": "https://example.com", "login_path": "/login", "use_proxy": true, ...}
		"""
		default_use_proxy = defaults.use_proxy if defaults else False
		default_persist_profile = defaults.persist_profile if defaults else False
		return cls(
			name=name,
			domain=data['domain'],
			login_path=data.get('login_path', defaults.login_path if defaults else '/login'),
			sign_in_path=data.get('sign_in_path', defaults.sign_in_path if defaults else '/api/user/sign_in'),
			user_info_path=data.get('user_info_path', defaults.user_info_path if defaults else '/api/user/self'),
			api_user_key=data.get('api_user_key', defaults.api_user_key if defaults else 'new-api-user'),
			bypass_method=data.get('bypass_method', defaults.bypass_method if defaults else None),
			waf_cookie_names=data.get('waf_cookie_names', defaults.waf_cookie_names if defaults else None),
			use_proxy=data.get('use_proxy', default_use_proxy),
			persist_profile=data.get('persist_profile', default_persist_profile),
		)

	def needs_waf_cookies(self) -> bool:
		"""判断是否需要获取 WAF cookies"""
		return self.bypass_method == 'waf_cookies'

	def needs_manual_check_in(self) -> bool:
		"""判断是否需要手动调用签到接口"""
		return self.sign_in_path is not None


@dataclass
class AppConfig:
	"""应用配置"""

	providers: Dict[str, ProviderConfig]

	@classmethod
	def load_from_env(cls) -> 'AppConfig':
		"""从环境变量加载配置"""
		providers = {
			'anyrouter': ProviderConfig(
				name='anyrouter',
				domain='https://anyrouter.top',
				login_path='/login',
				sign_in_path='/api/user/sign_in',
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'],
				use_proxy=False,
				persist_profile=True,
			),
			'agentrouter': ProviderConfig(
				name='agentrouter',
				domain='https://agentrouter.org',
				login_path='/login',
				sign_in_path=None,  # 通过登录回调的 checked_in 字段确认签到
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc'],
				use_proxy=True,
				persist_profile=False,
			),
		}

		# 尝试从环境变量加载自定义 providers
		providers_str = os.getenv('PROVIDERS')
		if providers_str:
			try:
				providers_data = json.loads(providers_str)

				if not isinstance(providers_data, dict):
					print('[WARNING] PROVIDERS must be a JSON object, ignoring custom providers')
					return cls(providers=providers)

				# 解析自定义 providers,会覆盖默认配置
				for name, provider_data in providers_data.items():
					try:
						providers[name] = ProviderConfig.from_dict(
							name,
							provider_data,
							defaults=providers.get(name),
						)
					except Exception as e:
						print(f'[WARNING] Failed to parse provider "{name}": {e}, skipping')
						continue

				print(f'[INFO] Loaded {len(providers_data)} custom provider(s) from PROVIDERS environment variable')
			except json.JSONDecodeError as e:
				print(
					f'[WARNING] Failed to parse PROVIDERS environment variable: {e}, using default configuration only'
				)
			except Exception as e:
				print(f'[WARNING] Error loading PROVIDERS: {e}, using default configuration only')

		return cls(providers=providers)

	def get_provider(self, name: str) -> ProviderConfig | None:
		"""获取指定 provider 配置"""
		return self.providers.get(name)


@dataclass
class AccountConfig:
	"""账号配置"""

	cookies: dict | str | None
	api_user: str | None = None
	provider: str = 'anyrouter'
	name: str | None = None
	email: str | None = None
	password: str | None = None
	github_cookies: list[dict] | None = None

	@classmethod
	def from_dict(cls, data: dict, index: int) -> 'AccountConfig':
		"""从字典创建 AccountConfig"""
		provider = data.get('provider', 'anyrouter')
		name = data.get('name', f'Account {index + 1}')

		return cls(
			cookies=data.get('cookies'),
			api_user=data.get('api_user'),
			provider=provider,
			name=name if name else None,
			email=data.get('email'),
			password=data.get('password'),
			github_cookies=data.get('github_cookies'),
		)

	def has_login_credentials(self) -> bool:
		"""是否配置了邮箱密码登录"""
		return bool(self.email and self.password)

	def get_display_name(self, index: int) -> str:
		"""获取显示名称"""
		return self.name if self.name else f'Account {index + 1}'


def load_accounts_config() -> list[AccountConfig] | None:
	"""从环境变量加载账号配置"""
	accounts_str = os.getenv('ANYROUTER_ACCOUNTS')
	if not accounts_str:
		print('ERROR: ANYROUTER_ACCOUNTS environment variable not found')
		return None

	try:
		accounts_data = json.loads(accounts_str)
	except json.JSONDecodeError as e:
		print(f'ERROR: ANYROUTER_ACCOUNTS JSON 解析失败: {e}')
		print('HINT: 常见原因 - 末尾多余逗号、使用了单引号、包含注释、或换行格式问题')
		return None

	try:
		if not isinstance(accounts_data, list):
			print('ERROR: Account configuration must use array format [{}]')
			return None

		# Windows PowerShell 合并 JSON 数组时可能生成 {"value": [...], "Count": N} 包装。
		normalized = []
		for item in accounts_data:
			if isinstance(item, dict) and set(item) == {'value', 'Count'}:
				if not isinstance(item['value'], list) or item['Count'] != len(item['value']):
					print('ERROR: Invalid PowerShell account array wrapper (value/Count)')
					return None
				normalized.extend(item['value'])
			else:
				normalized.append(item)

		accounts = []
		for i, account_dict in enumerate(normalized):
			if not isinstance(account_dict, dict):
				print(f'ERROR: Account {i + 1} configuration format is incorrect')
				return None

			has_github = 'github_cookies' in account_dict
			if has_github:
				if account_dict.get('provider') != 'agentrouter':
					print(f'ERROR: Account {i + 1}: github_cookies requires provider=agentrouter')
					return None
				try:
					account_dict['github_cookies'] = validate_github_cookies(
						account_dict['github_cookies'], check_expiry=False
					)
				except ValueError as exc:
					print(f'ERROR: Account {i + 1}: {exc}')
					return None
				if not str(account_dict.get('api_user', '')).isdigit():
					print(f'ERROR: Account {i + 1}: GitHub login requires api_user to verify account identity')
					return None

			if 'api_user' not in account_dict:
				has_login = account_dict.get('email') and account_dict.get('password')
				if not has_login:
					print(
						f'ERROR: Account {i + 1} missing required field (api_user) - only email+password login can omit it'
					)
					return None

			has_cookies = 'cookies' in account_dict and account_dict['cookies']
			has_login = account_dict.get('email') and account_dict.get('password')

			if not has_cookies and not has_login and not has_github:
				print(f'ERROR: Account {i + 1} must have cookies, email+password or github_cookies')
				return None

			if 'name' in account_dict and not account_dict['name']:
				print(f'ERROR: Account {i + 1} name field cannot be empty')
				return None

			accounts.append(AccountConfig.from_dict(account_dict, i))

		return accounts
	except Exception as e:
		print(f'ERROR: Account configuration format is incorrect: {e}')
		return None
