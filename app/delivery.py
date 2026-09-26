from __future__ import annotations
import os, smtplib, ssl
from email.message import EmailMessage
from .credentials import CredentialProvider

class FileMailer:
    @staticmethod
    def message(sender, recipient, subject, html_body, text_body):
        m=EmailMessage(); m["From"]=sender; m["To"]=recipient; m["Subject"]=subject; m.set_content(text_body); m.add_alternative(html_body,subtype="html"); return m

class SMTPMailer:
    def __init__(self, credentials=None):
        self.credentials=credentials or CredentialProvider()

    def resolve_auth_code(self):
        return self.credentials.get("SMTP_AUTH_CODE")

    def send(self, sender, recipient, subject, html_body, text_body, *, auth_code=None):
        required=["SMTP_HOST","SMTP_PORT"]
        missing=[x for x in required if not os.getenv(x)]
        if missing: raise RuntimeError("缺少 SMTP 配置: "+", ".join(missing))
        auth_code=self.resolve_auth_code() if auth_code is None else auth_code
        username=self.credentials.get("SMTP_USERNAME")
        if username!=sender: raise RuntimeError("发信地址与 SMTP 用户名不一致")
        msg=FileMailer.message(sender,recipient,subject,html_body,text_body); host=os.environ["SMTP_HOST"]; port=int(os.environ["SMTP_PORT"])
        try:
            if os.getenv("MAIL_USE_SSL","true").lower()=="true":
                with smtplib.SMTP_SSL(host,port,context=ssl.create_default_context(),timeout=30) as s:
                    s.login(username,auth_code)
                    refused=s.send_message(msg)
            else:
                with smtplib.SMTP(host,port,timeout=30) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(username,auth_code)
                    refused=s.send_message(msg)
        except (smtplib.SMTPException, OSError, ValueError):
            raise RuntimeError("SMTP 连接、认证或发送失败；请人工核对服务器状态，勿盲目重试") from None
        if refused: raise RuntimeError("SMTP 服务器拒收部分或全部收件人")
