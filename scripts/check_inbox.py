"""
查询当前 Outlook 邮箱是否有邮件（用 outlook_fetch_url 或 IMAP/Graph）。
用法：项目根目录  python -m protocol.scripts.check_inbox
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import cfg
from email_outlook import load_outlook_accounts, fetch_emails_outlook_api

def main():
    accounts = load_outlook_accounts()
    if not accounts:
        print("[x] No Outlook accounts in mail.txt")
        return
    fetch_url = (getattr(cfg.email, "outlook_fetch_url", None) or "").strip()
    if not fetch_url:
        print("[x] email.outlook_fetch_url not set in config.yaml")
        return
    print(f"[*] Fetch URL: {fetch_url}")
    print(f"[*] Accounts: {len(accounts)}")
    for i, acc in enumerate(accounts):
        email = (acc.get("email") or "").strip()
        print(f"\n--- {i+1}. {email} ---")
        emails = fetch_emails_outlook_api(acc, fetch_url, top=15)
        if emails is None:
            print("  (fetch failed)")
            continue
        if not emails:
            print("  (no mails)")
            continue
        print(f"  Total: {len(emails)}")
        for j, m in enumerate(emails[:10]):
            subj = (m.get("subject") or "")[:60]
            from_ = (m.get("from") or "")[:40]
            body = (m.get("body") or "")[:120].replace("\n", " ")
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            def _enc(s):
                if not s:
                    return ""
                return s.encode(enc, errors="replace").decode(enc)
            print(f"  [{j+1}] from={_enc(from_)} subject={_enc(subj)}")
            if body:
                print(f"      body: {_enc(body)}...")

if __name__ == "__main__":
    main()
