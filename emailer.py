"""
emailer.py
----------
Builds and sends the HTML email digest via Resend (https://resend.com).
Resend has a permanent free tier: 3,000 emails/month, 100/day. Plenty for a
digest sent to a few people once a day.
Only NEW opportunities at or above the configured threshold are included.
If there are no qualifying new opportunities, no email is sent.
"""
import os
import resend
SCORE_RANK = {"High": 3, "Medium": 2, "Low": 1}
def filter_for_email(opportunities, threshold):
    cutoff = SCORE_RANK.get(threshold, 2)
    return [o for o in opportunities if SCORE_RANK.get(o.get("score"), 0) >= cutoff]
def build_html(opportunities):
    highs = [o for o in opportunities if o.get("score") == "High"]
    meds = [o for o in opportunities if o.get("score") == "Medium"]
    def block(o):
        val = o.get("value", "Not specified")
        val_line = f" &middot; {val}" if val and val != "Not specified" else ""
        return f"""
        <tr><td style="padding:12px 0;border-bottom:1px solid #e0e0e0;">
          <div style="font-size:15px;font-weight:600;color:#1a1a1a;">{_esc(o.get('title',''))}</div>
          <div style="font-size:13px;color:#555;margin-top:3px;">
            {_esc(o.get('state',''))} &middot; {_esc(o.get('portal',''))} &middot; Due: {_esc(o.get('deadline','Not specified'))}{val_line}
          </div>
          <div style="font-size:13px;color:#1d5c3e;font-style:italic;margin-top:5px;">{_esc(o.get('why',''))}</div>
          <div style="font-size:12px;margin-top:5px;"><a href="{_esc(o.get('url','#'))}" style="color:#185fa5;">View listing</a></div>
        </td></tr>"""
    def section(title, items):
        if not items:
            return ""
        rows = "".join(block(o) for o in items)
        return f"""
        <tr><td style="padding-top:18px;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#888;">{title}</td></tr>
        {rows}"""
    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;">
      <h2 style="font-size:18px;color:#1a1a1a;">Procurement Radar</h2>
      <p style="font-size:14px;color:#333;">New procurement opportunities added since the last scan.</p>
      <table style="width:100%;border-collapse:collapse;">
        {section("High relevance", highs)}
        {section("Medium relevance", meds)}
      </table>
      <p style="font-size:14px;margin-top:24px;">
        <a href="https://sokanhaly.github.io/procurement-radar/dashboard/index.html" style="color:#185fa5;font-weight:600;">View all active opportunities on the dashboard &rarr;</a>
      </p>
      <p style="font-size:12px;color:#999;margin-top:16px;">
        Covers ME, NH, VT, MA, RI, CT, NY, NJ, PA and MD.
        Automated daily scan. Reply to flag any miscategorized listing.
      </p>
    </div>"""
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
def send_digest(opportunities, config):
    threshold = config["scan"]["email_threshold"]
    to_send = filter_for_email(opportunities, threshold)
    if not to_send:
        print("  No opportunities at or above threshold; no email sent.")
        return False
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY environment variable not set")
    resend.api_key = api_key
    from_name = config["email"]["from_name"]
    from_email = config["email"]["from_email"]
    subject = f"{config['email']['subject_prefix']} - {len(to_send)} new opportunities"
    html = build_html(to_send)
    params = {
        "from": f"{from_name} <{from_email}>",
        "to": config["email"]["recipients"],
        "subject": subject,
        "html": html,
    }
    try:
        resend.Emails.send(params)
        print(f"  Sent digest to {len(config['email']['recipients'])} recipient(s) via Resend.")
        return True
    except Exception as e:
        print(f"  Resend send failed: {e}")
        return False
