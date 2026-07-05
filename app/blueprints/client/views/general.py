"""
General client routes — help, marketing landing, view-switching, bug reports.
"""

from flask import current_app, request, redirect, render_template, url_for, jsonify, session
from flask_login import login_required, current_user
from datetime import datetime, timezone

from app.blueprints.client import client_bp
from app import limiter


@client_bp.route("/help")
def help_page():
    return render_template('help.html')


@client_bp.route("/get-started")
def get_started():
    return render_template('get_started.html')


@client_bp.route("/switch-view", methods=["POST"])
@login_required
def switch_view():
    """Toggle between walker and client view for dual-role users."""
    if current_user.role != 'walker' or current_user.client is None:
        return redirect(url_for('client.index'))
    view = request.form.get('view')
    if view in ('walker', 'client'):
        session['active_view'] = view
    if session.get('active_view') == 'client':
        return redirect(url_for('client.index'))
    return redirect(url_for('walker.pickups'))


@client_bp.route("/report-bug", methods=["POST"])
@login_required
@limiter.limit("3 per hour", key_func=lambda: f"report-bug:{current_user.id}")
def report_bug():
    from app.utils.email import send_email
    from app.utils.logging_config import recent_log_buffer
    from html import escape

    description = (request.form.get("description") or "").strip()
    if not description:
        return jsonify(success=False, message="Please describe the issue."), 400

    user = current_user
    user_agent = request.headers.get("User-Agent", "unknown")
    referrer = request.form.get("page_url") or request.referrer or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logs = list(recent_log_buffer)
    log_section = "\n".join(logs) if logs else "(no recent warnings or errors)"

    html = f"""
    <h2 style="color:#1B1B1B;font-family:sans-serif;">Bug Report</h2>
    <table style="font-family:sans-serif;font-size:14px;border-collapse:collapse;width:100%;">
      <tr><td style="padding:6px 12px 6px 0;color:#555;white-space:nowrap;"><strong>User</strong></td>
          <td style="padding:6px 0;">{escape(user.firstname)} {escape(user.lastname or '')} &lt;{escape(user.email)}&gt; — {escape(user.role)}</td></tr>
      <tr><td style="padding:6px 12px 6px 0;color:#555;"><strong>URL</strong></td>
          <td style="padding:6px 0;">{escape(referrer)}</td></tr>
      <tr><td style="padding:6px 12px 6px 0;color:#555;"><strong>Browser</strong></td>
          <td style="padding:6px 0;">{escape(user_agent)}</td></tr>
      <tr><td style="padding:6px 12px 6px 0;color:#555;"><strong>Time</strong></td>
          <td style="padding:6px 0;">{timestamp}</td></tr>
    </table>

    <h3 style="font-family:sans-serif;margin-top:24px;">Description</h3>
    <p style="font-family:sans-serif;font-size:14px;white-space:pre-wrap;">{escape(description)}</p>

    <h3 style="font-family:sans-serif;margin-top:24px;">Recent server logs (WARNING / ERROR)</h3>
    <pre style="background:#f4f4f4;padding:12px;font-size:12px;overflow-x:auto;border-radius:4px;">{escape(log_section)}</pre>
    """

    ok = send_email(
        to=current_app.config['BUG_REPORTS_EMAIL'],
        subject=f"Bug report from {user.firstname} {user.lastname or ''}".strip(),
        html=html,
    )

    if ok:
        return jsonify(success=True)
    return jsonify(success=False, message="Failed to send — please try again."), 500
