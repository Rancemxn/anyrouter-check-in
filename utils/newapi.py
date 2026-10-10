"""新版 NewAPI 的登录限制与本次任务创建的会话清理。"""

from urllib.parse import quote

import httpx

LOGIN_LIMIT_MESSAGES = {
	'AUTH_SESSION_LIMIT': '登录会话数量已达上限，请在已登录设备的“登录会话”中退出其他会话后重试',
	'AUTH_SESSION_ISSUANCE_LIMIT': '近期创建登录会话过于频繁，请等待服务端计数窗口结束后重试',
}


class NewAPILoginLimitError(Exception):
	"""账号级登录限制，切换代理不能解决。错误消息只使用本地固定文案。"""


def login_limit_error(payload: object) -> NewAPILoginLimitError | None:
	if not isinstance(payload, dict) or payload.get('success') is True:
		return None
	code = payload.get('code')
	if isinstance(code, str) and code in LOGIN_LIMIT_MESSAGES:
		return NewAPILoginLimitError(f'{code}: {LOGIN_LIMIT_MESSAGES[code]}')
	return None


def revoke_newapi_session(domain: str, access_token: str, session_id: str, *, proxy_url: str | None = None) -> bool:
	"""只撤销调用方本次登录签发的 SID；不调用退出其他会话或密码重置接口。"""
	try:
		with httpx.Client(proxy=proxy_url, timeout=15, follow_redirects=False) as client:
			response = client.delete(
				f'{domain.rstrip("/")}/api/user/sessions/{quote(session_id, safe="")}',
				headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
			)
			return response.status_code == 200 and response.json().get('success') is True
	except Exception:
		# 异常可能含有 SID、令牌或完整响应，不将其写入日志。
		return False
