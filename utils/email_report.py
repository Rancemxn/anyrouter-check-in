"""签到邮件：内联样式、表格布局，不加载外部图片或字体。"""

import math
from datetime import datetime, timedelta, timezone
from html import escape

BEIJING = timezone(timedelta(hours=8))
EXPIRY_WARNING_DAYS = 7


def format_credential_expiry(expiry: tuple[float, bool], now: datetime) -> str:
	expires_at, estimated = expiry
	date = datetime.fromtimestamp(expires_at, BEIJING).strftime('%Y-%m-%d %H:%M')
	remaining = expires_at - now.timestamp()
	status = f'剩余 {math.ceil(remaining / 86400)} 天' if remaining > 0 else '已到期'
	return f'{"预计到期" if estimated else "到期时间"}：{date}（北京时间） · {status}'


def _email_page(title: str, body: str) -> str:
	return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;color:#172033;font-family:Arial,'Microsoft YaHei',sans-serif;line-height:1.6">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px">
<tr><td style="background:#14283f;padding:26px 28px;border-radius:16px 16px 0 0">
<p style="margin:0 0 10px;color:#8fd8d0;font-size:12px;letter-spacing:2px">ANYROUTER · 签到助手</p>
<h1 style="margin:0;color:#ffffff;font-size:24px;line-height:1.4">{escape(title)}</h1></td></tr>
<tr><td style="background:#ffffff;padding:24px;border-radius:0 0 16px 16px">{body}</td></tr>
<tr><td style="padding:18px 12px;text-align:center;color:#64748b;font-size:12px">
自动签到通知 · 时间均为北京时间<br>登录凭证可能被提前撤销；到期时间不保证会话持续有效。</td></tr>
</table></td></tr></table></body></html>"""


def render_notice_email(title: str, content: str) -> str:
	return _email_page(
		title, f'<div style="font-size:14px;overflow-wrap:anywhere">{escape(content).replace(chr(10), "<br>")}</div>'
	)


def render_checkin_email(accounts: list[dict], now: datetime, run_url: str = '') -> tuple[str, str]:
	failed = sum(not account['success'] for account in accounts)
	deadline = now.timestamp() + EXPIRY_WARNING_DAYS * 86400
	expiring = sum(bool(account['expiry'] and account['expiry'][0] <= deadline) for account in accounts)
	title = '签到报告'
	if failed or expiring:
		title = '签到提醒 · ' + ' · '.join(
			part
			for part in (f'{failed} 个失败' if failed else '', f'{expiring} 个凭证需更新' if expiring else '')
			if part
		)
	body = f'<p style="margin:0 0 18px;color:#64748b;font-size:13px">{now.astimezone(BEIJING):%Y 年 %m 月 %d 日 %H:%M} · 本次处理 {len(accounts)} 个账号</p>'
	body += '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:24px"><tr>'
	for label, value, color in [
		('成功', len(accounts) - failed, '#0f766e'),
		('失败', failed, '#be123c'),
		('凭证需更新', expiring, '#b45309'),
	]:
		body += f'<td width="33%" style="padding:12px 6px;text-align:center;background:#f8fafc"><strong style="display:block;font-size:28px;color:{color}">{value}</strong><span style="font-size:12px;color:#64748b">{label}</span></td>'
	body += '</tr></table>'
	if expiring:
		body += '<p style="margin:0 0 22px;padding:14px 16px;background:#fff7ed;border-left:3px solid #f59e0b;border-radius:6px;font-size:14px;color:#92400e">请更新下方标记的登录凭证。已到期或将在 7 天内到期的凭证会持续提醒，即使本次签到成功。</p>'
	for account in sorted(
		accounts, key=lambda account: (account['success'], not (account['expiry'] and account['expiry'][0] <= deadline))
	):
		before, after = account['before'] or {}, account['after'] or {}
		if not account['success']:
			status, color, background = '需要处理', '#be123c', '#fff1f2'
		elif after.get('check_in_status') == 'rewarded':
			status, color, background = '奖励已确认', '#0f766e', '#ecfdf5'
		elif after.get('check_in_status') == 'unconfirmed':
			status, color, background = '奖励待确认', '#b45309', '#fff7ed'
		else:
			status, color, background = '签到已处理', '#0f766e', '#ecfdf5'
		body += f"""<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:16px;border:1px solid #e2e8f0;border-radius:10px"><tr><td style="padding:18px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td style="font-size:16px;font-weight:bold;overflow-wrap:anywhere">{escape(account['name'])}</td>
<td align="right" style="white-space:nowrap"><span style="padding:4px 9px;border-radius:5px;background:{background};color:{color};font-size:12px">{status}</span></td></tr></table>"""
		if not account['success'] or not after.get('success'):
			error = (
				account.get('error')
				or after.get('error')
				or ('签到未完成，请查看运行日志。' if not account['success'] else '余额暂不可用。')
			)
			body += (
				f'<p style="margin:12px 0 0;color:{color};font-size:14px;overflow-wrap:anywhere">{escape(error)}</p>'
			)
		if after.get('success'):
			body += f'<p style="margin:12px 0 2px;color:#64748b;font-size:12px">当前余额</p><p style="margin:0;font-size:26px;font-weight:bold;color:#172033">${after["quota"]:,.2f}</p>'
			body += f'<p style="margin:4px 0 0;color:#64748b;font-size:13px">累计使用 ${after["used_quota"]:,.2f}'
			if before.get('success'):
				reward = after['quota'] + after['used_quota'] - before['quota'] - before['used_quota']
				body += f' · 本轮额度增加 ${reward:,.2f}' if reward > 0.005 else ' · 本轮未记录到额度增加'
			body += '</p>'
		if account['expiry']:
			near = account['expiry'][0] <= deadline
			expiry_color = '#b45309' if near else '#64748b'
			expiry_text = format_credential_expiry(account['expiry'], now).replace('（北京时间）', '')
			body += f'<p style="margin:14px 0 0;padding-top:12px;border-top:1px solid #e2e8f0;color:{expiry_color};font-size:12px">{escape(expiry_text)}</p>'
			if near:
				action = (
					'重新导出 GitHub 会话并更新账号配置。'
					if account['github']
					else '重新登录网站，更新账号配置中的 session。'
				)
				body += f'<p style="margin:5px 0 0;color:#92400e;font-size:13px">{action}</p>'
		elif account['uses_cookies']:
			body += '<p style="margin:12px 0 0;color:#64748b;font-size:12px">登录凭证到期时间未知</p>'
		body += '</td></tr></table>'
	if run_url:
		body += f'<p style="margin:24px 0 4px;text-align:center"><a href="{escape(run_url, quote=True)}" style="display:inline-block;padding:11px 24px;border-radius:7px;background:#0f766e;color:#ffffff;text-decoration:none;font-size:14px">查看本次运行日志</a></p>'
	return title, _email_page('签到与凭证提醒' if failed or expiring else '本次签到报告', body)
