import io
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from db import get_user_lang
from i18n import t, is_rtl

_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
_FONTS_DIR = _BASE_DIR / "fonts"
_VAZIRMATN = _FONTS_DIR / "Vazirmatn-Regular.ttf"
_NOTOSANS = _FONTS_DIR / "NotoSans-Regular.ttf"

# Check uharfbuzz availability for RTL text shaping
try:
    import uharfbuzz  # noqa: F401
    _HAS_SHAPING = True
except ImportError:
    _HAS_SHAPING = False


def _sanitize(text: str) -> str:
    """Replace non-latin1 characters so core PDF fonts can render them."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ── Color palette ────────────────────────────────────────────────────────────

_BG_HEADER = (30, 41, 59)       # dark slate
_BG_CARD = (248, 250, 252)      # light gray
_BORDER_CARD = (203, 213, 225)  # slate-300
_TEXT_PRIMARY = (15, 23, 42)     # slate-900
_TEXT_SECONDARY = (71, 85, 105)  # slate-500
_TEXT_MUTED = (100, 116, 139)    # slate-400
_ACCENT = (59, 130, 246)        # blue-500
_GREEN = (34, 197, 94)          # green-500
_ORANGE = (249, 115, 22)        # orange-500
_PURPLE = (139, 92, 246)        # violet-500
_TEAL = (20, 184, 166)          # teal-500


class _PDF(FPDF):
    """FPDF subclass with modern footer."""

    def __init__(self, uid: int = 0):
        super().__init__()
        self._uid = uid
        self._use_unicode = False

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(*_BORDER_CARD)
        self.line(15, self.get_y() - 2, 195, self.get_y() - 2)
        if self._use_unicode:
            self.set_font("UniFont", "I" if not is_rtl(self._uid) else "", 8)
        else:
            self.set_font("Helvetica", "I", 8)
        self.set_text_color(*_TEXT_MUTED)
        page_text = t("pdf_page", self._uid, page=self.page_no(), total="{nb}")
        if not self._use_unicode:
            page_text = _sanitize(page_text)
        self.cell(0, 8, page_text, align="C")


def _setup_font(pdf: _PDF, uid: int):
    """Register a Unicode TTF font if needed for the user's language."""
    lang = get_user_lang(uid) or "en"

    if lang == "fa" and _VAZIRMATN.exists():
        font_path = str(_VAZIRMATN)
    elif _NOTOSANS.exists():
        font_path = str(_NOTOSANS)
    else:
        return

    pdf.add_font("UniFont", "", font_path, uni=True)
    pdf.add_font("UniFont", "B", font_path, uni=True)
    pdf.add_font("UniFont", "I", font_path, uni=True)
    pdf._use_unicode = True

    if lang in ("fa",) and _HAS_SHAPING:
        pdf.set_text_shaping(
            use_shaping_engine=True,
            direction="rtl",
            script="arab",
            language="fa",
        )


def generate_account_pdf(accounts: list[dict], title: str, uid: int = 0) -> io.BytesIO:
    """Generate a modern PDF with account details and QR codes.

    Each account dict: {email, proxy_link, qr_image (BytesIO|None),
                        traffic, duration, sub_link, panel}
    Returns BytesIO with .name = "accounts.pdf".
    """
    pdf = _PDF(uid=uid)
    _setup_font(pdf, uid)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)

    use_uni = pdf._use_unicode
    tmp_files = []

    def _text(s: str) -> str:
        return s if use_uni else _sanitize(s)

    def _set_font(style: str = "", size: int = 10):
        if use_uni:
            pdf.set_font("UniFont", style if not is_rtl(uid) else "", size)
        else:
            pdf.set_font("Helvetica", style, size)

    def _dot(x: float, y: float, color: tuple):
        """Draw a small colored circle indicator."""
        pdf.set_fill_color(*color)
        pdf.ellipse(x, y + 2, 3, 3, "F")

    def _labeled_line(x: float, text_w: float, label: str, color: tuple, size: int = 10):
        """Draw a dot + label text line."""
        y = pdf.get_y()
        _dot(x, y, color)
        pdf.set_xy(x + 5, y)
        _set_font("", size)
        pdf.set_text_color(*_TEXT_SECONDARY)
        pdf.cell(text_w - 5, 7, _text(label), new_x="LMARGIN", new_y="NEXT")

    def _draw_header():
        """Draw the page header bar."""
        pdf.set_fill_color(*_BG_HEADER)
        pdf.rect(0, 0, 210, 24, "F")
        pdf.set_y(6)
        _set_font("B", 14)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 12, _text(title), align="C")
        pdf.set_text_color(*_TEXT_PRIMARY)
        pdf.set_y(28)

    def _draw_card(acc: dict, index: int):
        """Draw a single account card."""
        card_w = 180
        card_x = 15
        qr_img = acc.get("qr_image")
        proxy_link = acc.get("proxy_link", "")
        qr_size = 48

        # Estimate card height
        card_h = 64
        if acc.get("sub_link"):
            card_h += 7
        if qr_img:
            card_h = max(card_h, qr_size + 30)
        if proxy_link:
            link_chars = len(proxy_link)
            link_lines = max(1, link_chars // 60 + 1)
            card_h += link_lines * 4 + 6

        # Check if card fits on page
        if pdf.get_y() + card_h + 6 > 278:
            pdf.add_page()
            _draw_header()

        start_y = pdf.get_y()

        # Card background
        pdf.set_fill_color(*_BG_CARD)
        pdf.set_draw_color(*_BORDER_CARD)
        pdf.rect(card_x, start_y, card_w, card_h, "DF")

        # Accent stripe on the left
        pdf.set_fill_color(*_ACCENT)
        pdf.rect(card_x, start_y, 3, card_h, "F")

        # Account number
        num_x = card_x + 8
        num_y = start_y + 5
        _set_font("B", 12)
        pdf.set_text_color(*_ACCENT)
        pdf.set_xy(num_x, num_y)
        pdf.cell(12, 8, _text(f"#{index + 1}"))

        # Email title
        pdf.set_xy(num_x + 14, num_y)
        _set_font("B", 13)
        pdf.set_text_color(*_TEXT_PRIMARY)
        pdf.cell(0, 8, _text(acc["email"]))

        # Thin separator under email
        sep_y = num_y + 11
        pdf.set_draw_color(*_BORDER_CARD)
        pdf.line(card_x + 7, sep_y, card_x + card_w - 7, sep_y)

        # Info section
        info_y = sep_y + 3
        info_x = card_x + 8
        text_w = card_w - 16
        if qr_img:
            text_w = card_w - qr_size - 24

        pdf.set_y(info_y)

        # Traffic line with green dot
        _labeled_line(info_x, text_w,
                      t("pdf_traffic", uid, traffic=acc["traffic"]),
                      _GREEN)

        # Duration line with orange dot
        _labeled_line(info_x, text_w,
                      t("pdf_duration", uid, duration=acc["duration"]),
                      _ORANGE)

        # Panel line with purple dot
        if acc.get("panel"):
            _labeled_line(info_x, text_w,
                          t("pdf_panel", uid, panel=acc["panel"]),
                          _PURPLE)

        # Subscription line with teal dot
        if acc.get("sub_link"):
            y = pdf.get_y()
            _dot(info_x, y, _TEAL)
            pdf.set_xy(info_x + 5, y)
            _set_font("", 9)
            pdf.set_text_color(*_ACCENT)
            pdf.cell(text_w - 5, 6, _text(t("pdf_subscription", uid, link=acc["sub_link"])),
                     new_x="LMARGIN", new_y="NEXT")

        # QR code on the right side
        if qr_img:
            qr_x = card_x + card_w - qr_size - 8
            qr_y = start_y + 7

            qr_img.seek(0)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(qr_img.read())
                tmp_path = tmp.name
            tmp_files.append(tmp_path)

            pdf.set_fill_color(255, 255, 255)
            pdf.rect(qr_x - 2, qr_y - 2, qr_size + 4, qr_size + 4, "F")
            pdf.image(tmp_path, x=qr_x, y=qr_y, w=qr_size, h=qr_size)

        # Proxy link at the bottom of the card
        if proxy_link:
            link_y = max(pdf.get_y() + 2, start_y + (qr_size + 14 if qr_img else 0))
            pdf.set_xy(info_x, link_y)
            _set_font("", 7)
            pdf.set_text_color(*_TEXT_MUTED)
            pdf.multi_cell(card_w - 16, 4, _text(proxy_link))
            pdf.set_text_color(*_TEXT_PRIMARY)

        pdf.set_y(start_y + card_h + 5)

    # First page
    pdf.add_page()
    _draw_header()

    for i, acc in enumerate(accounts):
        _draw_card(acc, i)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    buf.name = f"accounts_{stamp}.pdf"

    for f in tmp_files:
        try:
            os.unlink(f)
        except OSError:
            pass

    return buf


def generate_single_account_pdf(
    email: str,
    proxy_link: str,
    qr_image: io.BytesIO | None,
    traffic: str,
    duration: str,
    sub_link: str | None = None,
    uid: int = 0,
) -> io.BytesIO:
    """Convenience wrapper to generate a PDF for a single account."""
    return generate_account_pdf(
        [
            {
                "email": email,
                "proxy_link": proxy_link,
                "qr_image": qr_image,
                "traffic": traffic,
                "duration": duration,
                "sub_link": sub_link,
            }
        ],
        title=t("pdf_account_title", uid, email=email),
        uid=uid,
    )
