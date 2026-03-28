"""
core/email_outlook.py — Integration Outlook via Windows COM
==========================================================

Permet de lire, envoyer, répondre aux emails via Outlook installe.
Pas de credentials necessaires — utilise l'Outlook local de l'utilisateur.

Dependencies : pywin32 (pip install pywin32)
"""

import time
import re
from datetime import datetime, timedelta
from typing import Optional
from config.logger import get_logger

logger = get_logger(__name__)


class OutlookEmail:
    """Client email via Outlook COM."""

    def __init__(self):
        self._outlook = None
        self._connected = False
        self._connect()

    def _connect(self):
        try:
            import win32com.client
            self._outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            self._connected = True
            logger.info("OutlookEmail: connexion etablie")
        except ImportError:
            logger.error("pywin32 non installe : pip install pywin32")
        except Exception as e:
            logger.error(f"OutlookEmail: connexion echouee — {e}")
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _get_folder(self, folder_name: str):
        """Accede a un dossier Outlook (Inbox, Sent Items, etc.)."""
        if not self._connected:
            return None
        try:
            folders = {
                "inbox": self._outlook.GetDefaultFolder(6),
                "sent": self._outlook.GetDefaultFolder(5),
                "drafts": self._outlook.GetDefaultFolder(16),
                "outbox": self._outlook.GetDefaultFolder(4),
            }
            return folders.get(folder_name.lower())
        except Exception as e:
            logger.error(f"Erreur acces dossier {folder_name}: {e}")
            return None

    def _parse_email_address(self, entry) -> str:
        """Extrait l'adresse email d'un objet Entry."""
        try:
            if hasattr(entry, "Address"):
                return entry.Address
            return str(entry)
        except:
            return str(entry)

    def _item_to_dict(self, item, max_body_chars: int = 500) -> dict:
        """Convertit un MailItem en dict."""
        try:
            body = ""
            if hasattr(item, "Body") and item.Body:
                body = item.Body[:max_body_chars]
            elif hasattr(item, "HTMLBody") and item.HTMLBody:
                body = re.sub(r'<[^>]+>', '', item.HTMLBody)[:max_body_chars]

            sender_email = ""
            if hasattr(item, "SenderEmailAddress"):
                sender_email = item.SenderEmailAddress
            elif hasattr(item, "SendUsingAccount"):
                sender_email = str(item.SendUsingAccount)

            return {
                "id": getattr(item, "EntryID", str(time.time())),
                "subject": getattr(item, "Subject", "(sans objet)"),
                "sender": sender_email,
                "sender_name": getattr(item, "SenderName", sender_email),
                "to": getattr(item, "To", ""),
                "body": body,
                "date": getattr(item, "SentOn", datetime.now()).strftime("%Y-%m-%d %H:%M") if hasattr(item, "SentOn") else "",
                "unread": getattr(item, "UnRead", False),
                "importance": getattr(item, "Importance", 1),
                "has_attachments": getattr(item, "Attachments", []).Count > 0 if hasattr(item, "Attachments") else False,
            }
        except Exception as e:
            logger.error(f"Erreur conversion item: {e}")
            return {}

    def get_inbox(self, limit: int = 10, unread_only: bool = False) -> list:
        """Retourne les emails de la boite de reception."""
        folder = self._get_folder("inbox")
        if not folder:
            return []

        try:
            items = folder.Items
            items.Sort("[ReceivedTime]", True)

            emails = []
            count = 0
            for item in items:
                if count >= limit:
                    break
                try:
                    if unread_only and not item.UnRead:
                        continue
                    emails.append(self._item_to_dict(item))
                    count += 1
                except:
                    continue
            return emails
        except Exception as e:
            logger.error(f"Erreur lecture inbox: {e}")
            return []

    def get_unread_count(self) -> int:
        """Retourne le nombre d'emails non lus."""
        folder = self._get_folder("inbox")
        if not folder:
            return 0
        try:
            return folder.UnItemCount
        except:
            return 0

    def search_emails(self, query: str, folder: str = "inbox", limit: int = 10) -> list:
        """Recherche des emails par sujet, expediteur ou contenu."""
        folder_obj = self._get_folder(folder)
        if not folder_obj:
            return []

        try:
            items = folder_obj.Items
            items.Sort("[ReceivedTime]", True)

            query_lower = query.lower()
            results = []
            count = 0

            for item in items:
                if count >= limit:
                    break
                try:
                    subject = getattr(item, "Subject", "").lower()
                    sender = getattr(item, "SenderEmailAddress", "").lower()
                    body = (getattr(item, "Body", "") or "").lower()

                    if query_lower in subject or query_lower in sender or query_lower in body:
                        results.append(self._item_to_dict(item))
                        count += 1
                except:
                    continue
            return results
        except Exception as e:
            logger.error(f"Erreur recherche: {e}")
            return []

    def get_recent_from_sender(self, sender: str, limit: int = 5) -> list:
        """Retourne les emails recents d'un expediteur."""
        folder = self._get_folder("inbox")
        if not folder:
            return []

        try:
            items = folder.Items
            items.Sort("[ReceivedTime]", True)

            sender_lower = sender.lower()
            results = []
            count = 0

            for item in items:
                if count >= limit:
                    break
                try:
                    sender_email = getattr(item, "SenderEmailAddress", "").lower()
                    if sender_lower in sender_email:
                        results.append(self._item_to_dict(item))
                        count += 1
                except:
                    continue
            return results
        except Exception as e:
            logger.error(f"Erreur recherche expediteur: {e}")
            return []

    def send_email(self, to: str, subject: str = "", body: str = "", cc: str = "", bcc: str = "", attachments: list = None) -> dict:
        """Envoie un email."""
        if not self._connected:
            return {"success": False, "message": "Outlook non connecte"}

        try:
            import win32com.client
            mail = self._outlook.Parent.CreateItem(0)

            mail.To = to
            if cc:
                mail.CC = cc
            if bcc:
                mail.BCC = bcc
            mail.Subject = subject
            mail.Body = body

            if attachments:
                for path in attachments:
                    try:
                        mail.Attachments.Add(path)
                    except Exception as e:
                        logger.warning(f"Piece jointe echouee: {path} — {e}")

            mail.Send()
            logger.info(f"Email envoye a {to}: {subject}")
            return {"success": True, "message": f"Email envoye a {to}", "to": to, "subject": subject}

        except Exception as e:
            logger.error(f"Erreur envoi email: {e}")
            return {"success": False, "message": f"Erreur envoi: {e}"}

    def reply_email(self, item_id: str, body: str = "", to_all: bool = False) -> dict:
        """Repond a un email (necessite l'EntryID)."""
        if not self._connected:
            return {"success": False, "message": "Outlook non connecte"}

        try:
            folder = self._get_folder("inbox")
            if not folder:
                return {"success": False, "message": "Boite de reception introuvable"}

            items = folder.Items
            for item in items:
                if hasattr(item, "EntryID") and item.EntryID == item_id:
                    reply = item.Reply()
                    if not to_all:
                        reply.Recipients.Remove(1)
                    if body:
                        reply.Body = body + "\n\n" + reply.Body
                    reply.Send()
                    return {"success": True, "message": "Reponse envoyee"}

            return {"success": False, "message": "Email source introuvable"}
        except Exception as e:
            logger.error(f"Erreur reply: {e}")
            return {"success": False, "message": f"Erreur: {e}"}

    def forward_email(self, item_id: str, to: str, body: str = "") -> dict:
        """Transmet un email a quelqu'un."""
        if not self._connected:
            return {"success": False, "message": "Outlook non connecte"}

        try:
            folder = self._get_folder("inbox")
            if not folder:
                return {"success": False, "message": "Boite de reception introuvable"}

            items = folder.Items
            for item in items:
                if hasattr(item, "EntryID") and item.EntryID == item_id:
                    fwd = item.Forward()
                    fwd.To = to
                    if body:
                        fwd.Body = body + "\n\n" + fwd.Body
                    fwd.Send()
                    return {"success": True, "message": f"Email transmis a {to}"}

            return {"success": False, "message": "Email source introuvable"}
        except Exception as e:
            logger.error(f"Erreur forward: {e}")
            return {"success": False, "message": f"Erreur: {e}"}

    def mark_as_read(self, item_id: str) -> dict:
        """Marque un email comme lu."""
        try:
            folder = self._get_folder("inbox")
            if not folder:
                return {"success": False}

            for item in folder.Items:
                if hasattr(item, "EntryID") and item.EntryID == item_id:
                    item.UnRead = False
                    item.Save()
                    return {"success": True}

            return {"success": False, "message": "Email introuvable"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def mark_as_unread(self, item_id: str) -> dict:
        """Marque un email comme non lu."""
        try:
            folder = self._get_folder("inbox")
            if not folder:
                return {"success": False}

            for item in folder.Items:
                if hasattr(item, "EntryID") and item.EntryID == item_id:
                    item.UnRead = True
                    item.Save()
                    return {"success": True}

            return {"success": False, "message": "Email introuvable"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_important_emails(self, hours: int = 24) -> list:
        """Retourne les emails importants recents (non lus ou haute priorite)."""
        folder = self._get_folder("inbox")
        if not folder:
            return []

        try:
            items = folder.Items
            items.Sort("[ReceivedTime]", True)

            cutoff = datetime.now() - timedelta(hours=hours)
            emails = []

            for item in items:
                try:
                    received = getattr(item, "ReceivedTime", None)
                    if received and received < cutoff:
                        break

                    is_important = (
                        not getattr(item, "UnRead", False) == False or
                        getattr(item, "Importance", 1) == 2
                    )
                    if is_important:
                        emails.append(self._item_to_dict(item))
                except:
                    continue
            return emails[:15]
        except Exception as e:
            logger.error(f"Erreur emails importants: {e}")
            return []

    def get_summary(self) -> dict:
        """Resume rapide de la boite de reception."""
        inbox = self._get_folder("inbox")
        if not inbox:
            return {"connected": False}

        try:
            unread = 0
            for item in inbox.Items:
                if getattr(item, "UnRead", False):
                    unread += 1

            return {
                "connected": True,
                "total": inbox.Items.Count,
                "unread": unread,
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}


_outlook_client = None

def get_outlook_client() -> OutlookEmail:
    """Singleton du client Outlook."""
    global _outlook_client
    if _outlook_client is None:
        _outlook_client = OutlookEmail()
    return _outlook_client
